# SingWS 2.0 beta — implementation plan

Status: **proposed, not started.** Nothing in this plan has been built.

> **Merged 2026-09-15** with `docs/intelligent_audio/ROADMAP.md` (transitions, live-mic awareness, vocal effects). Where they differ, the roadmap's safety gates and §6 below win. Platform is now **Apple Silicon, macOS 15+ only**; 1.x (0.4.7.x) keeps Intel/older macOS with bug fixes for about a year. Architecture map: `docs/intelligent_audio/ARCHITECTURE.md`.
Drafted 2026-09-15 against `2.0` at `20c4f94` (identical to `main`, APP_VERSION `0.4.7.7`).

## Scope agreed with the operator

- AI-assisted **song transitions** and **digital mixer control**
- **Audio AI**: stem separation, key detection, pitch
- Inference is **local / on-device only** — no network dependency in any show path
- **Hotkeys and Elgato Stream Deck control** of app and mixer features (added 2026-09-15)
- **UI overhaul of the host app only**; the audience/rotation screen stays as
  shipped in 0.4.7.1

## 1. Repo findings

### What already exists and should be built on

| Area | Where | State |
|---|---|---|
| Pitch/tempo DSP | `vendor/signalsmith-stretch`, `native/signalsmith_audio_native.cpp` | Vendored, built, exposed to Python as `StretchEngine` (`set_modifiers`, `process_f32le`, `flush_f32le`) |
| Per-song key/tempo | `mpv_karaoke_transport.py:147` `set_modifiers()` | Live. Pitch and tempo are already independent controls, clamped 0.5–2.0 |
| BPM + first beat | `phrase_detect.py`, `_BpmDetectWorker` (`0.2.18.1.py:13418`) | Detected and cached per track |
| Transition analysis | `transition_analysis.py` (877 lines) | Audio boundaries, fade estimation, CDG/MP4 visual endpoints, crossfade selection, signature-keyed cache with JSONL checkpointing |
| Batch analysis pipeline | `AnalyzeLibraryWorker`, `AnalyzeLibraryParallelCoordinator` | Recyclable helper processes, 4-way parallel, cancellable, crash-resumable |
| Audio routing | `bass_background_engine.py`, `singws_master_audio.py`, `singws_eq.py`, `bass_soundboard_engine.py` | BASS mixer, master EQ/compressor/limiter, soundboard channels |
| Host remote control | `HostControlRelayWorker` (`0.2.18.1.py:11484`) | WebSocket v2 DAW relay — the natural transport for external control |

### What does not exist

- **No AI infrastructure of any kind.** No model runtime, no embeddings, no
  vector index, no inference dependency.
- **No key detection.** BPM is detected; musical key is not.
- **No MIDI or OSC.** `grep` for `mido`/`rtmidi`/`OSC`/mixer protocols returns
  nothing. Digital mixer control is entirely new ground.
- **No stem separation.**

### The structural fact that shapes everything

`0.2.18.1.py` is **59,054 lines**. `class KaraokeApp` occupies lines
19632–57777 — roughly **38,000 lines in one class** — and holds playback
control, queue, singer history, library scan, settings, KaraFun automation, the
DAW relay and most of the host UI.

All four workstreams above land in that class. That is the main risk in this
plan and the reason for the sequencing below.

### Hardware constraint (revised)

2.0 is developed and built on an **Apple Silicon** MacBook Pro and ships arm64
only, macOS 15+. The Intel constraint below no longer applies to 2.0:
stem separation and classification can use Core ML / the Neural Engine, though
stems remain offline, cached, prep-time work for show safety. Budgets are
measured against a base M1.

### Mixer hardware (revised)

The target digital mixer is a **Soundcraft Ui24R**, which the operator
**does not own yet**. All mixer-control work is built and tested against a
**virtual Ui mixer**: a local simulator that speaks the same network control
protocol (Ui boards are controlled over the network, not USB audio/MIDI), with
recorded/fixture state, fault injection (disconnect, latency, rejected
commands), and a contract test suite that later runs unchanged against real
hardware. No mixer feature may claim hardware verification until run on a real
board. The Ui24R also provides a multichannel USB audio interface, so the virtual environment needs a simulated multichannel Core Audio device (separate Singer 1, Singer 2, Host channels plus a return pair) alongside the network-control simulator.

