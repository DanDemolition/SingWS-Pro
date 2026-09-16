# SingWS Intelligent Audio and Vocal Effects Roadmap

Prepared for Dan / WildStyle Karaoke  
Date: September 12, 2026

Companion hookup diagram: `SingWS_Mixer_Hookup_Guide.png`

## Purpose

This document preserves the design discussed for gradually adding intelligent music transitions, live microphone awareness, and eventually software vocal effects to SingWS. The overriding goal is reliability during live karaoke. Every feature must work on both Intel and Apple Silicon Macs and must fail safely without interrupting playback.

## Core design decision

SingWS should not use a language model to control audio. It should combine:

- Deterministic playback and transition rules
- Cached analysis performed during library scanning
- Lightweight audio classification in a background worker
- CDG lyric timing and MP4 playback position
- Optional live microphone activity received from a USB mixer
- Per-track host corrections that override automated decisions

The existing audio engine always retains final control. AI output is advisory data and may never run inside the real-time audio callback or PyQt GUI thread.

## Audio inputs

### 1. Internal program audio

SingWS copies a low-resolution analysis feed from the decoded karaoke or BGM audio before it reaches the output device. The copy should be mono, 16 kHz, and processed outside the playback thread.

This feed provides clean information about:

- Audible start and end
- Silence and low-energy sections
- Guide vocals or recorded speech
- Applause embedded in a track
- Tempo, beat positions, key, energy, and loudness
- Fade and outro characteristics

It does not contain the live singer, host, or audience.

### 2. Live USB microphone feed

An optional USB mixer feed allows SingWS to know whether singers or the host are still vocalizing after the backing track or lyrics end.

#### Soundcraft Signature 10

The regular Signature 10 exposes two USB output channels, not individual input channels. Configure it to send Aux 1 and Aux 2 over USB instead of the main stereo mix:

- Aux 1: both singer microphones
- Aux 2: host microphone
- Karaoke/BGM playback channel: Aux 1 and Aux 2 sends fully down
- USB return channel: Aux 1 and Aux 2 sends fully down to prevent a feedback loop

This separates the combined singers from the host, although it cannot separate Singer Mic 1 from Singer Mic 2.

#### Multitrack mixers

On compatible multitrack mixers, SingWS may map separate USB channels for Singer Mic 1, Singer Mic 2, and Host Mic. Channel assignments must be configurable rather than hard-coded.

### 3. Audience input

A dedicated room microphone could eventually provide applause and crowd-energy detection, but it is optional and lower priority. The singer and host feeds are more valuable for preventing badly timed transitions.

## Recommended lightweight model

Use a small YAMNet-derived audio classifier exported to ONNX and executed using ONNX Runtime on the CPU.

- Input: mono 16-kHz audio
- Analysis window: approximately 0.96 seconds
- Update interval: approximately 0.48 seconds
- Intel Mac: ONNX Runtime CPU
- Apple Silicon: ONNX Runtime CPU initially, with optional Core ML acceleration later
- Optional optimization: INT8 quantization after accuracy testing

The SingWS-specific classifier should report probabilities for:

- Active vocal
- Instrumental music
- Speech
- Silence
- Applause/crowd
- Song ending
- Uncertain

YAMNet should not decide transitions by itself. Its output should be combined with signal measurements, file metadata, and lyric state.

Silero VAD may be evaluated for speech detection but should not be the primary singing detector. Heavy models such as Whisper, Demucs, and Spleeter should not run continuously during shows.

## Cached per-track analysis

During library scanning, analyze each track and store a versioned result:

- Audible start
- First meaningful audio
- First lyric timestamp, when available
- Last lyric timestamp, when available
- Last detected vocal
- Audible end
- Safe BGM entry point
- Safe early-end point
- Estimated tempo and beat grid
- Musical key
- Integrated and short-term loudness
- Outro classification
- Confidence score for every derived cue
- Analyzer/model version

Do not force the user to rescan the entire library whenever analysis improves. Records should be lazily upgraded when a song is played, inspected, or included in a background maintenance scan.

## Transition decision examples

### Normal ending

If lyrics have ended, the backing track is ending, and neither singer feed is active, begin BGM at the selected cue and loudness.

### Singer holding a final note

If the lyrics and backing track are ending but a singer microphone remains active, delay BGM or introduce it very gently after a configurable hold period.

### Host speaking

If the host microphone is active, start BGM at a low bed level and apply ducking. Raise it after the host stops.

### Silent dramatic outro

