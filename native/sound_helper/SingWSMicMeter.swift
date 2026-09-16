// SingWS Pro mic meter (Prompt 7). Separate process; diagnostic only.
//
//   SingWSMicMeter --list-devices
//   SingWSMicMeter --device UID --channels 1,2,3 [--interval-ms 100]
//
// Reads the chosen INPUT channels of one Core Audio device (e.g. the Ui24R's
// USB multitrack inputs, or a Signature 10's Aux 1/Aux 2 USB pair) and prints
// levels only. It never records, never stores audio, and never outputs audio.
//
// Protocol (stdout, JSON lines):
//   {"type":"hello","protocol":1,"device":"UID","name":"...","sample_rate":48000,"input_channels":32,"channels":[1,2,3]}
//   {"type":"levels","t":12.3,"ch":{"1":{"rms_db":-31.2,"peak_db":-12.0},...}}
//   {"type":"heartbeat","frames":123456,"rss_mb":20.1}
//   {"type":"device_lost"} | {"type":"format_changed","sample_rate":44100} | {"type":"error","message":"..."}
// Exits on stdin EOF, device loss, or format change (the app decides whether to restart).

import CoreAudio
import Foundation

let protocolVersion = 1
let outQueue = DispatchQueue(label: "singws.mic.out")

func emit(_ obj: [String: Any]) {
    outQueue.async {
        if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
           let line = String(data: data, encoding: .utf8) {
            FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
        }
    }
}

func finish(_ obj: [String: Any], code: Int32) -> Never {
    emit(obj)
    outQueue.sync {}
    exit(code)
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

func address(_ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}

func allDevices() -> [AudioObjectID] {
    var addr = address(kAudioHardwarePropertyDevices)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr else { return [] }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

func stringProperty(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String {
    var addr = address(selector)
    var value: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &value) == noErr, let v = value else { return "" }
    return v.takeRetainedValue() as String
}

func inputChannelCount(_ id: AudioObjectID) -> Int {
    var addr = address(kAudioDevicePropertyStreamConfiguration, kAudioObjectPropertyScopeInput)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size) == noErr, size > 0 else { return 0 }
    let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
    defer { raw.deallocate() }
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, raw) == noErr else { return 0 }
    let list = UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to: AudioBufferList.self))
    return list.reduce(0) { $0 + Int($1.mNumberChannels) }
}

func sampleRate(_ id: AudioObjectID) -> Double {
    var addr = address(kAudioDevicePropertyNominalSampleRate)
    var rate: Float64 = 0
    var size = UInt32(MemoryLayout<Float64>.size)
    return AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &rate) == noErr ? rate : 0
}

let args = CommandLine.arguments
func value(_ flag: String, _ fallback: String) -> String {
    if let i = args.firstIndex(of: flag), i + 1 < args.count { return args[i + 1] }
    return fallback
}

if args.contains("--list-devices") {
    var rows: [[String: Any]] = []
    for id in allDevices() {
        let inputs = inputChannelCount(id)
        if inputs == 0 { continue }
        rows.append(["uid": stringProperty(id, kAudioDevicePropertyDeviceUID),
                     "name": stringProperty(id, kAudioObjectPropertyName),
                     "input_channels": inputs, "sample_rate": sampleRate(id)])
    }
    finish(["type": "devices", "devices": rows], code: 0)
}

let wantedUID = value("--device", "")
let channels = value("--channels", "").split(separator: ",").compactMap { Int($0) }.filter { $0 > 0 }
let intervalMs = max(20, Int(value("--interval-ms", "100")) ?? 100)
guard !wantedUID.isEmpty, !channels.isEmpty else {
    finish(["type": "error", "message": "usage: --device UID --channels 1,2,3"], code: 2)
}
guard let device = allDevices().first(where: { stringProperty($0, kAudioDevicePropertyDeviceUID) == wantedUID }) else {
    finish(["type": "device_lost", "reason": "not_found"], code: 3)
}
let totalInputs = inputChannelCount(device)
let rate = sampleRate(device)
if let bad = channels.first(where: { $0 > totalInputs }) {
    finish(["type": "error", "message": "channel \(bad) exceeds \(totalInputs) input channels"], code: 2)
}

// Accumulators (IO queue only) -> snapshot under lock for the reporter.
let lock = NSLock()
var sumSquares = [Double](repeating: 0, count: channels.count)
var peaks = [Float](repeating: 0, count: channels.count)
var counts = [Int](repeating: 0, count: channels.count)
var totalFrames: Int64 = 0
let started = Date()

