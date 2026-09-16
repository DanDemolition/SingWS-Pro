// SingWS Pro vocal effects helper (Prompt 11, VFX0/VFX1). Separate process.
//
//   SingWSVocalFX --list-devices
//   SingWSVocalFX --device UID --channels 1,2 [--frames 128] [--effect none|reverb]
//
// Wet-only: the dry voice stays in the mixer and never passes through here.
// Starts BYPASSED and silent; the host must explicitly enable it.
//
// Protocol (stdout, JSON lines):
//   {"type":"hello","protocol":1,"device":"UID","sample_rate":48000,"frames":128,
//    "effect":"reverb","estimated_latency_ms":8.4,"channels":[1,2]}
//   {"type":"stats","frames":...,"clipped":...,"overruns":...,"peak_db":-12.0,"silent":false,"rss_mb":18.2}
//   {"type":"device_lost"} | {"type":"format_changed"} | {"type":"error","message":"..."}
// Control (stdin, JSON lines):
//   {"type":"params","wet":0.8,"enabled":true}
//   {"type":"bypass"}
// Exits on stdin EOF, device loss or format change; the app decides about restarting.
//
// The render callback is in vfx_engine.c, in C. Swift never runs in it: ARC can
// retain/release, allocate and lock on any object touch.

import CoreAudio
import Foundation

let protocolVersion = 1
let outQueue = DispatchQueue(label: "singws.vfx.out")

func emit(_ obj: [String: Any]) {
    outQueue.async {
        if let d = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
           let line = String(data: d, encoding: .utf8) {
            FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
        }
    }
}

func finish(_ obj: [String: Any], code: Int32) -> Never {
    emit(obj); outQueue.sync {}; exit(code)
}

func rssMegabytes() -> Double {
    var info = mach_task_basic_info()
    var count = mach_msg_type_number_t(MemoryLayout<mach_task_basic_info>.size) / 4
    let kr = withUnsafeMutablePointer(to: &info) {
        $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
            task_info(mach_task_self_, task_flavor_t(MACH_TASK_BASIC_INFO), $0, &count)
        }
    }
    return kr == KERN_SUCCESS ? Double(info.resident_size) / 1_048_576.0 : -1
}

func addr(_ s: AudioObjectPropertySelector,
          _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: s, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}

func listDevices() {
    var a = addr(kAudioHardwarePropertyDevices)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size) == noErr else {
        finish(["type": "error", "message": "cannot enumerate devices"], code: 2)
    }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size, &ids)

    var out: [[String: Any]] = []
    for id in ids {
        var ua = addr(kAudioDevicePropertyDeviceUID)
        var uid: CFString? = nil
        var s = UInt32(MemoryLayout<CFString?>.size)
        guard AudioObjectGetPropertyData(id, &ua, 0, nil, &s, &uid) == noErr, let uid else { continue }

        var na = addr(kAudioObjectPropertyName)
        var name: CFString? = nil
        s = UInt32(MemoryLayout<CFString?>.size)
        AudioObjectGetPropertyData(id, &na, 0, nil, &s, &name)

        func channels(_ scope: AudioObjectPropertyScope) -> Int {
            var sa = addr(kAudioDevicePropertyStreamConfiguration, scope)
            var bytes: UInt32 = 0
            guard AudioObjectGetPropertyDataSize(id, &sa, 0, nil, &bytes) == noErr, bytes > 0 else { return 0 }
            let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(bytes), alignment: 16)
            defer { raw.deallocate() }
            guard AudioObjectGetPropertyData(id, &sa, 0, nil, &bytes, raw) == noErr else { return 0 }
            let abl = UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to: AudioBufferList.self))
            return abl.reduce(0) { $0 + Int($1.mNumberChannels) }
        }
        let ins = channels(kAudioDevicePropertyScopeInput)
        let outs = channels(kAudioDevicePropertyScopeOutput)
        if ins == 0 && outs == 0 { continue }
        out.append(["uid": uid as String, "name": (name as String?) ?? "",
                    "input_channels": ins, "output_channels": outs])
    }
    finish(["type": "devices", "protocol": protocolVersion, "devices": out], code: 0)
}