If lyrics recently ended but the cached track analysis marks an intentional outro, preserve it unless the host has created an explicit override.

### Broken or empty ending

If there are no remaining lyrics, audio is effectively silent, and confidence is high, end the track early rather than waiting for the container duration.

### Uncertain result

Use the existing conservative transition behavior. AI uncertainty must never create an aggressive transition.

## Required safety architecture

- Never run inference, file decoding, database writes, logging, or UI work in the real-time audio callback.
- Use a bounded ring buffer to copy analysis audio without blocking playback.
- Run analysis in one low-priority worker process or thread.
- Drop stale analysis frames instead of building a backlog.
- Impose inference timeouts and health monitoring.
- Automatically bypass intelligent behavior if the worker stalls or exceeds its CPU budget.
- Preserve current playback behavior as the fallback.
- Keep all automatic cue points editable per track.
- Provide a global emergency disable control.
- Do not record or permanently store live microphone audio.
- Store only derived activity levels, classifications, timestamps, and diagnostic performance measurements.

## Phased implementation

### Phase 0 — Instrumentation only

Add timestamps and structured logging around the current transition system. Record when lyrics end, when meaningful audio ends, when BGM begins, fade duration, underruns, CPU load, and thread stalls. Make no behavioral changes.

Success condition: useful logs are produced without affecting playback on either Mac architecture.

### Phase 1 — Deterministic offline analysis

Add audible-start/end detection, loudness analysis, CDG last-lyric extraction, and conservative outro rules. Store versioned results in the database. Continue using existing transitions unless observer mode is enabled.

Success condition: cue estimates can be inspected and corrected without controlling a live show.

### Phase 2 — Observer mode

Create an Intelligent Transitions setting with Observer as the default. During shows, calculate and log what the engine would have done, but do not change playback.

Log:

- Proposed action and timestamp
- Actual action taken by the existing system or host
- Inputs that influenced the proposal
- Confidence
- Difference between proposed and actual timing
- CPU time and dropped analysis frames

Success condition: several real shows complete without audio regressions, and proposed decisions can be reviewed afterward.

### Phase 3 — Internal-audio assisted transitions

Permit automatic behavior only on high-confidence cases, initially limited to removing obvious dead air. Keep lyric and intentional-outro protections mandatory.

Success condition: no clipped lyrics or meaningful outros across an agreed test set and several live shows.

### Phase 4 — USB microphone awareness

Add audio-device selection, input mapping, meters, threshold calibration, disconnect detection, and privacy messaging. Begin with activity detection rather than elaborate classification.

Modes:

- Off
- Observe only
- Singer protection
- Singer protection plus host ducking

Success condition: unplugging, changing, or losing the mixer immediately returns the system to internal-audio-only behavior.

### Phase 5 — Adaptive musical transitions

Add beat-aware BGM entry, energy matching, improved crossfades, and program-loudness matching. Treat musical alignment as optional polish, never as a prerequisite for starting audio.

### Phase 6 — Vocal effects foundation

Build a separate real-time effects engine. It must not share its processing path, queue, or timing requirements with transition analysis.

For the Signature 10:

- Aux 1 singer feed enters SingWS
- Aux 2 host feed enters SingWS
- SingWS returns wet-only effects through the stereo USB return
- Dry microphones continue directly through the mixer
- USB return must never be routed back to the source auxiliaries

Initial effects:

- Room reverb
- Slapback karaoke delay
- Tempo-synchronized delay
- Chorus/doubling
- Host-mic BGM ducking
- Telephone/robot/special effects

Pitch correction should come later. Combined singer microphones are unsuitable for reliable duet pitch correction. Separate USB microphone channels are preferred for that feature.

## Settings and controls

Recommended settings page:

- Intelligent Transitions: Off / Observer / Assisted / Automatic
- Analysis CPU limit
- Conservative / Balanced / Responsive behavior
- Preserve silent outros
- Minimum time after final lyric
- Host speaking behavior: Wait / Low BGM bed / Ignore
- Live microphone awareness enable
- Audio input device
- Singer feed channel
- Host feed channel
- Per-input meter and calibration
- Test without affecting playback
- View recent decisions
- Reset a track's learned override
- Emergency bypass

Avoid exposing raw model terminology in the normal host interface. Advanced diagnostics may show probabilities and timings.

## Corrections and learning

Do not allow uncontrolled retraining during a show. When the host corrects a transition, store a deterministic per-track override:

