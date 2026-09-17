# Final integration audit (Prompt 12)

Cross-system audit of Intelligent Transitions, USB mic awareness and Vocal
Effects. **No features were added.** One integration defect was found and fixed
(§Failed). Written 2026-09-16 against `main`.

## Headline

**Not production-ready, and not close on the dimension that matters.** The code
is in good shape; the *evidence* is not. The two newest subsystems have never
run in anger:

- the transition observer has **watched zero songs** — no build existed until today
- the effects stream has **carried zero audio** — full duplex needs one device
  with both input and output, which this Mac does not have

Everything below distinguishes what is verified from what is merely written.

---

## Passed

**Isolation by thread, queue and failure domain.**
`vocal_fx.VocalFXMonitor` subclasses `SoundMonitor` rather than sharing an
instance, so effects and the classifier keep separate failure counters —
asserted by `test_vocal_fx.IsolationTests.test_monitor_is_its_own_instance_not_a_shared_one`.
Effects run in their own process with their own supervisor; analysis is reniced
and low priority, effects are not.

**Audio callbacks contain no blocking or unbounded operations.**
Enforced structurally, not by review: `native/vocal_fx/build.sh --test` fails if
`vfx_dsp.o` references any allocator, lock or I/O symbol (`nm -u` check). The
callback's work is bounded by frame count, and a frame count above the
preallocated maximum writes silence and counts an overrun rather than growing a
buffer (`vfx_engine.c` `render_cb`). No Swift runs in the callback by design.

**Queues are bounded with a defined overflow policy.**
`transition_events.py:100` — ring `deque(maxlen=capacity)`; overflow drops the
oldest, counts it (`_dropped`), and reports the count with the next batch
(`transition_events.py:11-12,134-136`). `sound_monitor.py:58` — window history
bounded. One exception, see Release Blockers.

**Stale timestamps cannot control playback.**
Generation tokens reject superseded events (`transition_observer._current`,
`stats["stale_ignored"]`); `test_stale_generation_sound_ignored` and
`test_backwards_clock_ignored` cover it. Cached cues are rejected when the
analyzer version moved (`transition_cues.py:81`).

**Device changes fail safely.**
Device loss and sample-rate change are transient everywhere: mic
(`USB_INPUT_DESIGN.md`) and effects (`vocal_fx._TRANSIENT`) both drop evidence,
retry, and do **not** count a failure — `test_device_loss_is_transient_and_never_bypasses`,
`test_format_change_is_transient`. Repeated genuine crashes bypass for the
session (`test_repeated_crashes_bypass_for_the_session`).

**Microphone audio is never persisted.**
The helpers write only to stdout; no `FileManager`, no `fopen`, no file handles
other than stdin/stdout across `native/sound_helper/*.swift` and
`native/vocal_fx/*`. Levels and classifications only.

**Model provenance and analyzer versioning.**
Model name captured from `hello` and stamped on every window
(`sound_monitor.py:235,257`). Cache records carry `cue_version` and
`analysis_version` and are discarded when either moves (`transition_cues.py:43-44,81`).

**Off mode restores legacy behaviour.**
`test_off_mode_emits_nothing`. Every switch defaults off:
`ia_instrumentation_enabled` False, `ia_mic_awareness_enabled` False,
`ia_sound_classifier_enabled` False (`0.2.18.1.py:3688-3693`), and effects are
off for absent or malformed settings (`test_disabled_by_default_and_for_bad_settings`).

**Observer has no playback authority.**
The strongest result in this audit, because it is structural rather than
promised: `test_observer_module_imports_nothing_that_can_play` and
`test_observer_public_api_is_inputs_only` parse the module with `ast` and assert
it imports nothing that can play and exposes inputs only;
`test_app_feed_never_reaches_playback_objects` covers the call site.
`vocal_fx.py` has the same guard (`test_module_never_imports_playback_or_transport`).

**Assisted cannot exceed its documented authority.**
Trivially, and in the safest possible way: **Assisted mode does not exist.**
`transition_observer.MODES` is `("off", "observer")` only. `ASSISTED_MODE_GATE.md`
reads No-Go, and the roadmap's implementation prompt refuses to run against a
No-Go gate.

**Human controls always win.**
Follows from the above: nothing in these subsystems can act, so every transition
is still driven by the existing 0.4.7.x code and the operator. This claim must be
re-audited the moment Assisted mode exists — it is currently true by absence, not
by design.

**Wet effects cannot create an *internal* feedback route.**
The engine reads device input and writes device output; there is no path routing
output back to input (`vfx_engine.c` `render_cb`). The real feedback risk is
external mixer routing, which is handled by detection and the amps-down
checklist, not by this code.

**Shutdown and crash recovery leave audio devices usable.**
`vfx_engine_destroy` stops, uninitialises and disposes both units, and now
deregisters its property listeners (see Failed). The helper exits on stdin EOF so
it cannot outlive the app (`SingWSVocalFX.swift`), and `_stop_proc` closes stdin
to trigger that (`sound_monitor.py:144-145`).

**arm64 package contents.**
All three Swift helpers ship arm64 in the built bundle — `SingWSSoundHelper`,
`SingWSMicMeter`, `SingWSVocalFX` — verified with `lipo -archs` against
`dist/SingWS Pro.app` after a clean build. Bundle passes its own gates: arm64,
media core loads, macOS 15 minimum, strict signing, DMG verified.

**Settings migrations are backward compatible.**
New keys are additive with defaults; the existing `*_migrated` flag pattern
(`0.2.18.1.py:3600-3633`) is unchanged. An old settings file deserializes.

