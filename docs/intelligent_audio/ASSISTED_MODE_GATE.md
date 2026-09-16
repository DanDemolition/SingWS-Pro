# Assisted mode gate

Prompt 9 part 1 — audit only. **No production behaviour was changed.**
Written 2026-09-16 against `main` at the CoreLocation fix.

## Decision: **NO-GO**

Not because anything is wrong with the implementation. Because the evidence this
gate exists to weigh **does not exist yet**.

The roadmap's own instruction is "Assume No-Go if evidence is incomplete", and the
audit is meant to read "a representative sample of exported observer logs". There
are none. A search of `~/SingWS` and `~/SingWSPro` returns no observer or
transition-event logs, because until 2026-09-16 there was no build of SingWS Pro
that could run at all, so the observer has never watched a song.

**Nothing further in this document can be validated until that changes.** The
sections below are the criteria Assisted mode would have to meet, written now so
the data collection can be aimed at them rather than gathered blind.

## 1. Evidence status

| Required evidence | Status |
|---|---|
| Observer proposals vs actual outcomes, real shows | **None.** Observer has never run against a song. |
| Proposal accuracy by track type (CDG / MP4 / streaming) | None |
| False-hold and false-early-end rates | None |
| Classifier agreement with deterministic cues | None. Helper compiled 2026-09-16, never run against program audio. |
| Mic evidence in a real room | None. `SingWSMicMeter` compiled, never run; the Ui24R is not owned. |
| Analysis/worker health under show load | None |
| Replay harness coverage | **Present** — 17 scenarios pass. Synthetic, not evidence of real-world accuracy. |

The replay fixtures are the only thing in this table that exists, and they prove
the logic does what it was designed to do — not that the design matches reality.

## 2. What Assisted mode may eventually be allowed to do

Per the roadmap, the initial scope is **removal of obvious dead air after both
lyrics and meaningful audio have ended**, and nothing else.

Approved (once evidence supports it):

- Start the BGM fade at `safe_bgm_entry` when `safe_bgm_entry_confidence >= 0.8`.
- End karaoke at `safe_early_end` when `safe_early_end_confidence >= 0.8` **and**
  the cue carries `early_end_verified_audio_and_lyrics`.

Prohibited in the first Assisted release, explicitly:

- Any action on `lyrics_outlast_audio` — lyrics outrank silence (`transition_cues.py:10`).
- Skipping, reordering, or shortening a song a singer is still performing.
- Beat-matched or harmonic mixing (that is MS2, a separate gate).
- Vocal effects (Prompts 10–11).
- Any mixer or device change.
- Acting on classifier or mic evidence **alone**, without a deterministic cue.

## 3. Confidence and evidence requirements

The implementation already enforces most of this; the gate adopts it rather than
inventing new numbers.

- `MIN_PROPOSAL_CONFIDENCE = 0.8` for any cue-driven action.
- Sound evidence: `MIN_SOUND_CONFIDENCE = 0.6`, with `VOCAL_ON_WINDOWS = 3` /
  `VOCAL_OFF_WINDOWS = 3` hysteresis. Advisory only — may veto an action, never cause one.
- Mic evidence: `MIC_ON_S = 0.5` to assert, `MIC_OFF_S = 1.2` minimum hold to clear.
  A `suspect_noise` channel stays untrusted until it goes silent. Veto only.
- Deterministic cues are necessary. Classifier and mic evidence can **withhold** an
  action but can never justify one on their own.

## 4. Timing bounds

- No assisted action before `LISTEN_LEAD_S = 15 s` from the earliest cue.
- Never earlier than `safe_early_end` / `safe_bgm_entry`; the cue is a floor, not a target.
- Minimum 1.0 s between an action and any subsequent assisted action on the same song.
- One assisted early-end per song generation. Ever.

## 5. Cooldown, hysteresis, stale data

- Proposals are already suspended after a seek (`observer_compare … proposals_suspended`).
  Assisted mode must keep that and additionally refuse to act for the remainder of
  the song after any seek.
- Generation tokens already reject stale events (`stats["stale_ignored"]`). Any
  event from a superseded generation must never act.
- Evidence older than one position tick is stale and must be discarded, not reused.
- After any bypass trip, Assisted stays off until the operator re-enables it.

## 6. Host override

- Any explicit operator action (play, pause, skip, complete, seek, BGM control)
  immediately outranks and cancels any pending assisted action.
- Per-track overrides win permanently for that track.
- Override must take effect without waiting for a worker, a timer, or a helper.

## 7. Failure behaviour

Assisted mode must **fall back to current 0.4.7.x behaviour**, not to a degraded
variant, on any of: classifier helper stall/crash/RSS breach; mic helper loss or
device unplug; missing or stale cached analysis (`no_cached_cues`); GUI stall
beyond the transition tick budget; unknown or uncertain observer state.

`SoundMonitor`'s existing supervision (heartbeat, crash/garbage/RSS budgets,
bypass after repeated failures) is the model; Assisted must not invent a second one.

## 8. Invariants

1. Lyrics never get cut. `lyrics_outlast_audio` blocks early end, unconditionally.
2. A deliberate silent outro is not dead air. Absence of evidence is never evidence.
3. Singer mic active ⇒ no early end and no BGM entry, regardless of cue confidence.
4. No assisted action without a deterministic cue at ≥ 0.8.
5. Assisted authority never exceeds what this document lists, and the code must
   make that structurally true, not merely intended.
6. The observer's "no access to playback" property is **not** relaxed. Assisted
   mode is a separate consumer of proposals; the observer must remain unable to act.

That last one matters: the current design is safe by construction, and the
temptation when implementing Assisted mode will be to let the observer call the
transport directly. It must not.

## 9. Kill switch and rollback

- `transition_observer_mode` back to `observer` disables all assisted action.
- `ia_instrumentation_enabled` false disables the event stream entirely.
- An always-visible emergency bypass with persistent status, reachable without
  opening Settings, per live-show rule 9's opt-in-and-off-by-default principle.
- Rollback is a settings change, never a rebuild. If Assisted mode can only be
  disabled by reinstalling, it is not shippable.

## 10. Tests required before Go

| Level | Requirement |
|---|---|
| Unit | Authority bounds, every invariant in §8, override precedence |
| Replay | All 17 existing scenarios plus assisted-specific: duplicate timers, rapid skips, song replacement, mode change mid-playback, shutdown during a pending action |
| Integration | Helper crash, mic unplug, seek, stale generation, missing analysis |
| Hardware | Apple Silicon, real CDG and MP4 tracks, real output device |
| Live show | **Multiple complete shows in observer mode first**, reviewed proposal-by-proposal against what actually happened |

## 11. What would move this to Go

1. Install a Pro build and run real shows with `ia_instrumentation_enabled` on and
   the observer in `observer` mode.
2. Export and review the proposals against actual outcomes — specifically the
   false-early-end rate, which is the one that cuts a singer off in front of a room.
3. Re-run this audit against that data.

Until step 2 produces a reviewed sample, this gate stays **No-Go**, and the
implementation prompt in `ROADMAP.md` must not be run.