- Keep full outro
- End here
- BGM may begin here
- Never overlap this track
- Custom fade duration

These overrides take priority over model output and survive rescans unless explicitly reset.

Collected corrections may later form a training dataset, but model training should happen offline and produce a tested, versioned model shipped with SingWS.

## Testing when access to hardware is limited

Build a replayable audio-test harness before enabling live control. It should accept prerecorded program audio, optional singer feed, optional host feed, CDG events, and expected decisions. Live microphone recordings should be created only with consent and kept as explicit test fixtures rather than silently collected.

Test categories:

- CDG with normal ending
- CDG with lyrics after an instrumental break
- CDG with silent tail
- CDG with corrupt or missing packets
- MP4 with credits
- MP4 with visual content after audio ends
- Long held final vocal
- Host speaking immediately after a performance
- Duet vocals
- Applause embedded in the karaoke file
- Sudden noise or dropped microphone connection
- USB device removed during playback
- High CPU load
- Worker crash or timeout
- Intel and Apple Silicon packaged builds

Every captured failure should become a permanent automated regression fixture when legally and practically possible.

## Release gates

Do not enable automatic transitions by default until all of the following are true:

- Observer mode has completed multiple full shows on Intel and Apple Silicon.
- No measured audio underruns or UI stalls are attributable to analysis.
- All worker failure and device-disconnect tests fall back cleanly.
- A representative CDG and MP4 regression library passes.
- The host can immediately override or disable the system.
- Analyzer and model versions are stored with cached results.
- Existing behavior remains available through one setting.

## Recommended first development task

Implement Phase 0 and the observer-mode data model before adding YAMNet or USB capture. Specifically:

1. Define the transition state machine and typed input events.
2. Add nonblocking structured decision logging.
3. Extract final CDG lyric timing.
4. Calculate deterministic audible-end candidates.
5. Display proposed transitions without executing them.
6. Create replay tests for known difficult songs.

This provides the architecture and evidence needed to judge whether a model improves decisions. Adding a model first would make failures difficult to reproduce and distinguish from existing timing problems.

## Sequential AI coding prompts

These prompts are designed to be run **in order against the same SingWS branch**. Do not paste the entire set into one session. Complete and review one stage, commit it, and then begin the next. Each model must inspect the repository rather than assuming filenames or architecture.

The recommended division of work is:

- **Claude Opus Light:** architecture discovery, concurrency and safety design, difficult audio-routing design, and final cross-system review.
- **GPT-5.6 Sol Light:** bounded implementation, database migrations, UI work, test fixtures, logging, and mechanical integration.

This is not a competition between the models. Opus should define or audit the dangerous architectural boundaries; Sol should implement narrowly scoped, testable increments. Every handoff must leave repository documentation that the next session can inspect.

### Shared rules for every prompt

Every prompt below incorporates these requirements, but keep this checklist available while reviewing the work:

- Preserve all existing SingWS behavior unless the current phase explicitly changes it.
- Intel macOS and Apple Silicon macOS are equally required targets.
- Never block the playback callback, decoder callback, or PyQt GUI thread.
- Never add unbounded queues or allow stale analysis work to accumulate.
- No automatic transition control before its designated phase.
- No microphone recording or permanent raw microphone storage.
- New behavior must have an immediate fallback to the current behavior.
- Add focused tests for every new state transition and failure mode.
- Do not perform unrelated refactors or dependency upgrades.
- Do not claim hardware behavior was tested when it was only simulated.
- End every session with a concise handoff file at `docs/intelligent_audio/HANDOFF.md`, updating rather than duplicating it.

### Prompt 1 — Repository architecture and safety map

**Recommended model: Claude Opus Light**  
**Purpose: investigation and design only; no production behavior changes.**

