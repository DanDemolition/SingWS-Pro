# SingWS 2.0 beta — implementation plan

Status: **proposed, not started.** Nothing in this plan has been built.

> **Merged 2026-09-15** with `docs/intelligent_audio/ROADMAP.md` (transitions, live-mic awareness, vocal effects). Where they differ, the roadmap's safety gates and §6 below win. Platform is now **Apple Silicon, macOS 15+ only**; 1.x (0.4.7.x) keeps Intel/older macOS and is maintained **permanently** as the free edition (decided 2026-09-16). Architecture map: `docs/intelligent_audio/ARCHITECTURE.md`.
Drafted 2026-09-15 against `2.0` at `20c4f94` (identical to `main`, APP_VERSION `0.4.7.7`).

## Scope agreed with the operator

- AI-assisted **song transitions** and **digital mixer control**
- **Audio AI**: stem separation, key detection, pitch
- Inference is **local / on-device only** — no network dependency in any show path
- **SingWS Pro is free** (decided 2026-09-16); monetization is online-only features,
  not the app. 1.x remains the free edition permanently alongside it.
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
measured against a base M1 (Apple silicon). The development machine is an
**M1 Max, 10 cores, 32 GB, macOS 27**, with ~175 GB free disk — the real ceiling on
any stems cache.

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

### MS0 — Release plumbing
Version scheme for the beta channel (`2.0.0b1`), settings flag namespace
(`ai_*`), a `docs/2.0/` home, and an update-manifest path that cannot offer a
beta to a 0.4.7.x show machine. Small, entirely non-functional, de-risks the
rest.


**Added 2026-09-15:** SingWS Pro branding — Pro app icon, DMG background and helper art reading "Drag SingWS Pro". Separate identity (`SingWS Pro.app`, `com.singws.pro`, `~/SingWSPro`) and own repo `DanDemolition/SingWS-Pro` are done; see `docs/intelligent_audio/HANDOFF.md`.

### MS1 — Key detection into the analysis pipeline

**Status 2026-09-16: detector built and then MEASURED TO FAIL on real audio.
`key_detect.VALIDATED` is False and `is_usable()` returns False unconditionally,
so nothing can act on a key. MS2, MS4 and MS5 are blocked on fixing this.**

Smaller than planned, because `phrase_detect` already had `_stft_mag()` and
`_chroma()`; MS1 reuses them rather than growing a second spectral stack. No new
dependency. Method is Krumhansl-Kessler profile correlation over an averaged,
per-frame-normalised chroma.

Measured on real library tracks: **0.44 s/track**, so the full 130,824-track
library is about 16 h single-threaded or **~4 h on the existing 4 workers**.

Two findings from real audio:

- **The runner-up is almost always the relative key** (G major 0.801 vs E minor
  0.701; B minor 0.749 vs D major 0.739). Relative keys share all seven pitch
  classes, so chroma cannot separate them and scoring against the runner-up made
  confidence meaningless. Confidence is now measured against the best genuinely
  different key, with `relative_margin` reported separately.
- **Confidence is inherently small** for this method (~0.12-0.15) even with a
  strong `fit` (0.58-0.80). `fit` is the better discriminator of "is this track
  tonal at all".

### Validation, and the negative result

Calibration first looked like it needed a human naming keys by ear. It does not.
`tools/validate_key_detect.py` **pitch-shifts real library tracks by a known
amount and requires the detected tonic to move by exactly that much** — the
shift is the ground truth, so no known-key song list is needed, and it runs on
real mixes rather than synthetic tones. The harness is the durable result of MS1
and any future attempt should be measured with it.

Measured on real audio:

| Property | Result |
|---|---|
| Shift tracking (tonic moves by N) | **4/15** |
| Octave invariance (±12 changes nothing) | **Fails** — G major read as E minor and C major |

The synthetic tests pass, including transposition invariance, so the method
works on clean tones and falls apart on real mixes. Cause: the chroma sums raw
magnitude across the spectrum, so **spectral tilt moves the answer, not pitch
content**. A band-limited, log-compressed, whitened chroma was tried — octave
invariance improved to 5/6 but shift tracking fell to 1/15, so it is not a quick
fix and tuning was stopped rather than continued against a three-track sample.

