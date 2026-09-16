// SingWS Pro sound helper (Prompt 6). Separate process; a crash here can never
// stop playback. Analysis results are advisory data for the transition
// OBSERVER only.
//
//   SingWSSoundHelper --tap-pid PID [--window 1.0] [--overlap 0.5]
//       Capture the mono mix of process PID with a Core Audio process tap
//       (macOS 14.2+) and classify it with Apple's built-in SoundAnalysis model.
//   SingWSSoundHelper --stdin [--rate 16000]      (float32 mono PCM, for tests)
//   SingWSSoundHelper --list-labels
//
// Protocol (stdout, one JSON object per line):
//   {"type":"hello","model":"apple.soundanalysis.version1","protocol":1,"source":"tap"}
//   {"type":"window","t":12.5,"dur":1.0,"top":[["singing",0.82],...],"proc_ms":3.1}
//   {"type":"heartbeat","windows":42,"dropped":0,"rss_mb":61.2}
//   {"type":"error","message":"..."}        (then exit non-zero)
// The helper exits when stdin reaches EOF, so it never outlives SingWS Pro.
//
// Nothing in this helper records or stores audio.

import AVFoundation
import CoreAudio
import Foundation
import SoundAnalysis

let protocolVersion = 1
let outQueue = DispatchQueue(label: "singws.sound.out")

func emit(_ obj: [String: Any]) {
    outQueue.async {
        if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
           var line = String(data: data, encoding: .utf8) {
            line += "\n"
            FileHandle.standardOutput.write(line.data(using: .utf8)!)
        }
    }
}

func fail(_ message: String, code: Int32 = 1) -> Never {
    emit(["type": "error", "message": message])
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

final class Classifier: NSObject, SNResultsObserving {
    var windows = 0
    var lastResult = Date()

    func request(_ request: SNRequest, didProduce result: SNResult) {
        guard let result = result as? SNClassificationResult else { return }
        let now = Date()
        windows += 1
        let top = result.classifications.prefix(5).map { [$0.identifier, Double($0.confidence)] as [Any] }
        emit(["type": "window", "t": result.timeRange.start.seconds, "dur": result.timeRange.duration.seconds,
              "top": top, "proc_ms": now.timeIntervalSince(lastResult) * 1000.0])
        lastResult = now
    }

    func request(_ request: SNRequest, didFailWithError error: Error) {
        fail("classification failed: \(error.localizedDescription)")
    }
}

func makeRequest(window: Double, overlap: Double) throws -> SNClassifySoundRequest {
    let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
    request.windowDuration = CMTime(seconds: window, preferredTimescale: 48_000)
    request.overlapFactor = overlap
    return request
}

// MARK: Core Audio helpers

func getProperty<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, _ value: inout T,
                    qualifier: UnsafeRawPointer? = nil, qualifierSize: UInt32 = 0) -> OSStatus {
    var address = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                             mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    return withUnsafeMutablePointer(to: &value) {
        AudioObjectGetPropertyData(object, &address, qualifierSize, qualifier, &size, $0)
    }
}

func processObject(for pid: pid_t) -> AudioObjectID? {
    var pidValue = pid
    var object = AudioObjectID(kAudioObjectUnknown)
    let status = withUnsafePointer(to: &pidValue) {
        getProperty(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyTranslatePIDToProcessObject,
                    &object, qualifier: $0, qualifierSize: UInt32(MemoryLayout<pid_t>.size))
    }
    return (status == noErr && object != kAudioObjectUnknown) ? object : nil
}

func defaultOutputUID() -> String? {
    var device = AudioObjectID(kAudioObjectUnknown)
    guard getProperty(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultSystemOutputDevice,
                      &device) == noErr else { return nil }
    var uid: CFString = "" as CFString
    guard getProperty(device, kAudioDevicePropertyDeviceUID, &uid) == noErr else { return nil }
    return uid as String
}