var procID: AudioDeviceIOProcID?
let ioQueue = DispatchQueue(label: "singws.mic.io", qos: .userInitiated)
var status = AudioDeviceCreateIOProcIDWithBlock(&procID, device, ioQueue) { _, input, _, _, _ in
    let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
    // Map absolute channel number -> (buffer, interleaved index).
    lock.lock()
    defer { lock.unlock() }
    var base = 0
    for buffer in buffers {
        let n = Int(buffer.mNumberChannels)
        guard n > 0, let data = buffer.mData else { base += n; continue }
        let frames = Int(buffer.mDataByteSize) / (MemoryLayout<Float32>.size * n)
        let samples = data.assumingMemoryBound(to: Float32.self)
        for (slot, ch) in channels.enumerated() where ch - 1 >= base && ch - 1 < base + n {
            let offset = ch - 1 - base
            var ss = 0.0
            var pk: Float = 0
            for f in 0..<frames {
                let s = samples[f * n + offset]
                ss += Double(s * s)
                pk = max(pk, abs(s))
            }
            sumSquares[slot] += ss
            peaks[slot] = max(peaks[slot], pk)
            counts[slot] += frames
        }
        if base == 0 { totalFrames += Int64(frames) }
        base += n
    }
}
guard status == noErr else { finish(["type": "error", "message": "IOProc create failed (\(status)); microphone permission may be denied"], code: 1) }

// Device loss and format change listeners.
var aliveAddr = address(kAudioDevicePropertyDeviceIsAlive)
AudioObjectAddPropertyListenerBlock(device, &aliveAddr, outQueue) { _, _ in
    var alive: UInt32 = 1
    var size = UInt32(MemoryLayout<UInt32>.size)
    var a = address(kAudioDevicePropertyDeviceIsAlive)
    if AudioObjectGetPropertyData(device, &a, 0, nil, &size, &alive) != noErr || alive == 0 {
        FileHandle.standardOutput.write("{\"type\":\"device_lost\",\"reason\":\"not_alive\"}\n".data(using: .utf8)!)
        exit(3)
    }
}
var rateAddr = address(kAudioDevicePropertyNominalSampleRate)
AudioObjectAddPropertyListenerBlock(device, &rateAddr, outQueue) { _, _ in
    let newRate = sampleRate(device)
    if newRate != rate {
        FileHandle.standardOutput.write("{\"type\":\"format_changed\",\"sample_rate\":\(newRate)}\n".data(using: .utf8)!)
        exit(4)
    }
}

status = AudioDeviceStart(device, procID)
guard status == noErr else { finish(["type": "error", "message": "AudioDeviceStart failed (\(status))"], code: 1) }

emit(["type": "hello", "protocol": protocolVersion, "device": wantedUID, "name": stringProperty(device, kAudioObjectPropertyName),
      "sample_rate": rate, "input_channels": totalInputs, "channels": channels])

func db(_ x: Double) -> Double { x > 1e-10 ? 20.0 * log10(x) : -120.0 }

let reporter = DispatchSource.makeTimerSource(queue: outQueue)
reporter.schedule(deadline: .now() + .milliseconds(intervalMs), repeating: .milliseconds(intervalMs))
var ticks = 0
reporter.setEventHandler {
    lock.lock()
    var ch: [String: Any] = [:]
    for (slot, number) in channels.enumerated() {
        let rms = counts[slot] > 0 ? (sumSquares[slot] / Double(counts[slot])).squareRoot() : 0
        ch[String(number)] = ["rms_db": (db(rms) * 10).rounded() / 10, "peak_db": (db(Double(peaks[slot])) * 10).rounded() / 10]
        sumSquares[slot] = 0; peaks[slot] = 0; counts[slot] = 0
    }
    let frames = totalFrames
    lock.unlock()
    if let data = try? JSONSerialization.data(withJSONObject: ["type": "levels", "t": Date().timeIntervalSince(started), "ch": ch], options: [.sortedKeys]),
       let line = String(data: data, encoding: .utf8) {
        FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
    }
    ticks += 1
    if ticks % max(1, 1000 / intervalMs) == 0 {
        if let data = try? JSONSerialization.data(withJSONObject: ["type": "heartbeat", "frames": frames, "rss_mb": rssMegabytes()]),
           let line = String(data: data, encoding: .utf8) {
            FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
        }
    }
}
reporter.resume()

// Exit when SingWS Pro closes stdin.
Thread.detachNewThread {
    while true {
        if FileHandle.standardInput.availableData.isEmpty {
            AudioDeviceStop(device, procID)
            exit(0)
        }
    }
}
dispatchMain()