## 2. Principles for this release

1. **No show path may depend on inference at runtime.** Every AI result is
   computed ahead of time, cached against a file signature, and read as plain
   data during a show. If the cache misses, behaviour falls back to what
   0.4.7.x does today.
2. **Every feature ships behind a settings flag, default off**, until the
   operator has rehearsed it. `AGENTS.md` live-show rule 10.
3. **Extract seams from `KaraokeApp` only where a feature needs them.** No
   big-bang split. Each milestone that touches the class lifts exactly the panel
   or controller it needs into its own module, with tests, and leaves the rest
   alone.
4. **0.4.7.x stays shippable throughout.** 2.0 work lives on this branch; show
   fixes continue to land on `main` and get merged forward.

## 3. Milestones

Each milestone stops with the `AGENTS.md` report format and is independently
testable. Nothing is built into a `.app` until the operator asks.

### M0 — Release plumbing
Version scheme for the beta channel (`2.0.0b1`), settings flag namespace
(`ai_*`), a `docs/2.0/` home, and an update-manifest path that cannot offer a
beta to a 0.4.7.x show machine. Small, entirely non-functional, de-risks the
rest.


**Added 2026-09-15:** SingWS Pro branding — Pro app icon, DMG background and helper art reading "Drag SingWS Pro". Separate identity (`SingWS Pro.app`, `com.singws.pro`, `~/SingWSPro`) and own repo `DanDemolition/SingWSPro` are done; see `docs/intelligent_audio/HANDOFF.md`.

### M1 — Key detection into the analysis pipeline
Add musical key + confidence to the existing per-track analysis, alongside BPM
and loudness. Local DSP (chroma/HPCP + Krumhansl-style profile correlation over
numpy/scipy, which are already dependencies) — no new model runtime, no new
paid or heavyweight dependency. Cached with the existing signature keying and
checkpoint recovery. Feeds M2, M4 and M5.

### M2 — Harmonic and beat-aware transitions
`transition_analysis.py` currently picks crossfade length from audio/visual
boundaries. Extend it to also use key and beat grid: align the crossfade to the
beat, prefer harmonically compatible neighbours when the background-music
playlist has a choice, and adjust the curve when keys clash. Pure extension of a
well-tested module, no UI required, flag-gated.

### M3 — Offline stem separation
Run a local separation model over library tracks as a batch job on the existing
recyclable-helper pipeline. Stems cached to disk per track. Then a playback path
to mute/solo them — the real payoff being an on-demand **vocal guide track for
any MP3+G**, plus instrumental-only for confident singers. Heaviest milestone;
splits into: model selection and offline benchmark on Intel → batch job and
cache → playback routing → host UI control.

### M4 — Pitch, key and tempo surfaced properly
Signalsmith already does the DSP and the transport already exposes it. This
milestone is about making it usable: suggest a key based on M1 plus the singer's
history, keep pitch and tempo genuinely independent in the UI, preserve per-song
settings, and handle seek/reset cleanly. Mostly UI and state, little new DSP.

### M5 — Digital mixer control
New module behind a small interface, the way `AGENTS.md` asks for DSP backends.
Blocked on one answer from you: **which mixer, over which protocol** (see open
questions). AI's role here is to propose gain/EQ/effect moves per singer from
their measured history — always as a suggestion the KJ accepts, never an
automatic change to a live board mid-song.

### M7 — Command registry, hotkeys and Stream Deck (added 2026-09-15)

One registry of named, typed **actions** (play/pause, skip, next singer,
BGM fade/duck, key ±, tempo ±, transitions mode, emergency bypass, soundboard
pads, and later mixer actions such as mute mic, FX on/off, fader nudge). Every
surface calls the same actions, so behaviour and safety checks live in one place.

1. **Registry** — pure module with action ids, parameters, enabled-state and
   live-show guards (e.g. destructive actions need confirm or long-press). Wraps
   existing `KaraokeApp` methods; no behaviour changes.
2. **Keyboard hotkeys** — in-app shortcuts with a remappable settings page and
   conflict detection. Optional global hotkeys (work when SingWS is not focused)
   need macOS Accessibility/Input Monitoring permission; off by default.