Options for the next attempt, in rough order of cost: a constant-Q or HPCP-style
chroma with proper octave folding and per-octave normalisation; beat-synchronous
averaging so sustained bass stops dominating; or accepting a vetted open-source
key detector if one exists with a compatible licence. Whichever is chosen, the
bar is the harness above at or near 100%, not a plausible-looking answer.
Add musical key + confidence to the existing per-track analysis, alongside BPM
and loudness. Local DSP (chroma/HPCP + Krumhansl-style profile correlation over
numpy/scipy, which are already dependencies) — no new model runtime, no new
paid or heavyweight dependency. Cached with the existing signature keying and
checkpoint recovery. Feeds MS2, MS4 and MS5.

### MS2 — Harmonic and beat-aware transitions
`transition_analysis.py` currently picks crossfade length from audio/visual
boundaries. Extend it to also use key and beat grid: align the crossfade to the
beat, prefer harmonically compatible neighbours when the background-music
playlist has a choice, and adjust the curve when keys clash. Pure extension of a
well-tested module, no UI required, flag-gated.

### MS3 — Stem separation (revised 2026-09-16)

**Whole-library pre-separation is not viable.** ~130,824 tracks at even 20 MB of
stems each is ~2.6 TB; the dev machine has ~175 GB free. Stems are therefore
produced per song, never as a library batch.

Operator-confirmed use cases are **last-minute request** (song must start within
a second or two with stems active) and **instrumental on demand** (strip a baked-in
guide vocal now). Both decide *before* the song starts — no mid-song stem swap is
required, which removes the hardest case from scope.

Consequences:

- **Two stems suffice** (vocals / accompaniment). Faster and far smaller to cache
  than 4-stem separation.
- **Progressive look-ahead separation**: separate in chunks, begin playback when the
  first chunk is ready, keep the separator ahead of the playhead — like streaming
  transcode. Time-to-first-audio target ~1–2 s on the M1 Max.
- **Fallback is mandatory**: if the separator falls behind the playhead, revert to
  the original audio mid-song rather than glitch. Live-show rule.
- **Playback path is BASS**, not mpv: synchronized multi-track mixing is what the
  existing two-deck `BASSmix` engine already does.
- Three tiers share one engine: pre-separated (queued songs, cached) → progressive
  (no lead time) → cache with a size budget and oldest-first eviction.

Model choice stays a measurement, not a preference: benchmark htdemucs, Open-Unmix
and MDX-Net variants on real karaoke sources (which are unusual — often already
instrumental, sometimes with guide vocals) for quality, speed on this machine, Core ML
conversion viability and licence. All three are MIT.

### MS4 — Pitch, key and tempo surfaced properly
Signalsmith already does the DSP and the transport already exposes it. This
milestone is about making it usable: suggest a key based on MS1 plus the singer's
history, keep pitch and tempo genuinely independent in the UI, preserve per-song
settings, and handle seek/reset cleanly. Mostly UI and state, little new DSP.

### MS5 — Digital mixer control
New module behind a small interface, the way `AGENTS.md` asks for DSP backends.
Blocked on one answer from you: **which mixer, over which protocol** (see open
questions). AI's role here is to propose gain/EQ/effect moves per singer from
their measured history — always as a suggestion the KJ accepts, never an
automatic change to a live board mid-song.

### MS7 — Command registry, hotkeys and Stream Deck (added 2026-09-15)

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
5. **Mixer actions** — Ui24R actions added to the registry through MS5, tested
   against the virtual Ui24R. (Bitfocus Companion also supports Soundcraft Ui
   directly; SingWS actions are for workflows that combine app + mixer.)

Tests: registry unit tests, shortcut conflict tests, API auth/contract tests
with a fake client, plugin tests against a mock SingWS API. No Stream Deck
hardware verification is claimed without a real device.

### MS6 — Host UI overhaul
Last, deliberately. By this point MS1–MS5 have already lifted several controllers
out of `KaraokeApp`, so the overhaul is re-laying-out modules rather than
carving up a 38k-line class while also changing how it behaves. Audience screen
untouched.

## 4. Open questions