// ---- arguments -------------------------------------------------------------

var deviceUID = ""
var channels: [Int] = []
var frames: Int32 = 128
var effect = "none"

var args = Array(CommandLine.arguments.dropFirst())
if args.first == "--list-devices" { listDevices() }
while let a = args.first {
    args.removeFirst()
    switch a {
    case "--device":   deviceUID = args.isEmpty ? "" : args.removeFirst()
    case "--channels": channels = (args.isEmpty ? "" : args.removeFirst())
                          .split(separator: ",").compactMap { Int($0.trimmingCharacters(in: .whitespaces)) }
    case "--frames":   frames = Int32(args.isEmpty ? "128" : args.removeFirst()) ?? 128
    case "--effect":   effect = args.isEmpty ? "none" : args.removeFirst()
    default:
        finish(["type": "error",
                "message": "usage: --device UID --channels 1,2 [--frames N] [--effect none|reverb] | --list-devices"],
               code: 2)
    }
}
if deviceUID.isEmpty {
    finish(["type": "error", "message": "usage: --device UID --channels 1,2 [--frames N] [--effect none|reverb]"], code: 2)
}

// ---- engine ----------------------------------------------------------------

var errBuf = [CChar](repeating: 0, count: 256)
let useReverb: Int32 = (effect == "reverb") ? 1 : 0
var chans = channels.map { Int32($0) }

let engine: OpaquePointer? = chans.withUnsafeMutableBufferPointer { cb in
    vfx_engine_create(deviceUID, cb.baseAddress, Int32(cb.count), frames, useReverb, &errBuf, 256)
}
guard let engine else {
    finish(["type": "error", "message": String(cString: errBuf)], code: 3)
}

emit(["type": "hello", "protocol": protocolVersion, "device": deviceUID,
      "sample_rate": vfx_engine_sample_rate(engine),
      "frames": Int(vfx_engine_frames(engine)),
      "effect": effect,
      "estimated_latency_ms": vfx_engine_estimated_latency_ms(engine),
      "channels": channels])

if vfx_engine_start(engine, &errBuf, 256) == 0 {
    vfx_engine_destroy(engine)
    finish(["type": "error", "message": String(cString: errBuf)], code: 4)
}

func shutdown(_ obj: [String: Any], _ code: Int32) -> Never {
    vfx_engine_stop(engine); vfx_engine_destroy(engine); finish(obj, code: code)
}

// Control thread. Parameter changes are published as an atomic snapshot inside
// the engine; nothing here can block the callback.
DispatchQueue.global().async {
    while let line = readLine(strippingNewline: true) {
        guard let d = line.data(using: .utf8),
              let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { continue }
        switch o["type"] as? String {
        case "params":
            let wet = (o["wet"] as? Double) ?? 1.0
            let on = (o["enabled"] as? Bool) ?? false
            vfx_engine_publish(engine, Float(wet), on ? 1 : 0)
        case "bypass":
            vfx_engine_publish(engine, 1.0, 0)
        default: break
        }
    }
    // stdin EOF: the app is gone. Fade is not waited for; the process is ending.
    shutdown(["type": "stopped", "reason": "stdin_eof"], 0)
}

// Stats / supervision loop.
while true {
    Thread.sleep(forTimeInterval: 0.5)
    if vfx_engine_device_ok(engine) == 0 { shutdown(["type": "device_lost"], 0) }
    if vfx_engine_format_changed(engine) != 0 { shutdown(["type": "format_changed"], 0) }

    var f: UInt64 = 0, c: UInt64 = 0, o: UInt64 = 0
    var peak: Int32 = 0, silent: Int32 = 0
    vfx_engine_stats(engine, &f, &c, &o, &peak, &silent)
    emit(["type": "stats", "frames": f, "clipped": c, "overruns": o,
          "peak_db": Double(peak) / 1000.0, "silent": silent != 0,
          "rss_mb": rssMegabytes()])
}