3. **Local control API** — authenticated localhost WebSocket exposing the
   registry (list actions, invoke, subscribe to state for button feedback).
   Reuse the `HostControlRelayWorker` v2 message style where it fits.
4. **Stream Deck plugin** — Elgato Stream Deck SDK plugin (JavaScript/Node)
   that talks to the local API, with live key titles/icons (current singer,
   BGM level, mute state). Zero-integration fallback: Stream Deck "Hotkey"
   actions sending the SingWS keyboard shortcuts.
5. **Mixer actions** — Ui24R actions added to the registry through M5, tested
   against the virtual Ui24R. (Bitfocus Companion also supports Soundcraft Ui
   directly; SingWS actions are for workflows that combine app + mixer.)

Tests: registry unit tests, shortcut conflict tests, API auth/contract tests
with a fake client, plugin tests against a mock SingWS API. No Stream Deck
hardware verification is claimed without a real device.

### M6 — Host UI overhaul
Last, deliberately. By this point M1–M5 have already lifted several controllers
out of `KaraokeApp`, so the overhaul is re-laying-out modules rather than
carving up a 38k-line class while also changing how it behaves. Audience screen
untouched.

## 4. Open questions

1. ~~**Which digital mixer?**~~ **Answered:** Soundcraft Ui series, not yet owned — build against a virtual mixer first. Model: **Ui24R** (multichannel USB-B audio interface + network control), so individual mic channels can reach SingWS over USB with no extra interface. Original question: Behringer X32/Wing, Soundcraft Ui, A&H, a USB
   interface, something else? This decides OSC vs MIDI vs a vendor protocol and
   is the only true blocker in the plan — M5 cannot start without it.
2. **Stems: which model?** Benchmark on Apple Silicon (base M1 floor) with Core ML candidates before committing. I would
   measure candidates on real library tracks in M3 and bring you numbers rather
   than pick blind.
3. **Beta distribution:** separate installer alongside 0.4.7.x, or does the 2.0
   beta replace the show app once you are happy with it?

## 5. Recommended next step

Start at **M1 (key detection)**. It is self-contained, needs no new dependency,
feeds three later milestones, touches `KaraokeApp` barely at all, and is fully
testable without a build. It also answers the question of how much of the
130k-track library can be analysed in reasonable time before any heavier model
work is committed to.


## 6. Merged sequence (2026-09-15)

Each step keeps its own gate from the roadmap. AI coding-agent assignments and prompts are in `docs/intelligent_audio/ROADMAP.md`.

1. **M0 — Release plumbing + platform floor**: 2.0 versioning, `ai_*`/`ia_*` flags, arm64-only, `LSMinimumSystemVersion` 15.0.
2. **IA Phase 0 — Transition instrumentation** (roadmap Prompt 2).
3. **M1 — Key detection** (numpy/scipy, into existing analysis pipeline).
4. **IA Phase 1 — Finish deterministic track analysis**: much exists in `transition_analysis.py`; close gaps (CDG lyric API, MP4 tail confidence, level tap).
5. **IA Phase 2 — Observer mode + replay harness** (Prompt 4).
6. **Classifier spike** — SoundAnalysis first (Prompt 5), then observer integration (Prompt 6).
7. **Virtual Soundcraft Ui mixer** — simulator + protocol contract tests; foundation for M5.
8. **USB mic awareness** (Prompts 7–8) — design against simulated devices; first real target is Ui24R multichannel USB with per-mic channels (Signature 10 Aux workaround becomes secondary).
9. **M2 / IA Phase 5 — Harmonic, beat-aware transitions.**
10. **IA Assisted mode gate** (Prompt 9).
11. **M3 — Offline stems** (Core ML) and **M4 — Pitch/key/tempo UI.**
12. **Vocal effects** — Swift helper (Prompts 10–11).
13. **M5 — Digital mixer control** against the virtual mixer, then real Ui hardware.
13a. **M7 — Command registry, hotkeys, Stream Deck.** Registry + in-app hotkeys can start right after M0 (they touch little and help every later milestone); the local API and Stream Deck plugin follow; mixer actions land with M5.
14. **M6 — Host UI overhaul.**
15. **Final integration audit** (Prompt 12).
