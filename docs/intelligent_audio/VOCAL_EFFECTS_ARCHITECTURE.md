# Vocal effects architecture (Prompt 10)

Design only. **No effects code was written and no production behaviour changed.**
Written 2026-09-16 against `main`.

Companion documents: `USB_INPUT_DESIGN.md` (Prompt 7, mic capture),
`ARCHITECTURE.md` (what must never be blocked), `ASSISTED_MODE_GATE.md`.

## 0. Two corrections to the roadmap brief

**Hardware order is reversed.** The Prompt 10 brief in `ROADMAP.md` describes a
"Primary Signature 10 signal path". Prompt 7 (`USB_INPUT_DESIGN.md`) already
decided the opposite and it still holds: **Ui24R primary, Signature 10 backup.**
This document follows the newer decision. The Signature 10 path is designed for
as a first-class fallback because it is the mixer that actually exists today —
the Ui24R is still not owned.

**The callback cannot be Swift.** The brief says "native Swift helper process
using AVAudioEngine". The process should be Swift; **the render callback must not
be.** Swift's ARC can retain/release, allocate and take locks on any object
touch, and none of that is permissible in a real-time callback. See §4.

## 1. Signal flow

Wet-only return. The dry voice never passes through the computer, so a helper
crash, an overload, or a full app failure costs the effect — never the vocal.

```
             ┌──────────── mixer (Ui24R) ────────────┐
 mic 1 ─────►│ ch1 ──┬──────────────────► Main L/R ──┼──► speakers   (DRY, always)
 mic 2 ─────►│ ch2 ──┤                               │
 host  ─────►│ ch3 ──┤                               │
             │       └── USB send (per-channel) ─────┼──┐
             │                                       │  │
             │ USB return (stereo) ──► Main L/R ─────┼──┼──► speakers (WET only)
             └───────────────────────────────────────┘  │
                                                        ▼
                              SingWSVocalFX  (separate process)
                              HAL I/O unit, 64–128 frames
                              wet-only out; dry never returned
```

**Feedback rule, non-negotiable:** the USB return must never feed any bus that
feeds the USB send. On the Ui24R that means the return channel's sends to the
channels being captured stay at zero; on the Signature 10 both Aux knobs on the
USB return channel stay fully down. This is the same rule as Prompt 7 §19 and the
helper must detect violations rather than trust configuration (§8).

**Signature 10 limitation, carried forward:** both singers share Aux 1, so singer
mics arrive pre-mixed. Reverb, delay and BGM ducking are fine on a shared bus.
**Per-singer pitch correction is impossible** on that path and must not be offered
when the Signature 10 profile is active — not degraded, not approximated, absent.

## 2. Process and ownership

`SingWSVocalFX` is a **separate process**, following the established helper pattern
(`SingWSSoundHelper`, `SingWSMicMeter`): JSON lines on stdio, exits on stdin EOF so
it never outlives the app, supervised for heartbeat and resource budgets.

It shares that *pattern* and shares **nothing else**:

| | Transition analysis / classifier | Vocal effects |
|---|---|---|
| Latency budget | ~100 ms, best effort | 64–128 frames, hard |
| Failure cost | Degraded suggestions | Audible artefact in the room |
| Scheduling | Low priority, reniced | Real-time thread, never reniced |
| Worker queue | `SoundMonitor` shared | **Its own supervisor** |

Per the Prompt 10 gate: *do not combine the vocal-effects callback with the
model/transition-analysis worker.* That extends to the supervisor. Reusing
`SoundMonitor`'s instance would couple a classifier stall to the effects path.
Reuse the *code*; do not share the *instance* or the failure counter.

## 3. Real-time graph

```
HAL AUHAL (input)  ──► ring ──► render callback ──► AU chain ──► HAL AUHAL (output)
                                      │
                        parameter snapshot (lock-free, double-buffered)
                                      ▲
                        control thread (JSON lines from SingWS)
```

Prefer a **HAL I/O AudioUnit with a C render callback** over `AVAudioEngine` for
the real-time path. `AVAudioEngine` is convenient for graph construction and fine
for setup, but its Swift-facing tap and node callbacks are not safe at 64 frames.
Where AVAudioEngine is used, it must not own the callback.