**Test suites.** `./tools/run_tests.sh` **exits 0**: 1145 passed + 51 subtests,
then 86 native. `native/vocal_fx/build.sh --test`: 13 signal tests plus the
symbol check.

---

## Failed (found and fixed in this audit)

**Use-after-free on the vocal-effects teardown path.**
`vfx_engine_create` registers two Core Audio property listeners
(`kAudioDevicePropertyDeviceIsAlive`, `kAudioDevicePropertyNominalSampleRate`)
*before* `AudioUnitInitialize`. `vfx_engine_destroy` disposed the units but never
deregistered those listeners, leaving Core Audio holding a pointer to freed
memory and calling into it on the next device property change. Reachable through
`create`'s own failure path, which calls `destroy` after the listeners are
installed.

Fixed in `vfx_engine.c` `vfx_engine_destroy` by removing both listeners before
the struct is freed. This is the same class of defect as the watchdog
use-after-free in live-show rule 9, and it was invisible to every test because
the stream has never run.

No regression test added: the failure needs a real device property change with a
live Core Audio registration, which cannot be simulated here without the audio
path this audit says is unproven. **Recorded as an explicit gap.**

---

## Unverified — hardware

None of these can be settled from the source tree. None should be guessed.

| Area | Unverified |
|---|---|
| Observer accuracy | Every proposal. No song has been observed. False-early-end rate — the one that cuts a singer off — is unknown. |
| Classifier | `SingWSSoundHelper` compiled and lists labels, but has never classified program audio. |
| Mic awareness | `SingWSMicMeter` compiled, never run against a mixer. Ui24R not owned. Channel map assumed, unconfirmed. |
| Vocal effects | **The stream has never run.** Round-trip latency, 64-frame stability, reverb by ear, overload under load, unplug mid-playback. |
| Latency figure | The helper's `estimated_latency_ms` is arithmetic from advertised device figures, not a measurement. |
| Chorus viability | Wet sums acoustically with dry, so round-trip is a comb filter. May be unusable. |
| Sleep/wake | Transient path is designed and unit-tested; never exercised through a real sleep. |
| Qt 6.11 rendering | Host and audience windows inspected once on screen; not through a full show. |

---

## Release blockers

1. **No observer evidence.** `ASSISTED_MODE_GATE.md` is No-Go. Assisted mode
   cannot ship, and the transition work cannot be called validated, until
   multiple real shows have been observed and reviewed.
2. **Ad-hoc signing, no hardened runtime, no notarization.**
   `build_singws_mac_arm64.sh:170` is `codesign --force --deep --sign -`. There
   is no `--options runtime` and no notarization step. This is not theoretical:
   today's investigation found the 1.x app reporting
   `authorization_status=0 / kCLErrorDenied` for location because **TCC grants do
   not survive ad-hoc re-signed rebuilds**. The effects and mic helpers need
   microphone permission and will hit the same wall on every rebuild.
3. **The effects audio path is unproven.** VFX0's own deliverable — a measured
   round-trip latency — does not exist.
4. ~~**Unbounded logging queue.**~~ **Closed 2026-09-16.** Now
   `queue.Queue(maxsize=_LOG_QUEUE_MAX)` behind `_BoundedLogQueueHandler`, which
   drops when full, counts the drops, and reports the total once there is room —
   a defined overflow policy rather than silent loss. Covered by a behavioural
   test, not only a source assertion.
5. ~~**`vfx_*` settings are not in `DEFAULTS`.**~~ **Closed 2026-09-16.** Added,
   all off/neutral; nothing starts the helper yet.

### Also closed 2026-09-16 — the quiet-dependency pattern

`media_helpers.probe_duration_seconds()` caught a missing `mutagen` and returned
0.0 for every file, which silently disabled trailing-silence detection. That
exact shape — a lazy import, a bare `except`, and no test touching the real
dependency — produced two separate faults in one day (mutagen, CoreLocation).
The dependency failure is now reported once, loudly, while per-file failures stay
quiet, and `dependency_status()` exposes it for diagnostics.

### Still open — rotation card text clipping

The card shows "No singer is" and "Queue a song to start ⋯" with the ends
missing. **An attempted fix made it worse and was reverted** (verified on screen
both times): `setWordWrap(True)` caused the title to wrap onto three lines,
overflow the card and collide with the subtitle, because a word-wrapped QLabel
has a small minimum width and the layout starves it.

The useful finding for the next attempt: the text column is really only ~120 px
wide in the running app. An isolated reproduction of the same row — avatar 96
fixed, text stretch 1, meter 150 fixed, card 870 — gives the labels 590 px and
does **not** reproduce the clipping. So the constraint is somewhere other than
that row's obvious geometry, and it must be found before changing anything;
guessing at the layout produced a worse regression once already.

**Do not label this system production-ready.** Apple Silicon hardware testing and
mixer routing are both unverified, and by the roadmap's own release gates that is
disqualifying on its own.

---

## Next concrete action

Two operator tasks, in this order, because everything else waits on them:

1. **Install the Pro build and run real songs** with `ia_instrumentation_enabled`
   on and the observer in `observer` mode. Collect the logs. That alone unblocks
   `ASSISTED_MODE_GATE.md` and converts the largest Unverified block above into
   evidence.
2. **Work the Signature 10 checklist** at the end of
   `VOCAL_EFFECTS_ARCHITECTURE.md`, amps down, and record the measured round-trip
   latency.

The highest-value *code* task meanwhile is blocker 2: move off ad-hoc signing.
It is the one item that will otherwise keep re-breaking permissions on every
build, and it has already cost an investigation today.