```text
You are working in the SingWS repository. We are beginning a multi-stage Intelligent Audio project for reliable karaoke-to-BGM transitions and later optional vocal effects. Intel macOS support is mandatory alongside Apple Silicon. This session is architecture discovery only.

Inspect the complete paths involved in:
- karaoke and BGM file decoding
- CDG packet/lyric timing
- MP4 playback timing
- audio output and device selection
- transition/crossfade decisions
- library scanning and cached metadata
- database schema and migrations
- PyQt signals, QThreads, worker processes, timers, and shutdown
- logging, crash reporting, tests, and packaging for both Mac architectures

Produce `docs/intelligent_audio/ARCHITECTURE.md` containing:
1. A precise current-state data-flow description with real symbols and file paths.
2. The exact callbacks/threads that must never be blocked.
3. Recommended insertion points for a bounded analysis tap, transition observer, cached track analysis, and optional USB inputs.
4. Existing code that can be reused.
5. Risks specific to Intel performance, device switching, shutdown, and packaged builds.
6. A proposed typed event/state model without implementing it.
7. A dependency recommendation comparing ONNX Runtime, Essentia, and existing dependencies, including packaging impact.
8. A staged file-level implementation plan with rollback points.

Do not add AI dependencies, change playback behavior, or refactor unrelated code. You may add documentation-only diagrams if useful. Run existing relevant tests to establish a baseline and record commands and results honestly.

Update `docs/intelligent_audio/HANDOFF.md` with discoveries, open questions, baseline test results, and the recommended next task. Return a concise summary of files inspected/created and any blockers.
```

**Gate:** A human or later review session must confirm that the document names actual repository symbols and identifies the genuine audio-critical threads.

### Prompt 2 — Transition instrumentation and typed observer events

**Recommended model: GPT-5.6 Sol Light**  
**Purpose: implement Phase 0 without altering transitions.**

```text
Implement Phase 0 of the SingWS Intelligent Audio roadmap. First read:
- `docs/intelligent_audio/ARCHITECTURE.md`
- `docs/intelligent_audio/HANDOFF.md`
- the actual current playback, CDG, MP4, BGM, logging, and test code

Add a small typed event model and nonblocking structured instrumentation for existing transitions. Capture, where the application already knows them:
- track identity and media type
- playback start and reported duration
- final known CDG lyric timestamp/event
- current playback position
- karaoke audio end/stop
- BGM start request and actual start
- fade start/end and configured duration
- manual skip/stop/seek
- decoder or output underrun indicators already available
- worker/thread timing warnings already available

Requirements:
- Do not change any transition decision or playback timing.
- Do not add an AI model or microphone capture.
- Logging must never perform blocking I/O on an audio callback or GUI-critical path.
- Use a bounded queue or the repository's safe logging mechanism; define an explicit overflow/drop policy.
- Avoid logging raw audio or sensitive singer information beyond what current logs already allow.
- Add a feature flag if needed, defaulting to safe/off unless ordinary diagnostics are already always enabled.
- Add unit tests for event serialization, ordering assumptions, overflow behavior, and clean shutdown.
- Preserve compatibility with old databases/configuration.

Run focused tests plus the existing relevant suite. Clearly separate tests actually run from tests requiring Intel, Apple Silicon, a mixer, or a live show.

Update `docs/intelligent_audio/HANDOFF.md` with exact files changed, event schema, test commands/results, remaining hardware tests, and rollback instructions. Do not begin Phase 1.
```

**Gate:** Existing transition timing must be byte-for-byte or behaviorally unchanged, with instrumentation disabled or passive.

### Prompt 3 — Deterministic track analysis and versioned cache

**Recommended model: GPT-5.6 Sol Light**  
**Purpose: implement Phase 1 without model inference.**

```text
Implement Phase 1 of the SingWS Intelligent Audio roadmap. Read all files in `docs/intelligent_audio/`, the current scanner, duration/loudness analysis, CDG parser, MP4 metadata handling, database migrations, and related tests.

Add deterministic, versioned per-track transition analysis using existing dependencies where practical. Calculate and cache:
- audible start candidate
- audible end candidate
- final CDG lyric timestamp when available
- duration and media type
- silence/energy envelope near the beginning and ending
- conservative safe-BGM-entry candidate
- conservative safe-early-end candidate
- confidence/reason codes
- analyzer version and analysis timestamp

Requirements:
- Do not add YAMNet or any ML runtime yet.
- Reuse prior scan results and existing loudness data when valid.
- Do not require a destructive full-library rescan.
- Implement lazy/versioned upgrading so older records remain valid and are refreshed gradually.
- Bound memory and CPU usage, especially on Intel.
- Never analyze synchronously on the GUI or playback-critical thread.
- Treat CDG lyrics as stronger evidence than silence alone.
- Never automatically execute the proposed cue in this phase.
- Add migrations that are backward compatible and tested against a copied/temporary database.
- Add fixtures covering normal endings, silent tails, instrumental breaks, absent lyrics, corrupt metadata, and very short tracks.

Expose results only through diagnostics or an internal/test interface; do not clutter the normal host UI yet. Run focused and regression tests.

Update `docs/intelligent_audio/HANDOFF.md` with schema changes, analyzer versioning policy, performance measurements, test results, untested hardware cases, and rollback instructions. Do not implement automatic transitions.
```