Apple AUs are hosted by calling `AudioUnitRender` on pre-instantiated units from
inside the callback — instantiate, allocate and configure everything before the
stream starts.

## 4. Callback contract

Inside the render callback, **none of the following may occur**:

- allocation or free (including anything that could trigger Swift ARC)
- locks, mutexes, semaphores, or any call that can block
- Objective-C or Swift message dispatch on objects that may be released
- logging, `print`, `os_log`, filesystem or network access
- inference, model evaluation, or any call into the classifier
- unbounded loops, or work proportional to anything but the buffer size

Permitted: reading a lock-free parameter snapshot, arithmetic on preallocated
buffers, `AudioUnitRender` on preconfigured units, atomic stores of counters.

Parameter changes cross the boundary as an **immutable snapshot published
atomically** (double-buffer plus an atomic index). The control thread builds the
next snapshot, then publishes; the callback reads whichever index is current. No
partial state is ever visible. Smoothed parameters (gain, mix) ramp per-buffer
inside the callback rather than jumping.

## 5. Apple Audio Units vs third-party

Evaluated against AGENTS.md: no paid or proprietary SDKs; built-in macOS
frameworks are explicitly allowed.

| Effect | Built-in | Verdict |
|---|---|---|
| Reverb | `kAudioUnitSubType_Reverb2` | **Use it.** Good quality, low cost, tolerant of round-trip latency. |
| Slap / tempo delay | `kAudioUnitSubType_Delay` | **Use it.** Tempo sync computed host-side from BPM already in `phrase_detect`. |
| EQ / filters | `kAudioUnitSubType_NBandEQ` | Use it. |
| Ducking | Gain in our own callback | Trivial; do not pull in a sidechain AU. |
| **Chorus / doubling** | **None** | **Gap.** macOS ships no chorus AU. Needs a small modulated fractional-delay line of our own — roughly 40 lines of C, no dependency. |
| Pitch correction | `AUNewTimePitch` exists | **Not in scope.** Latency and the shared-bus limitation (§1) make it unsuitable; revisit only on per-mic Ui24R channels. |

No third-party DSP is required for the planned effect set. Signalsmith Stretch is
already vendored if pitch/time work is ever revisited, and is free.

## 6. Buffer and latency budget

Target: **64 frames** where the device supports it, **128** as the safe default.

| Stage | 64 @ 48 kHz | 128 @ 48 kHz |
|---|---|---|
| Mixer A/D + USB in | ~1.5–3 ms | ~1.5–3 ms |
| Input buffer | 1.33 ms | 2.67 ms |
| Effect processing | < 0.5 ms | < 0.5 ms |
| Output buffer | 1.33 ms | 2.67 ms |
| USB out + D/A | ~1.5–3 ms | ~1.5–3 ms |
| **Round trip** | **~6–9 ms** | **~8–12 ms** |

These are estimates from the buffer maths and typical USB class-compliant
figures. **Real round-trip must be measured, not assumed** (§10).

Why it matters per effect, given the dry path is acoustically summed with the wet:

- **Reverb, slapback:** latency is inaudible as a defect — a late wet tail is what
  reverb *is*. Safe at either buffer size.
- **Chorus / doubling:** wet and dry sum acoustically, so round-trip delay becomes
  a fixed comb filter on the combined signal. At ~8 ms this is an audible hollow
  colouration, not a subtle one. **This is the effect that decides whether 64
  frames is required**, and it must be validated by ear on real hardware before
  chorus is offered at all.

Latency compensation: the helper reports measured round-trip; tempo-synced delay
times subtract it so a synced delay lands on the beat at the speaker, not at the
converter.

## 7. Safety invariants

1. **Dry is never at risk.** The computer only ever adds a wet signal.
2. Helper crash, overload, device loss or bypass ⇒ **wet fades to silence over
   ~10 ms**; never a hard mute (pop) and never a stuck tail.
3. The effects path never touches karaoke or BGM playback. mpv and BASS are
   untouched by this subsystem; a helper failure cannot interrupt a song.
4. No effect is ever enabled by default. Off until the operator turns it on, per
   live-show rule 10.