final class ProcessTap {
    var tapID = AudioObjectID(kAudioObjectUnknown)
    var aggregateID = AudioObjectID(kAudioObjectUnknown)
    var procID: AudioDeviceIOProcID?

    func start(pid: pid_t, onBuffer: @escaping (AVAudioPCMBuffer) -> Void) throws -> AVAudioFormat {
        guard let process = processObject(for: pid) else { throw NSError(domain: "tap", code: 1,
            userInfo: [NSLocalizedDescriptionKey: "no Core Audio process object for pid \(pid)"]) }
        let description = CATapDescription(monoMixdownOfProcesses: [process])
        description.uuid = UUID()
        description.isPrivate = true
        description.muteBehavior = .unmuted
        var status = AudioHardwareCreateProcessTap(description, &tapID)
        guard status == noErr else { throw NSError(domain: "tap", code: Int(status),
            userInfo: [NSLocalizedDescriptionKey: "AudioHardwareCreateProcessTap failed (\(status)); audio capture permission may be denied"]) }

        var aggregate: [String: Any] = [
            kAudioAggregateDeviceNameKey: "SingWS Pro Analysis Tap",
            kAudioAggregateDeviceUIDKey: "com.singws.pro.tap.\(UUID().uuidString)",
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: description.uuid.uuidString,
                                               kAudioSubTapDriftCompensationKey: true]],
        ]
        if let output = defaultOutputUID() {
            aggregate[kAudioAggregateDeviceMainSubDeviceKey] = output
            aggregate[kAudioAggregateDeviceSubDeviceListKey] = [[kAudioSubDeviceUIDKey: output]]
        }
        status = AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID)
        guard status == noErr else { throw NSError(domain: "tap", code: Int(status),
            userInfo: [NSLocalizedDescriptionKey: "AudioHardwareCreateAggregateDevice failed (\(status))"]) }

        var asbd = AudioStreamBasicDescription()
        status = getProperty(tapID, kAudioTapPropertyFormat, &asbd)
        guard status == noErr, let format = AVAudioFormat(streamDescription: &asbd) else {
            throw NSError(domain: "tap", code: Int(status), userInfo: [NSLocalizedDescriptionKey: "tap format unavailable"]) }

        let ioQueue = DispatchQueue(label: "singws.sound.io", qos: .utility)
        status = AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, ioQueue) { _, input, _, _, _ in
            // Copy out of the IO buffer; analysis happens on another queue.
            guard let copy = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: input, deallocator: nil),
                  let owned = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: copy.frameLength) else { return }
            owned.frameLength = copy.frameLength
            let channels = Int(format.channelCount)
            if let src = copy.floatChannelData, let dst = owned.floatChannelData {
                for c in 0..<channels { dst[c].update(from: src[c], count: Int(copy.frameLength)) }
            }
            onBuffer(owned)
        }
        guard status == noErr else { throw NSError(domain: "tap", code: Int(status),
            userInfo: [NSLocalizedDescriptionKey: "IOProc create failed (\(status))"]) }
        status = AudioDeviceStart(aggregateID, procID)
        guard status == noErr else { throw NSError(domain: "tap", code: Int(status),
            userInfo: [NSLocalizedDescriptionKey: "AudioDeviceStart failed (\(status))"]) }
        return format
    }

    func stop() {
        if let procID { AudioDeviceStop(aggregateID, procID); AudioDeviceDestroyIOProcID(aggregateID, procID) }
        if aggregateID != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(aggregateID) }
        if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID) }
    }
}

// MARK: main

let args = CommandLine.arguments
func value(_ flag: String, _ fallback: String) -> String {
    if let i = args.firstIndex(of: flag), i + 1 < args.count { return args[i + 1] }
    return fallback
}
let window = Double(value("--window", "1.0")) ?? 1.0
let overlap = Double(value("--overlap", "0.5")) ?? 0.5

