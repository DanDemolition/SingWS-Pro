# Sound classification feasibility (Prompt 5)

Status: **spike built, measurements pending.** No measurement below has been
run on real hardware yet. Decision: **No-Go until the tables are filled in**
(the roadmap's rule: assume No-Go when evidence is incomplete).

Target: Apple Silicon, macOS 15+. Budget floor: base M1.

## Options, in evaluation order

| # | Option | Bundle cost | Notes |
|---|---|---|---|
| 1 | **Apple SoundAnalysis built-in classifier** (`SNClassifySoundRequest(.version1)`) in a Swift helper process | ~0 (system framework; helper binary < 1 MB) | Preferred. Runs where the system chooses (Neural Engine/CPU). Crash isolated from playback. |
| 2 | SoundAnalysis via PyObjC inside a worker process | PyObjC SoundAnalysis bindings | Only if the helper IPC proves awkward. Same classifier. |
| 3 | YAMNet-derived model converted to Core ML | ~4–15 MB model | Only if option 1's labels are unusable. Needs license/provenance review. |
| 4 | ONNX Runtime CPU | ~30+ MB runtime | Last resort. |
| — | Deterministic features (RMS, spectral flux, zero-crossing) | 0 | Always kept as the fallback and for silence/activity. Essentia rejected (AGPL, heavy). |

## What was built (experimental, not loaded by the app or bundled)

- `experimental/sound_analysis/SingWSSoundProbe.swift` + `build.sh`: CLI helper; `--list-labels`, `--file PATH`, `--stdin` (float32 mono PCM). One JSON line per window with the top 5 labels and per-window latency; a summary line with cold start, average and max latency, and peak RSS.
- `experimental/sound_analysis/probe_client.py`: child process with timeout, bounded output, crash and malformed-line handling.
- `experimental/sound_analysis/benchmark.py`: labelled-clip benchmark → `report.json` with confusion matrix and timings.
- `sound_classes.py` (repo root, pure): maps Apple labels → `active_vocal`, `speech`, `music`, `applause_crowd`, `silence`, `uncertain`. One window is never a decision. Unknown labels contribute nothing. "Singing over backing" resolves to `active_vocal`. Tests: `test_sound_classes.py`.

**Confirm on the Mac:** exact label names (`--list-labels`). The mapping uses exact names first, then whole-word keywords, and must be corrected if Apple's names differ.

## Proposed budgets (base M1)

| Metric | Budget | Measured |
|---|---|---|
| Helper CPU, average during analysis | ≤ 5 % of one core | pending |
| Helper peak memory (RSS) | ≤ 150 MB | pending |
| Per-window processing, average / max | ≤ 20 ms / ≤ 100 ms | pending |
| Cold start (helper launch → first result) | ≤ 1.5 s, and never on the song-start path | pending |
| Queue depth between app and helper | ≤ 4 windows; drop oldest | design |
| App startup impact | 0 (helper starts lazily, off the GUI thread) | design |
| Effect on playback (underruns, GUI tick) | none measurable | pending |

## Accuracy targets (window majority per labelled clip)

| Clip type | Target | Measured |
|---|---|---|
| Silence → `silence` | ≥ 95 % | pending |
| Backing track only → `music` (false `active_vocal`) | ≥ 85 % (≤ 10 %) | pending |
| Singing over backing → `active_vocal` | ≥ 85 % | pending |
| Host speech → `speech` | ≥ 85 % | pending |
| Applause/cheering → `applause_crowd` | ≥ 80 % | pending |
| Noisy room | report only; must mostly be `uncertain`, not confident vocal | pending |

## How to run (after the full build)

```bash
cd ~/Documents/SingWSPro/experimental/sound_analysis
./build.sh
./SingWSSoundProbe --list-labels > labels.txt
../../qtvenv/bin/python benchmark.py --clips ~/SingWS-test-clips --out report.json
```

Clip folders are named by expected class (`silence/`, `music/`, `active_vocal/`, `speech/`, `applause_crowd/`, `noisy_room/`). Use only audio you're allowed to use. Live-mic recordings only with consent, kept as explicit fixtures.

## Go / No-Go rule

**Go for Prompt 6 (observer integration)** only if all of these hold:

- all budget rows are measured on real Apple Silicon and within budget;
- accuracy targets are met, or the misses are only on classes the observer doesn't use yet;
- the helper builds, signs and notarizes inside `SingWS Pro.app`;
- a helper crash or timeout leaves playback untouched (probe_client behavior confirmed).

Otherwise fall back to option 2 or 3, or to deterministic features only.

## Packaging notes (for Prompt 6)

- Helper goes in `SingWS Pro.app/Contents/MacOS/` (or `Helpers/`), signed with the same identity and hardened runtime; no extra entitlements expected for file/stdin analysis.
- Live microphone input later (Prompt 7) needs `NSMicrophoneUsageDescription` and the audio-input entitlement.
- No network, no model download, nothing written to disk.