**Gate:** Old libraries open normally, rescanning is not mandatory, and analysis cannot interrupt playback.

### Prompt 4 — Observer mode and replay harness

**Recommended model: GPT-5.6 Sol Light**  
**Purpose: safely compare proposed decisions with real behavior.**

```text
Implement Phase 2: Intelligent Transition Observer Mode. Read `docs/intelligent_audio/`, all changes from prior phases, current transition settings, and current test infrastructure.

Add settings with these initial modes:
- Off
- Observer

Observer mode must calculate what an assisted transition would propose but must never execute, schedule, cancel, seek, fade, stop, or start playback. Log:
- proposed action and monotonic timestamp
- evidence and reason codes
- confidence
- current lyric state
- cached audible-end and outro data
- actual existing-system/host action when it occurs
- timing difference between proposed and actual action
- analysis latency, queue drops, and worker health

Build a deterministic replay harness that can feed prerecorded or synthetic program-audio envelopes, CDG events, playback events, and expected decisions without requiring real audio hardware. Prefer small generated/synthetic fixtures unless repository-owned media is already licensed for testing.

Requirements:
- Observer is the default if the feature is exposed during development; Automatic must not exist yet.
- Use monotonic clocks for interval decisions.
- Make logs easy to export with existing diagnostic bundles.
- Bound retained history and avoid raw live audio storage.
- Add tests proving Observer cannot reach playback-control methods, including mocks/spies around start, stop, seek, and fade commands.
- Add worker crash, queue overflow, shutdown, and stale-event tests.

Run all relevant tests and document what still needs live-show observation on both Mac architectures.

Update `docs/intelligent_audio/HANDOFF.md` with usage instructions, log interpretation, test commands/results, hardware gaps, and rollback instructions. Do not add ML or USB capture.
```

**Gate:** Automated tests must prove observer decisions have no command path to playback.

### Prompt 5 — Lightweight audio model feasibility spike

**Recommended model: Claude Opus Light**  
**Purpose: prove packaging and performance before product integration.**

```text
Perform a bounded feasibility spike for lightweight audio classification in SingWS. Read all `docs/intelligent_audio/` material and inspect the build/package configuration for Intel x86_64 and Apple Silicon arm64.

Evaluate a YAMNet-derived classifier with ONNX Runtime CPU as the baseline. Do not wire it into live playback. Create an isolated prototype/benchmark that accepts mono 16-kHz samples and reports frame-level probabilities or embeddings approximately every 0.48 seconds.

Evaluate and document:
- exact model source, license, checksum, and redistribution implications
- conversion/export steps to ONNX, if conversion is needed
- whether every used ONNX operator is supported on both target architectures
- cold-start time, average and worst-case inference time, CPU, and memory
- model and runtime bundle sizes
- behavior on silence, singing, speech, instrumental music, applause, and noisy mixtures
- false-confidence behavior and how probabilities should be calibrated
- packaged-app signing/notarization considerations
- whether Essentia or deterministic features are a better choice for any output

Do not claim Intel or Apple Silicon performance unless measured on that hardware. If only one architecture is available, provide a repeatable benchmark command and mark the other result pending. Do not add a permanent production dependency unless the spike clearly passes defined budgets.

Propose explicit budgets for Intel, including maximum worker CPU, memory, inference latency, queue depth, and startup impact. Recommend Go/No-Go and fallback options.

Place disposable spike code under an clearly marked experimental/test location and ensure normal SingWS builds do not load it. Update `docs/intelligent_audio/MODEL_EVALUATION.md` and `HANDOFF.md`. Do not enable model-driven decisions.
```

**Gate:** Proceed only if both architectures can be packaged and the Intel CPU budget is verified on actual Intel hardware.

### Prompt 6 — Model integration in observer mode only

**Recommended model: GPT-5.6 Sol Light**  
**Purpose: integrate approved inference without playback authority.**