5. Overload bypass is automatic and latching: repeated overruns bypass for the
   session and say so visibly, rather than oscillating in and out mid-song.
6. The helper exits on stdin EOF. It cannot outlive SingWS Pro.
7. No audio is ever written to disk. Levels and diagnostics only, as Prompt 7.

## 8. Feedback detection

A wet-only return can still howl if the mixer is misrouted. The helper watches for
sustained broadband gain with rising level across consecutive buffers on the
*return-adjacent* input and, on detection, **fades wet to silence, latches bypass,
and reports** rather than merely warning. A feedback loop in a full room is not a
condition to warn about and continue.

This complements Prompt 7's `suspect_noise`, which is a slower, level-only heuristic
on the metering path. They are independent by design.

## 9. Device loss, sleep/wake, signing

Device handling follows Prompt 7 exactly: `kAudioDevicePropertyDeviceIsAlive` and
sample-rate change are **transient** — drop to silence, report, retry every 2 s,
do not count a failure. Crashes, garbage output, missed heartbeats and resource
overruns count; three ⇒ bypassed for the session.

**Signing reality, and it is a problem worth stating.** The brief asks for
"hardened runtime and notarization". `build_singws_mac_arm64.sh` currently uses
**ad-hoc signing** (`codesign --sign -`) and does not notarize. Today, 2026-09-16,
we confirmed the consequence of that on a different feature: the 1.x app's location
permission reads `authorization_status=0` because TCC grants do not survive
ad-hoc re-signed rebuilds. An effects helper needs **microphone** permission.
Unless signing changes, the operator will face the same repeated-prompt or
silently-denied behaviour on every rebuild. Resolve signing before shipping
effects to a show; it is not a blocker for the passthrough milestone.

## 10. Staged plan

Each stage stops and reports. Nothing reaches a show until the stage above it is
verified on hardware.

- **VFX0 — Passthrough.** Helper process, control channel, HAL I/O, unity gain,
  wet-only. Proves device selection, buffer size, round-trip latency measurement
  and clean start/stop. **No effect.** Deliverable: measured round-trip number.
- **VFX1 — One wet effect (reverb).** `Reverb2` in the chain, wet-only, smoothed
  wet/dry, fade-to-silence on bypass. Chosen first because it is the effect most
  tolerant of round-trip latency.
- **VFX2 — Supervision.** Own supervisor instance, heartbeat, overload bypass,
  device loss/reconnect, feedback detection.
- **VFX3 — Delay + tempo sync**, using existing BPM, with latency compensation.
- **VFX4 — Ducking**, driven by host-mic evidence already produced by Prompt 8.
- **VFX5 — Chorus/doubling**, custom modulated delay, **gated on the comb-filter
  validation in §6**. May be abandoned if 64 frames is unreachable on the hardware.

## 11. Test plan

- **Unit (host-side):** control protocol, parameter snapshot publication, tempo→delay
  maths with latency compensation, settings persistence.
- **Helper, offline:** render callback driven by a synthetic clock through a
  preallocated graph; assert bit-exact unity gain at VFX0 and no allocation in the
  callback (verified by an allocation-trapping harness, not by inspection).
- **Fault injection:** helper kill mid-song, device unplug, sample-rate change,
  stdin EOF, overload storm, parameter spam during playback.
- **Integration:** karaoke and BGM continue undisturbed through every fault above —
  this is the one that matters most, and it is testable without hardware.
- **Hardware (operator):** real round-trip measurement, reverb by ear, feedback-loop
  drill with speakers down, chorus comb-filter judgement.

## 12. Decisions requiring real hardware

These cannot be settled from the source tree, and none should be guessed:

1. **Actual round-trip latency** at 64 and 128 frames on the real interface.
2. **Whether 64 frames is stable** under show load, or only 128.
3. **Whether chorus/doubling is viable at all** — the acoustic comb filter of wet
   against dry is a judgement call by ear, in a room.
4. **Ui24R USB channel map** — assumed Singer 1 = 1, Singer 2 = 2, Host = 3, still
   unconfirmed (carried over from Prompt 7).
5. **Whether output and effects can share one device cleanly**, or whether an
   aggregate device with drift compensation is needed.