if args.contains("--list-labels") {
    do {
        for label in try SNClassifySoundRequest(classifierIdentifier: .version1).knownClassifications.sorted() { print(label) }
        exit(0)
    } catch { fail(error.localizedDescription) }
}

let classifier = Classifier()
let analysisQueue = DispatchQueue(label: "singws.sound.analysis", qos: .utility)
var pending = 0              // buffers queued for analysis (analysisQueue-owned after increment on io queue)
let pendingLock = NSLock()
var dropped = 0
let maxPending = 8           // bounded: drop stale audio instead of building a backlog


let heartbeat = DispatchSource.makeTimerSource(queue: outQueue)
heartbeat.schedule(deadline: .now() + 1, repeating: 1)
heartbeat.setEventHandler {
    pendingLock.lock(); let d = dropped; pendingLock.unlock()
    emit(["type": "heartbeat", "windows": classifier.windows, "dropped": d, "rss_mb": rssMegabytes()])
}
heartbeat.resume()

do {
    if args.contains("--tap-pid") {
        guard let pid = pid_t(value("--tap-pid", "")) else { fail("bad --tap-pid", code: 2) }
        // Parent-death guard (tap mode only; --stdin mode reads audio from stdin):
        // exit when SingWS Pro closes our stdin.
        Thread.detachNewThread {
            while true {
                if FileHandle.standardInput.availableData.isEmpty { outQueue.sync {}; exit(0) }
            }
        }
        let tap = ProcessTap()
        var analyzer: SNAudioStreamAnalyzer?
        var frame: AVAudioFramePosition = 0
        let format = try tap.start(pid: pid) { buffer in
            pendingLock.lock()
            if pending >= maxPending { dropped += 1; pendingLock.unlock(); return }
            pending += 1
            pendingLock.unlock()
            analysisQueue.async {
                analyzer?.analyze(buffer, atAudioFramePosition: frame)
                frame += AVAudioFramePosition(buffer.frameLength)
                pendingLock.lock(); pending -= 1; pendingLock.unlock()
            }
        }
        let streamAnalyzer = SNAudioStreamAnalyzer(format: format)
        try streamAnalyzer.add(try makeRequest(window: window, overlap: overlap), withObserver: classifier)
        analysisQueue.sync { analyzer = streamAnalyzer }
        emit(["type": "hello", "model": "apple.soundanalysis.version1", "protocol": protocolVersion,
              "source": "tap", "sample_rate": format.sampleRate, "channels": format.channelCount])
        signal(SIGTERM) { _ in exit(0) }
        atexit { }  // tap objects are private and released with the process
        _ = tap
        dispatchMain()
    } else if args.contains("--stdin") {
        let rate = Double(value("--rate", "16000")) ?? 16000
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1,
                                         interleaved: false) else { fail("bad format", code: 2) }
        let analyzer = SNAudioStreamAnalyzer(format: format)
        try analyzer.add(try makeRequest(window: window, overlap: overlap), withObserver: classifier)
        emit(["type": "hello", "model": "apple.soundanalysis.version1", "protocol": protocolVersion, "source": "stdin"])
        var position: AVAudioFramePosition = 0
        while true {
            let data = FileHandle.standardInput.readData(ofLength: Int(rate / 10) * 4)
            if data.isEmpty { break }
            let frames = AVAudioFrameCount(data.count / 4)
            guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames) else { break }
            buffer.frameLength = frames
            data.withUnsafeBytes { raw in
                if let src = raw.baseAddress?.assumingMemoryBound(to: Float.self), let dst = buffer.floatChannelData?[0] {
                    dst.update(from: src, count: Int(frames))
                }
            }
            analyzer.analyze(buffer, atAudioFramePosition: position)
            position += AVAudioFramePosition(frames)
        }
        analyzer.completeAnalysis()
        outQueue.sync {}
        exit(0)
    } else {
        fail("usage: SingWSSoundHelper --tap-pid PID | --stdin | --list-labels", code: 2)
    }
} catch {
    fail(error.localizedDescription)
}