```text
Integrate the approved lightweight classifier into SingWS Observer Mode only. Before coding, read `docs/intelligent_audio/MODEL_EVALUATION.md`, `ARCHITECTURE.md`, and `HANDOFF.md`. Stop and report a blocker if the evaluation does not contain an explicit Go decision and verified packaging plan.

Requirements:
- Feed the model a downmixed mono 16-kHz copy of decoded program audio through a bounded nonblocking ring buffer.
- Never allocate, resample, infer, log, lock, or perform I/O in the real-time audio callback beyond the minimal preallocated handoff approved by the architecture.
- Use one low-priority worker and drop stale frames rather than queueing them.
- Limit live inference to the useful ending region when practical; use cached analysis elsewhere.
- Add worker timeouts, heartbeat/health state, CPU-budget monitoring, and automatic bypass.
- Record model version, input timing, probabilities, calibrated class result, and confidence/reason codes.
- Combine model output with deterministic evidence only inside the observer decision engine.
- Model output must have no path to playback control.
- Preserve deterministic-only observer operation when the model/runtime is missing or fails.
- Add tests for malformed model files, unsupported runtime, worker crash, slow inference, stale frames, queue overflow, seek, rapid song changes, and shutdown.

Run packaging/build checks available in the environment and list actual versus pending Intel/Apple Silicon verification.

Update `docs/intelligent_audio/HANDOFF.md` with integration boundaries, budgets, test results, model provenance/version, failure behavior, and rollback instructions. Do not add Assisted or Automatic mode.
```

**Gate:** Several real shows should run in observer mode before any playback authority is added.

### Prompt 7 — USB input discovery and Signature 10 routing

**Recommended model: Claude Opus Light**  
**Purpose: design and prototype safe dual-input capture without vocal effects.**

```text
Design and prototype optional live microphone awareness for SingWS, with the Soundcraft Signature 10 as the first target. Read all `docs/intelligent_audio/`, current audio device/output code, and macOS packaging/permission configuration.

Hardware routing assumption to validate and document:
- Signature 10 USB output is switched to Aux 1/Aux 2 rather than Main L/R.
- Aux 1 contains both singer microphones.
- Aux 2 contains the host microphone.
- Karaoke/BGM USB-return channel has both aux sends at zero.
- USB return must never be fed back into the source aux buses.

First produce a design covering:
- simultaneous playback and capture through the same USB device
- CoreAudio device enumeration and stable identity
- channel mapping and sample-rate conversion
- aggregate-device and device-switch risks
- input loss/reconnect behavior
- permissions
- meters/calibration
- feedback-loop prevention and user warnings
- separation between transition analysis and future low-latency effects

Then, only if consistent with the current architecture, implement a diagnostic capture prototype that displays bounded Singer Feed and Host Feed activity meters and classifications. It must not alter playback or return processed audio.

Requirements:
- Off by default.
- Do not record or persist raw microphone audio.
- Never open the laptop microphone automatically.
- Clearly show the selected device and channels.
- Handle unplugging, sleep/wake, sample-rate changes, and missing channels by disabling mic awareness and preserving normal playback.
- Use basic activity/speech/sustained-vocal features first; do not assume ordinary speech VAD reliably recognizes singing.
- Add simulated-device tests and a manual Signature 10 test checklist.

Update `docs/intelligent_audio/USB_INPUT_DESIGN.md` and `HANDOFF.md` with exact implementation status and untested hardware behavior. Do not implement vocal effects or automatic transitions.
```

**Gate:** USB disconnects and configuration errors must not disrupt karaoke or BGM output.

### Prompt 8 — USB microphone awareness in observer decisions

**Recommended model: GPT-5.6 Sol Light**  
**Purpose: integrate singer/host activity safely, still without automatic control.**

```text
Integrate the approved USB singer and host activity feeds into Intelligent Transition Observer Mode. Read `USB_INPUT_DESIGN.md`, `MODEL_EVALUATION.md`, `ARCHITECTURE.md`, and `HANDOFF.md` before editing.

Add configurable mappings and modes:
- Live microphone awareness Off
- Observe only
- Singer protection proposal
- Singer protection plus host-ducking proposal

All modes remain advisory in this phase. Proposed logic should distinguish:
- lyrics/backing ending while singers remain active
- host speaking after a performance
- quiet room/feed
- sustained noise or likely feedback
- missing/disconnected input
- uncertain classification

Use hysteresis, debounce, minimum hold times, and monotonic timestamps so meters and proposed decisions do not chatter. Never treat one analysis frame as sufficient evidence. Device loss must immediately remove microphone evidence and return the observer to internal-audio-only reasoning.

Add UI device/channel selection, meters, calibration, Test mode, and an obvious status/failure indicator without overcrowding the main show screen. Add replay fixtures and tests for held notes, duet activity, host speech, noise bursts, disconnects, reconnects, stale input, and silent channels.

Do not send processed audio back to the mixer and do not enable automatic playback changes.

Update `docs/intelligent_audio/HANDOFF.md` with thresholds, state logic, tests, manual hardware checklist, known limitations, and rollback instructions.
```

