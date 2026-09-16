# Experimental: SoundAnalysis feasibility spike (Prompt 5)

**Not part of the SingWS Pro app.** Nothing here is imported by `0.2.18.1.py`
or bundled by `SingWS-arm64.spec`. It exists to answer one question with
measurements: can Apple's built-in SoundAnalysis classifier tell singing,
speech, music, applause and silence apart cheaply enough for SingWS Pro?

## Pieces

| File | What it is |
|---|---|
| `SingWSSoundProbe.swift` | Command-line helper. Runs Apple's built-in classifier (`SNClassifySoundRequest(classifierIdentifier: .version1)`) on an audio file or on raw PCM from stdin; prints one JSON line per analysis window. |
| `build.sh` | Builds the helper with `swiftc` for arm64, macOS 15. |
| `../../sound_classes.py` | Pure mapping from Apple labels to SingWS classes (active_vocal, speech, music, applause_crowd, silence, uncertain). Unit-tested. |
| `probe_client.py` | Python side: runs the helper as a child process with a timeout, bounded line reading, and crash handling; maps results with `sound_classes`. |
| `benchmark.py` | Runs the helper over a folder of test clips, records cold start, per-window latency, CPU and memory, and writes a JSON report. |

## Run (on the Mac, after everything is built)

```bash
cd ~/Documents/SingWSPro/experimental/sound_analysis
./build.sh
./SingWSSoundProbe --list-labels > labels.txt          # confirm real label names
./SingWSSoundProbe --file /path/to/song.mp3 | head      # one JSON line per window
../../qtvenv/bin/python benchmark.py --clips /path/to/test_clips --out report.json
```

Test clips to collect (short, 10–30 s each): silence, a karaoke backing track,
singing over backing, speech only (host talking), applause/cheering, a noisy room.
Put the results into `docs/intelligent_audio/MODEL_EVALUATION.md`.
