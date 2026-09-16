// SingWS Pro — SoundAnalysis feasibility probe (experimental, not shipped).
//
//   SingWSSoundProbe --list-labels
//   SingWSSoundProbe --file PATH [--window 1.0] [--overlap 0.5]
//   SingWSSoundProbe --stdin [--rate 16000] [--window 1.0] [--overlap 0.5]
//       (stdin: raw little-endian float32 mono PCM)
//
// Output: one JSON object per line.
//   {"type":"window","t":12.5,"dur":1.0,"top":[["singing",0.82],["music",0.61],...],"latency_ms":3.1}
//   {"type":"summary","windows":N,"cold_start_ms":...,"avg_latency_ms":...,"max_latency_ms":...,"peak_rss_mb":...}
//   {"type":"error","message":"..."}
//
// Latency is wall time between consecutive result callbacks, measured in the
// helper; it is a bound on per-window processing, not end-to-end audio latency.

import AVFoundation
import Foundation
import SoundAnalysis

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
       let line = String(data: data, encoding: .utf8) {
        FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
    }
}

func peakRSSMegabytes() -> Double {
    var usage = rusage()
    getrusage(RUSAGE_SELF, &usage)
    return Double(usage.ru_maxrss) / (1024.0 * 1024.0)  // macOS reports bytes
}

final class Observer: NSObject, SNResultsObserving {
    var windows = 0
    var latencies: [Double] = []
    var last = Date()
    let topN = 5

    func request(_ request: SNRequest, didProduce result: SNResult) {
        guard let result = result as? SNClassificationResult else { return }
        let now = Date()
        let latency = now.timeIntervalSince(last) * 1000.0
        last = now
        windows += 1
        latencies.append(latency)
        let top = result.classifications.prefix(topN).map { [$0.identifier, Double($0.confidence)] as [Any] }
        emit([
            "type": "window",
            "t": result.timeRange.start.seconds,
            "dur": result.timeRange.duration.seconds,
            "top": top,
            "latency_ms": latency,
        ])
    }

    func request(_ request: SNRequest, didFailWithError error: Error) {
        emit(["type": "error", "message": error.localizedDescription])
    }

    func requestDidComplete(_ request: SNRequest) {}
}

func makeRequest(window: Double, overlap: Double) throws -> SNClassifySoundRequest {
    let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
    request.windowDuration = CMTime(seconds: window, preferredTimescale: 48_000)
    request.overlapFactor = overlap
    return request
}

let args = CommandLine.arguments
func value(_ flag: String, _ fallback: String) -> String {
    if let i = args.firstIndex(of: flag), i + 1 < args.count { return args[i + 1] }
    return fallback
}
let window = Double(value("--window", "1.0")) ?? 1.0
let overlap = Double(value("--overlap", "0.5")) ?? 0.5

do {
    if args.contains("--list-labels") {
        let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
        for label in request.knownClassifications.sorted() { print(label) }
        exit(0)
    }

    let started = Date()
    let observer = Observer()

    if args.contains("--file") {
        let url = URL(fileURLWithPath: value("--file", ""))
        let analyzer = try SNAudioFileAnalyzer(url: url)
        let request = try makeRequest(window: window, overlap: overlap)
        _ = try analyzer.add(request, withObserver: observer)
        observer.last = Date()
        let coldStart = observer.last.timeIntervalSince(started) * 1000.0
        analyzer.analyze()
        let lat = observer.latencies
        emit([
            "type": "summary", "windows": observer.windows, "cold_start_ms": coldStart,
            "avg_latency_ms": lat.isEmpty ? 0 : lat.reduce(0, +) / Double(lat.count),
            "max_latency_ms": lat.max() ?? 0, "peak_rss_mb": peakRSSMegabytes(),
        ])
    } else if args.contains("--stdin") {
        let rate = Double(value("--rate", "16000")) ?? 16000
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate,
                                         channels: 1, interleaved: false) else {
            emit(["type": "error", "message": "bad format"]); exit(2)
        }
        let analyzer = SNAudioStreamAnalyzer(format: format)
        let request = try makeRequest(window: window, overlap: overlap)
        try analyzer.add(request, withObserver: observer)
        observer.last = Date()
        let chunkFrames = AVAudioFrameCount(rate / 10)   // 100 ms blocks
        var framePosition: AVAudioFramePosition = 0
        let stdin = FileHandle.standardInput
        while true {
            let data = stdin.readData(ofLength: Int(chunkFrames) * 4)
            if data.isEmpty { break }
            let frames = AVAudioFrameCount(data.count / 4)
            guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames) else { break }
            buffer.frameLength = frames
            data.withUnsafeBytes { raw in
                if let src = raw.baseAddress?.assumingMemoryBound(to: Float.self),
                   let dst = buffer.floatChannelData?[0] {
                    dst.update(from: src, count: Int(frames))
                }
            }
            analyzer.analyze(buffer, atAudioFramePosition: framePosition)
            framePosition += AVAudioFramePosition(frames)
        }
        analyzer.completeAnalysis()
        let lat = observer.latencies
        emit([
            "type": "summary", "windows": observer.windows,
            "avg_latency_ms": lat.isEmpty ? 0 : lat.reduce(0, +) / Double(lat.count),
            "max_latency_ms": lat.max() ?? 0, "peak_rss_mb": peakRSSMegabytes(),
        ])
    } else {
        print("usage: SingWSSoundProbe --list-labels | --file PATH | --stdin [--rate 16000]")
        exit(2)
    }
} catch {
    emit(["type": "error", "message": error.localizedDescription])
    exit(1)
}