1. ~~**Which digital mixer?**~~ **Answered:** Soundcraft **Ui24R** (multichannel USB-B
   audio interface + network control), not yet owned — build against a virtual Ui
   mixer first, and claim no hardware verification until run on a real board.
2. ~~**Stems: which model?**~~ **Deferred, not an operator question.** Decided by
   benchmark at MS3; see that milestone for the revised shape and criteria.
3. ~~**Beta distribution?**~~ **Answered 2026-09-16:** SingWS Pro and 1.x live
   alongside **permanently**. 1.x is the free edition and stays maintained —
   Intel and older macOS indefinitely, not "about a year". Every show-critical fix
   needs an explicit forward-port decision. Both may connect to wskar.com as host,
   with only one actively hosting a show.

### Resolved 2026-09-16 — licensing

**SingWS Pro is open source and free.** Monetization is online-only features,
which the operator has resolved legally and separately; this plan does not treat
the app as a commercial product. That settles most of what follows:

- **GPL (libmpv / IINA).** The obligation is corresponding source of the conveyed
  work to recipients. An open-source app satisfies it by construction. Keep the
  notices correct in the bundle.
- **BASS (proprietary).** `vendor/bass/bass.txt:517` — free for a non-commercial
  entity not making money from the product. A free, open-source app fits that tier.

**One technical issue remains, and it is not about money or price.** A GPL work
(mpv/IINA) linked against a proprietary library (BASS) is incompatible regardless
of what the app itself is licensed as: the operator can grant exceptions for their
own code, but cannot grant them for mpv's. This exists in 1.x today.

**Recommended cleanup, not a blocker:** rebuild libmpv as LGPL (`--enable-lgpl`,
excluding GPL-only components) and link dynamically. That removes the incompatibility
outright. **Sub-task:** verify an LGPL build retains what CDG and MP4 playback need.
Schedule alongside the arm64 framework work; do not let it gate MS0–MS2.

## 5. Recommended next step

Start at **MS1 (key detection)**. It is self-contained, needs no new dependency,
feeds three later milestones, touches `KaraokeApp` barely at all, and is fully
testable without a build. It also answers the question of how much of the
130k-track library can be analysed in reasonable time before any heavier model
work is committed to.


## 6. Merged sequence (2026-09-15)

Each step keeps its own gate from the roadmap. AI coding-agent assignments and prompts are in `docs/intelligent_audio/ROADMAP.md`.

1. **MS0 — Release plumbing + platform floor**: 2.0 versioning, `ai_*`/`ia_*` flags, arm64-only, `LSMinimumSystemVersion` 15.0.
2. **IA Phase 0 — Transition instrumentation** (roadmap Prompt 2).
3. **MS1 — Key detection** (numpy/scipy, into existing analysis pipeline).
4. **IA Phase 1 — Finish deterministic track analysis**: much exists in `transition_analysis.py`; close gaps (CDG lyric API, MP4 tail confidence, level tap).
5. **IA Phase 2 — Observer mode + replay harness** (Prompt 4).
6. **Classifier spike** — SoundAnalysis first (Prompt 5), then observer integration (Prompt 6).
7. **Virtual Soundcraft Ui mixer** — simulator + protocol contract tests; foundation for MS5.
8. **USB mic awareness** (Prompts 7–8) — design against simulated devices; first real target is Ui24R multichannel USB with per-mic channels (Signature 10 Aux workaround becomes secondary).
9. **MS2 / IA Phase 5 — Harmonic, beat-aware transitions.**
10. **IA Assisted mode gate** (Prompt 9).
11. **MS3 — Stems** (per-song, progressive look-ahead, 2-stem, BASS mixing) and **MS4 — Pitch/key/tempo UI.**
12. **Vocal effects** — Swift helper (Prompts 10–11).
13. **MS5 — Digital mixer control** against the virtual mixer, then real Ui hardware.
13a. **MS7 — Command registry, hotkeys, Stream Deck.** Registry + in-app hotkeys can start right after MS0 (they touch little and help every later milestone); the local API and Stream Deck plugin follow; mixer actions land with MS5.
14. **MS6 — Host UI overhaul.**
15. **Final integration audit** (Prompt 12).