**Gate:** Complete observer logs from real shows must demonstrate that singer and host detection improves timing rather than merely reacting to noise.

### Prompt 9 — Carefully limited Assisted mode

**Recommended model: Claude Opus Light for safety review, then GPT-5.6 Sol Light for implementation**

Run the review prompt first:

```text
Audit the complete Intelligent Audio implementation and observer logs before Assisted mode is allowed. Do not implement playback control in this session.

Read all `docs/intelligent_audio/`, relevant source/tests, and a representative sample of exported observer logs. Identify only actions that are high-confidence, reversible, and demonstrably safer than current behavior. Initially consider limiting Assisted mode to removal of obvious dead air after lyrics and meaningful audio have both ended.

Produce `docs/intelligent_audio/ASSISTED_MODE_GATE.md` containing:
- evidence from observer results
- approved and prohibited actions
- exact confidence/evidence requirements
- minimum/maximum timing bounds
- cooldown, hysteresis, and stale-data rules
- host override behavior
- model/worker/device failure behavior
- invariants protecting lyrics and intentional outros
- kill switch and rollback plan
- required unit, replay, integration, Intel, Apple Silicon, and live-show tests
- explicit Go/No-Go decision

Assume No-Go if evidence is incomplete. Update `HANDOFF.md`. Make no production behavior changes.
```

Only after an explicit Go decision, use this implementation prompt with GPT-5.6 Sol Light:

```text
Implement only the actions explicitly approved in `docs/intelligent_audio/ASSISTED_MODE_GATE.md`. Read every file in `docs/intelligent_audio/` and stop if the gate is absent, says No-Go, or is ambiguous.

Add an Assisted mode that is off by default and cannot exceed the approved authority. Preserve existing behavior whenever evidence is missing, stale, uncertain, or unhealthy. Human commands and per-track overrides always win immediately.

Requirements:
- Enforce all documented invariants in code and tests.
- Keep intentional-outro and active-lyric protections mandatory.
- Add an obvious emergency bypass with persistent status.
- Record every assisted action, evidence, confidence, timing, health state, and override.
- Ensure duplicate timers/events cannot trigger repeated transitions.
- Test worker/model failure, USB loss, seeks, song replacement, rapid skips, shutdown, and mode changes during playback.
- Do not add broader automatic beat mixing or vocal effects.

Run all possible tests and clearly list remaining hardware/live-show gates. Update `HANDOFF.md` with exact authority granted to Assisted mode, results, and rollback steps.
```

**Gate:** Assisted mode remains opt-in until multiple shows succeed on both architectures.

### Prompt 10 — Vocal-effects architecture

**Recommended model: Claude Opus Light**  
**Purpose: design the hard real-time subsystem separately from transition AI.**

```text
Design the SingWS Vocal Effects subsystem. This is separate from Intelligent Transition analysis and must never share its worker queue, latency assumptions, or failure path. Read all `docs/intelligent_audio/`, current audio I/O, and the current supported mixer workflows.

Primary Signature 10 signal path:
- singer microphones feed Aux 1 to USB input 1
- host microphone feeds Aux 2 to USB input 2
- dry microphones continue directly through the analog mixer
- SingWS processes the USB inputs and returns wet-only stereo effects through the mixer USB return channel
- USB return aux sends remain at zero to prevent a feedback loop

Design for:
- 64/128-sample low-latency operation where hardware supports it
- dry-through-mixer plus wet-only computer return
- reverb, slap delay, tempo-synchronized delay, chorus/doubling, special effects, and BGM ducking
- latency measurement and compensation
- overload bypass without pops
- device loss/reconnect
- feedback-loop detection/warnings
- no allocations, inference, locks, logs, or filesystem activity in the effects callback
- Intel and Apple Silicon packaging
- future separate-channel mixers
- explicit limitation that combined Singer Aux 1 is unsuitable for reliable duet pitch correction

Do not implement effects yet. Produce `docs/intelligent_audio/VOCAL_EFFECTS_ARCHITECTURE.md` with a signal-flow diagram, callback contract, DSP library evaluation, buffer strategy, latency budget, safety invariants, staged implementation plan, and test plan. Include a minimal first milestone that passes audio through with unity gain and then adds one wet-only effect.

Update `HANDOFF.md` and return a concise list of decisions requiring real Signature 10 testing.
```