6. **Signature 10 Aux bleed** — how much dry leaks into the send, which sets the
   usable wet/dry ratio on the backup path.
7. **Whether microphone TCC survives** the current ad-hoc signing across rebuilds (§9).

---

# VFX0/VFX1 implementation notes (Prompt 11, 2026-09-16)

Built: `native/vocal_fx/` (`vfx_dsp.c` real-time core, `vfx_engine.c` Core Audio,
`SingWSVocalFX.swift` host process, `build.sh`) and `vocal_fx.py` (app side).

**The callback is C, as §4 requires.** Swift handles argv, JSON and lifecycle
only. This is enforced structurally, not by intent: `build.sh --test` fails if
the real-time core's object file references any allocator, lock or I/O symbol.

## Verified without hardware

`./native/vocal_fx/build.sh --test` — 13 signal tests plus the symbol check:

- starts bypassed and silent (nothing is ever enabled by default)
- **unity gain is bit-exact passthrough** — the VFX0 deliverable
- bypass fade never rises, reaches true silence, and the first bypassed buffer
  is not a hard mute (the pop this fade exists to prevent)
- gain changes glide with no step discontinuity (no zipper noise)
- limiter keeps output inside full scale and counts what it squashed
- snapshot publication, overrun accounting, and null/zero/negative frame safety

`test_vocal_fx.py` — 16 tests: opt-in defaults, command construction, unknown
effect falling back to passthrough, wet clamping, bypass, dead-pipe writes
returning False instead of raising, device loss and format change treated as
transient (never counted as failures), repeated crashes bypassing for the
session, restart restoring the operator's setting rather than silently staying
bypassed, and the isolation guards — `vocal_fx.py` may not import any playback
module, and two monitors may not share a failure counter.

Helper error paths were exercised for real: an unknown device gives
`{"type":"error","message":"audio device not found"}` and exit 3; an input-only
device gives `cannot bind device`. No crash in either.

## NOT verified, and cannot be here

**The stream has never run.** This Mac's built-in microphone and speakers are
*separate* Core Audio devices, and full duplex needs one device carrying both.
Nothing was faked to work around that: no aggregate device was created on the
operator's machine, and the audio path is therefore unproven.

That means unmeasured: real round-trip latency, whether 64 frames is stable,
reverb quality by ear, overload behaviour under load, and device unplug during
playback. **The estimated latency the helper reports is arithmetic from the
device's advertised figures, not a measurement.** Treat it as a starting guess.

## Manual test checklist — Signature 10

**Speakers and amplifiers fully down before step 1. Do not raise them until
step 6 confirms there is no feedback route.**

1. **Amps down.** Signature 10 connected by USB. Both Aux knobs on the USB
   return channel **fully down** — this is the feedback route, and it is the
   only thing preventing a howl.
2. Route singer mics to Aux 1, host mic to Aux 2. Confirm the dry mics still
   reach Main L/R with the computer switched off entirely.
3. `./native/vocal_fx/SingWSVocalFX --list-devices` and note the Signature 10 UID.
4. `--device <UID> --channels 1,2 --frames 128 --effect none`. Confirm `hello`
   reports the expected sample rate and channel count.
5. Send `{"type":"params","wet":1.0,"enabled":true}`. **Still bypassed at the
   mixer**: bring the USB return channel up only far enough to hear it at very
   low level.
6. **Feedback check.** Speak into a mic. Raise the return slowly. If level
   builds on its own, stop: an Aux is feeding the return. Fix routing, restart.
7. Only now raise amps to normal. Confirm dry is unchanged and the wet return
   adds a duplicate of the voice at unity (VFX0 has no effect).
8. Restart with `--effect reverb` and repeat 5–7. Reverb should be audible only
   in the return; the dry voice must sound untouched.
9. **Pull the USB cable mid-speech.** The wet must fade to silence and the dry
   must be entirely unaffected. The helper should report `device_lost` and the
   host should retry without counting a failure.
10. Measure real round-trip if possible (a click into a mic, recorded against
    the return) and record it — several later decisions depend on that number.

Signature 10 limitation, repeated because it is easy to forget: both singers
share Aux 1, so per-singer processing is impossible on this path. Reverb, delay
and ducking are fine; pitch correction is not offered and must not be.