**Gate:** Do not combine the vocal-effects callback with the model/transition-analysis worker.

### Prompt 11 — Vocal-effects pass-through and first effect

**Recommended model: GPT-5.6 Sol Light**  
**Purpose: implement the smallest safe real-time effects slice.**

```text
Implement only the first approved milestone in `docs/intelligent_audio/VOCAL_EFFECTS_ARCHITECTURE.md`. Stop if that document lacks explicit callback invariants, latency budget, dependency decision, or test plan.

Begin with:
1. audio-device/channel configuration
2. stable low-latency input-to-wet-return pass-through at unity/zero effect
3. hard bypass and safe device-loss behavior
4. latency/overload diagnostics outside the callback
5. one wet-only effect approved by the architecture, preferably a simple delay or reverb

Requirements:
- Never route dry microphone audio back unless the design explicitly calls for it; the analog mixer already carries dry vocals.
- Never feed the USB return back into Aux 1/Aux 2.
- Preallocate callback resources.
- No model inference, database access, logging I/O, locks, or UI calls in the audio callback.
- Smooth parameter changes to prevent zipper noise and pops.
- Default to bypass/off.
- Recover safely from sample-rate changes, disconnects, overloads, and app shutdown.
- Add offline impulse/signal tests, callback timing tests, clipping tests, bypass tests, and simulated device failures.
- Do not implement pitch correction in this phase.

Run available tests and provide a manual Signature 10 routing/test checklist with volume-down safety steps. Update `HANDOFF.md` with measured latency where actually tested, remaining architecture-specific tests, and rollback steps.
```

**Gate:** Test first with speakers/amps down and verify no feedback route before raising levels.

### Prompt 12 — Final integration audit

**Recommended model: Claude Opus Light**  
**Purpose: ensure independently built pieces form one safe system.**

```text
Perform a final cross-system audit of SingWS Intelligent Transitions, USB microphone awareness, and Vocal Effects. Do not add new features.

Read the entire `docs/intelligent_audio/` directory, all related source changes, tests, build scripts, settings migrations, and observer/assisted diagnostics available in the repository.

Verify:
- transition analysis and vocal effects are isolated by thread, queue, and failure domain
- audio callbacks contain no blocking or unbounded operations
- every queue is bounded with a defined overflow policy
- stale timestamps cannot control current playback
- device changes and sleep/wake fail safely
- Intel and Apple Silicon dependencies/package contents are correct
- settings and database migrations are backward compatible
- model provenance/versioning and cached analyzer versioning are complete
- microphone audio is never persisted
- human controls and track overrides always win
- Off mode restores legacy behavior
- Observer has no playback authority
- Assisted cannot exceed its documented authority
- wet effects cannot create an internal feedback route
- shutdown and crash recovery leave audio devices usable

Run the broadest safe test suite available, add missing regression tests only when directly tied to discovered integration defects, and avoid unrelated refactors.

Produce `docs/intelligent_audio/FINAL_AUDIT.md` with Passed, Failed, Unverified Hardware, and Release Blockers sections. For every claim, cite the relevant symbol/test/log. Update `HANDOFF.md` with the next concrete action. Do not label the system production-ready while either Mac architecture or required mixer routing remains unverified.
```

## Combining work from both models

Use one branch and sequential commits. Do not ask both models to implement the same phase independently and then merge large competing changes. The safe handoff cycle is:

1. Start from a clean, committed branch.
2. Give the selected model exactly one prompt above.
3. Review its diff and `HANDOFF.md`.
4. Run the listed tests yourself or in a separate review session.
5. Commit that phase with its documentation and tests.
6. Give the next model the next prompt and require it to read the existing documentation first.
7. If a gate says No-Go, fix or gather evidence rather than skipping forward.

For especially risky phases, use Opus Light to audit and write the gate, then Sol Light to implement only what the gate explicitly permits. This prevents architectural assumptions from being lost between sessions while keeping implementation work bounded.

## Summary of agreed direction

- Intel support is mandatory.
- ONNX Runtime CPU is the baseline inference path.
- Internal decoded audio analyzes the track.
- USB mixer inputs detect live singers and host speech.
- Signature 10 uses Aux 1 for singers and Aux 2 for the host.
- Audience detection is optional and separate.
- Transition intelligence and vocal effects are separate subsystems.
- Vocal effects use a low-latency wet-only USB return.
- Observer mode and safe fallback precede automatic control.
- Per-track human overrides always win.
