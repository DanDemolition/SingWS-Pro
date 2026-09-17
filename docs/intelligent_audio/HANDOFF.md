# Intelligent Audio handoff

Updated 2026-09-15.

## Done — Prompt 1 (architecture discovery)

- `docs/intelligent_audio/ROADMAP.md` — roadmap revised for Apple Silicon / macOS 15+.
- `docs/intelligent_audio/ARCHITECTURE.md` — current-state map, never-block list, insertion points, risks, event model, dependency choice, staged plan.
- `docs/2.0/plan.md` — merged with the roadmap (§6 merged sequence); mixer answered as Soundcraft Ui (not owned → virtual mixer).
- `AGENTS.md` — 2.0 platform line, roadmap pointer, built-in macOS frameworks allowed, dev-machine section rewritten, Ui-mixer-not-owned note.
- No production code changed.

## Key discoveries

- Audio samples never enter Python (libmpv bridge + BASS). The GUI thread's `update_time_left` tick is the transition controller.
- mpv has no level tap, so `_read_level_db()` is always None on the shipping engine.
- Much of roadmap Phase 1 already exists in `transition_analysis.py`.
- Logging queue is unbounded (`queue.SimpleQueue`).
- arm64 build still pins macOS 12.3.

## Baseline tests

Linux VM, Python 3.10: `test_transition_analysis`, `test_analysis_helper_transport` — 48 ran, 46 pass, 2 PyQt6 import errors (environment). **Full macOS suite not run this session.**

## Open questions

1. (Answered) Mixer is Ui24R: per-mic USB channels and network control; build against a virtual Ui24R (control simulator + simulated multichannel audio device).
2. (Answered) arm64 mpv frameworks: `native_dual_view/Frameworks/` — 59 arm64 dylibs, dated 2026-09-04 (matches the 0.4.6.7 arm64 release). Copied from the 1.x checkout; gitignored. `Frameworks 2/` and `Frameworks-intel-release/` are x86_64 (1.x only); `Frameworks-iina-current-arm64-broken/` is a known-bad attempt. 2.0 builds must use `Frameworks/` only.
3. (Confirmed) `cdg_lyrics_finished()` is missing on `MpvKaraokeTransport`; the CDG lyrics floor is inactive.
4. Is the GPL/IINA licensing question resolved for 2.0 distribution?

## Done — M0 platform floor (2026-09-15)

- `SingWS-arm64.spec` `LSMinimumSystemVersion` 15.0; `build_singws_mac_arm64.sh` verifies `--maximum 15.0`.
- `native/mpv_bridge/build_bridge.sh` defaults to arm64 / macOS 15.0 (existing prebuilt dylib unchanged until rebuilt).
- Deleted `SingWS-x86_64.spec`, `build_singws_mac_intel.sh`. `build_all.sh` is arm64-only.
- `tools/write_manifest.py` and `tools/release_version.py` arm64-only.
- `release.sh` arm64-only **and disabled** unless `SINGWS_2_UPDATE_CHANNEL_READY=1`: 1.x clients read `docs/release.json` on main and GitHub "latest", so 2.0 must not publish there until it has its own update channel.
- Tests updated: `test_release_tools.py` (17 pass in Linux VM), `test_no_gstreamer_guard.py`, `test_karafun_intel.py` (compile only; need macOS/PyQt6 to run).
- Not changed: runtime Intel branches in `0.2.18.1.py` (`platform.machine()` checks) and their tests; `constraints-macos12.txt` name. Remove in a later, focused cleanup.
- Deleted unneeded `native_dual_view/Frameworks 2`, `Frameworks-intel-release`, `Frameworks-iina-current-arm64-broken`.

## Done — SingWS Pro update channel (2026-09-15, revised)

- SingWS Pro has its own **public repo `DanDemolition/SingWS-Pro`**; 1.x stays in `DanDemolition/SingWS`.
- App: `DEFAULT_UPDATE_REPO`, `DEFAULT_UPDATE_MANIFEST_URL` (`SingWSPro/main/docs/release.json`); `_effective_update_repo()` / `_effective_update_manifest_url()` migrate 1.x values from imported settings.
- `docs/release.json` is a Pro placeholder (no downloads). The 1.x download page `docs/index.html` was removed from this tree.
- `release.sh` refuses unless origin is SingWSPro and branch is `main`; version must be 2.x; normal "latest" releases.
- 4-part versions (`2.0.0.1`…); `_version_key` ignores letters.
- Tests: `test_release_tools` + `test_legacy_import` 25 pass (Linux VM).

## Done — SingWS Pro side-by-side identity (2026-09-15, source only)

- Name **SingWS Pro**. `APP_VERSION` 2.0.0.0 (first beta via `./release.sh 2.0.0.1`).
- Data folder `~/SingWSPro` (`APP_DIRNAME`, also `song_index.APP_DIRNAME`, `phrase_markers` fallback, tool defaults). 1.x keeps `~/SingWS`.
- Bundle `SingWS Pro.app`, executable `SingWSPro`, id `com.singws.pro` (separate macOS permissions/prefs). DMG `SingWS-Pro-<ver>-arm64-installer.dmg`; updates download to `~/Downloads/SingWS Pro Updates`.
- First launch: `_offer_legacy_1x_import()` → `legacy_import.py` asks once to copy `~/SingWS` (skips logs, partial/lock files; never overwrites; never writes to 1.x; rewrites settings paths that pointed into `~/SingWS`). Warns if 1.x is running. Skipped when `SINGWS_HOME` is set.
- Tests: `test_legacy_import.py` (4) + `test_release_tools.py` (20) pass in Linux VM. App-level prompt is unverified until run on macOS.
- To do (operator wants it): **SingWS Pro branding** — Pro app icon (`SingWS.icns`/iconset), DMG background + helper art (`build_dmg_background.py`, `tools/make_dmg_assets.py`, `design/`) saying "Drag SingWS Pro".
- Decided: 1.x and Pro may both connect to wskar.com as host; that is acceptable as long as only one is actively hosting a show. No server-side arbitration needed now.

## macOS baseline — 2026-09-15 (Apple Silicon, Python 3.14, fresh `qtvenv`)

`SINGWS_SECONDARY_TEST_PYTHON=./qtvenv/bin/python ./tools/run_tests.sh` → **1035 passed, 11 failed, 7 errors, 34 subtests passed** (84.6 s). Log: `test-baseline.log` (local, not committed). The runner stops after the main pass fails, so the four mpv/PyObjC secondary modules did not run.

- Caused by the SingWS Pro rename — **fixed after the run** (not yet re-run on macOS): `test_recent_regressions::test_widget_surfaces_are_double_buffered_by_default_on_macos` (looked for `setApplicationName("SingWS")`), `test_profile_isolation::test_regular_profile_keeps_original_location` (expected `~/SingWS`).
- Environment: `test_singer_history_counts` (7 failed + 7 errors) needs the private `SingWS-Server` repo checked out at `./SingWS-Server` plus PHP.
- Pre-existing test bugs — **fixed after the run** (not yet re-run on macOS):
  - `test_rotation_tv_design`: 0.4.7.7-rc1 changed the karaoke-time backdrop check to 1000 ms; test still expected 250. Test updated.
  - `test_karafun_fullscreen`: patched `threading.Thread` globally, which broke `threading.Timer` in the (newer) delayed re-verify path. Now fakes both; failing test updated for the one re-check, plus a new test for a re-check that succeeds. 6 pass in Linux VM.

## Done — Prompt 2 / Phase 0 transition instrumentation (2026-09-15)

Passive only: no transition decision, timer, fade or playback timing changed.

- New `transition_events.py`: `PlaybackEvent` (schema v1), `EventRecorder` with a bounded ring (2048 events), one daemon writer thread flushing about every second to `~/SingWSPro/logs/transition_events_YYYYMMDD.jsonl`. **Overflow policy:** keep newest, drop oldest, write a `recorder_dropped` event with the count. `record()` does no I/O or JSON work; unknown kinds rejected; non-scalar data dropped; tracks are a 12-char SHA-1 of the path (no singer names, no audio).
- Setting `ia_instrumentation_enabled` (default **off**); checkbox "Record transition diagnostics" under Seamless transitions; applies immediately.
- Module-level `_ia_record(owner, ...)` (not a method, so lightweight test hosts never break) adds generation (increments per karaoke start), track id, media mode. Call sites in `0.2.18.1.py`:
  - `karaoke_start` — end of `_start_mpv_karaoke_transport`, **outside** the start try-block
  - `karaoke_eos` — `_on_karaoke_ended`
  - `media_end` — `_handle_media_end_safe` (trigger, early_end_reason, auto_advance, crossfade_enabled)
  - `early_end_trim` — both verified-tail exits in `_maybe_trim_end_silence`
  - `bgm_prestart`, `eos_fallback`, `stall_fallback` — `update_time_left`
  - `bgm_prefire_verified` — `_prefire_bgm_at_verified_audio_end`
  - `bgm_fade_in` — `BackgroundMusicPlayer.fade_in` (after its guards)
  - `manual_stop` — `stop_playback`; `manual_seek` — `_karaoke_seek_seconds`
  - `gui_stall` — `_perf_log_if_slow("ui_update_time_left")` over threshold
  - recorder closed in `_on_app_about_to_quit`
- Not captured yet (not available without new code): final CDG lyric timestamp (`cdg_lyrics_finished()` still missing), BASS/mpv underrun counters, actual audible BGM start time.
- `transition_events.py` added to the spec helper list.
- macOS run 2026-09-15: 1052 passed; only new failure was `test_performance_safety::test_scanned_tail_ends_promptly_without_waiting_for_graphics` (SimpleNamespace host had no `_ia_record` method) → helper moved to module level. Singer-history failures are environment.
- Tests: new `test_transition_events.py` (14): disabled no-op, no I/O on the caller thread, ordering/schema/filtering, overflow, close/flush, write failure, concurrent producers, and static contracts (default off, helper only touches the recorder and swallows errors, every call is a bare statement so results can't drive decisions, shutdown closes). Pass in Linux VM; **macOS run pending**.
- Rollback: turn the setting off (no events); full revert = remove `transition_events.py`, its import, `_ia_record` and the listed call sites.
- Still needs real hardware/shows: confirm no measurable GUI-tick cost with logging on; review a real show's JSONL.

## Done — Prompt 3 / Phase 1 deterministic cue candidates (2026-09-15)

Most of Phase 1 already existed in `transition_analysis.py` (versioned `TransitionAnalysis` records keyed by path+mtime+size, audio edges from the scan envelope, fade estimate, CDG/MP4 visual end, isolated helper, JSONL checkpoints). This session added the missing cue layer **without touching that cache or its version**.

- New pure `transition_cues.py`: `derive_cues(record) -> TrackCues` (`CUE_ANALYZER_VERSION = 1`) with audible start/end, final lyric (CDG/MP4 visual end) + confidence, **safe BGM entry**, **safe early end**, outro class, per-cue confidence, reason codes, analyzer + analysis versions, computed_at.
- **Lazy upgrade by design:** cues are recomputed from whatever record exists, so improving the cue policy never marks `transition-analysis.json` stale or forces a rescan. (Bumping `TRANSITION_ANALYSIS_VERSION` would invalidate every record and silently disable existing early-tail behavior. Don't do it for cue changes.)
- Policy (constants in the module): lyrics outrank silence (silence alone never proposes early end); early end = max(audio_end, final_lyric) + 0.5 s, only with visual confidence ≥ 0.85, track ≥ 20 s, and ≥ 1 s saved; karaoke BGM entry only after a verified audio end with ≥ 1 s dead tail; BGM tracks enter at audible start.
- Diagnostics only: `python tools/inspect_transition_cues.py [--match TEXT] [--json]` reads `~/SingWSPro/transition-analysis.json` read-only. Nothing in the app calls `derive_cues`; no playback change, no UI.
- Performance: 130,000 records derived in 0.69 s (Linux VM, pure Python). No I/O per record.
- Tests: `test_transition_cues.py` (12): normal ending, lyrics after instrumental, silence-only, low confidence, no gain, very short, absent audio, corrupt/old metadata, BGM, purity, no playback/Qt imports, CLI reads without writing. Pass in Linux VM; macOS run pending.
- Not done / gaps: karaoke envelopes are not persisted (`to_dict` drops them for size), so no stored start/end energy snippets; `cdg_lyrics_finished()` on the transport is still unimplemented and intentionally left alone (implementing it would activate the existing lyric floor in live early-trim = behavior change; do it in Phase 3 behind its gate); schema/database migrations were not needed.
- Rollback: delete `transition_cues.py`, its test and the tool. No data or app code depends on them.

## Done — Prompt 4 / Phase 2 observer mode + replay harness (2026-09-16)

Observer proposes and logs; it has **no path to playback**.

- New `transition_observer.py`: `TransitionObserver(sink)` with inputs only (`song_started`, `position`, `seeked`, `bgm_started`, `song_ended`) and modes `off` / `observer` (Automatic does not exist). Proposes `bgm_fade_in` at `safe_bgm_entry` and `end_karaoke` at `safe_early_end` once per song, only at cue confidence ≥ 0.8; suspends after a manual seek; ignores stale generations and backwards monotonic time; emits `observer_proposal` and `observer_compare` (actual BGM start / song end vs proposal: `delta_s`, `saved_s`, `trigger`, `manual`, `seeked`). Sink errors are counted, never raised.
- App wiring (`0.2.18.1.py`): module-level `_IA_OBSERVER` and `_ia_observe(owner, what, ...)` fed from karaoke start (cues via `transition_analysis_cached` → `derive_cues`; no decode, None while the cache loads), `update_time_left` position, `_karaoke_seek_seconds`, `_handle_media_end_safe`, `stop_playback`, `BackgroundMusicPlayer.fade_in`. Active only when **Record transition diagnostics** is on and `transition_observer_mode` is `observer` (default). No UI yet; logs go to the same `transition_events_*.jsonl`.
- Replay harness: `transition_replay.py` (JSON scenarios: record + events + expect/forbid), fixtures in `test_fixtures/transition_replay/` (8: normal CDG, lyrics after instrumental, silence only, no cache, seek, manual stop, stale events, BGM delta), CLI `python tools/replay_transitions.py [--verbose]`.
- Tests `test_transition_observer.py` (11): all fixtures; each proposal once; off mode silent; backwards clock; sink failure; low confidence; observer imports whitelist; public API inputs-only; **app feed exercised against a Mock owner whose methods are spies → zero method calls**; every `_ia_observe` call is a bare statement; event kinds registered. 37 IA tests pass in Linux VM; macOS pending.
- Not captured: worker crash/queue overflow for the observer itself — it runs synchronously in O(1) on the existing tick, with recorder overflow already handled by Phase 0. Actual audible BGM start is approximated by `fade_in` time.
- Needs live observation: several full shows with diagnostics on, then review proposal vs actual deltas.
- Rollback: set `transition_observer_mode` to `off`; full revert = remove `transition_observer.py`, `transition_replay.py`, fixtures, tool, `_IA_OBSERVER`/`_ia_observe` and its call sites.

## Done — Prompt 5 / classifier feasibility spike (2026-09-16, built, not measured)

- Experimental SoundAnalysis probe (Swift CLI), safe Python client, labelled-clip benchmark under `experimental/sound_analysis/` (not imported by the app, not bundled; build output gitignored).
- `sound_classes.py` + `test_sound_classes.py` (9 pass in Linux VM): Apple label → SingWS class mapping and single-window scoring.
- `docs/intelligent_audio/MODEL_EVALUATION.md`: options ranked, M1 budgets, accuracy targets, run steps, Go/No-Go rule. **Current decision: No-Go (unmeasured).**
- Operator decision 2026-09-16: build everything first, then test and check it all at once. So gates are recorded as *pending* rather than blocking the build, and nothing past this point gets playback authority until those checks pass.

## Done — Prompt 6 / classifier into Observer only (2026-09-16, built, untested on macOS)

Built ahead of the Prompt 5 Go decision at the operator's request; **`ia_sound_classifier_enabled` defaults off**, and nothing here has playback authority.

- `native/sound_helper/SingWSSoundHelper.swift` + `build.sh`: separate process. **Core Audio process tap** (`CATapDescription(monoMixdownOfProcesses:)` → private aggregate device → IOProc) on the SingWS Pro PID, classified by Apple SoundAnalysis `.version1`. JSON protocol: hello (model id, protocol 1) / window / heartbeat (windows, dropped, rss) / error. Bounded: at most 8 buffers queued for analysis; older audio dropped and counted. Exits on stdin EOF (never outlives the app). `--stdin` mode for tests. Captures only; records nothing. **Not compiled or run yet** (the Linux VM can't build Swift for macOS).
- `sound_monitor.py`: launches the helper **only while armed** on a daemon thread at nice 10. GUI-safe O(1) `arm`/`disarm`/`latest`; keeps the newest 8 windows with a sequence id; a window older than 2.5 s is never returned. Health: heartbeat timeout (3 s), error line, crash, >50 malformed lines, RSS > 200 MB, or CPU > 15 % for ~3 s → kill and restart; 3 failures → **bypassed for the session**.
- Observer (`transition_observer.py`): `listening_window()` (15 s before the earliest cue, not after a seek), `sound()` with hysteresis (3 vocal windows at ≥ 0.6 to turn "program vocal" on, 3 non-vocal off; `uncertain` changes nothing), `sound_unavailable()`. While program vocal is active at the early-end cue it proposes `hold` instead of `end_karaoke`, then proposes the end once vocals stop. Emits `observer_evidence` on changes only; the monitor emits `sound_health`.
- App: `_IA_SOUND`, `_ia_sound_monitor()`, `_ia_sound_configure()`; `_ia_observe` arms/disarms by listening window, feeds each window once (seq dedupe), disarms on seek/end, shuts down on quit. Helper path: bundle `_MEIPASS` or `native/sound_helper/`. Spec bundles the helper if built and adds `NSAudioCaptureUsageDescription`.
- Note: a process tap hears the whole SingWS Pro mix (karaoke + BGM + soundboard). Near the song end that's mostly karaoke, and the observer only listens there, but BGM pre-start overlap can add music. Recorded as a known limitation.
- Tests: `test_sound_monitor.py` (14, real subprocesses with `test_fixtures/sound_helper/fake_helper.py`: normal, disarm, stale TTL, crash → bypass, error line, missing heartbeat, malformed flood, RSS budget, missing helper, shutdown, launch failure, non-blocking GUI calls, default-off/permission/imports contracts); observer +3 sound tests, updated API and app-feed spy test (monitor mock; still zero playback calls); replay fixtures 09–11 (guide vocal holds end, uncertain changes nothing, classifier loss falls back). 63 IA tests pass in Linux VM.
- macOS checks pending: build helper; first-run capture permission prompt; tap works on the actual output device (incl. USB mixer); CPU/RSS within MODEL_EVALUATION budgets; helper killed cleanly on quit and sleep/wake; signing/notarization with the helper bundled.
- Rollback: setting off (no helper ever launched); full revert = remove helper, `sound_monitor.py`, observer sound methods, `_IA_SOUND` wiring and spec entries.

## Done — Prompt 7 / live mic inputs design + diagnostic prototype (2026-09-16, built, untested on hardware)

- Design: `docs/intelligent_audio/USB_INPUT_DESIGN.md` (Ui24R primary, Signature 10 backup, feedback prevention, UID identity, mapping, same-device clocking, loss/reconnect, permissions, states, manual checklists for both mixers).
- `native/sound_helper/SingWSMicMeter.swift` (built by `build.sh`, bundled if built): `--list-devices`; `--device UID --channels …` → per-channel RMS/peak dB every 100 ms, heartbeat, `device_lost` / `format_changed` (then exits), stdin-EOF exit. Levels only; no audio output, no recording.
- `mic_config.py` (presets ui24r / signature10 / signature22mtk / custom, validation, settings round-trip), `mic_activity.py` (per-role silent / active / sustained / clipping / suspect_noise / stale with hysteresis; calibration), `mic_monitor.py` (`MicMonitor` on `SoundMonitor` supervision; unplug and format change are **transient** retries that drop evidence immediately; `list_input_devices`). `sound_monitor.py` gained `_command` / `_on_row` / `_is_transient` hooks (behavior unchanged; its 14 tests still pass).
- `mic_diagnostics_dialog.py`: Settings → **Live Mic Inputs…**: feedback/privacy warning, enable checkbox, mixer preset, device list (worker thread), role → channel spinboxes, Start/Stop Meters, meters and state text, 3 s quiet calibration, Save. Meters never start on open; closing stops the helper.
- Settings `ia_mic_awareness_enabled` (false), `ia_mic_config`. Spec: `NSMicrophoneUsageDescription`, mic modules and helper bundled; entitlements add `com.apple.security.device.audio-input`.
- **Not wired into the observer** (asserted by test); that's Prompt 8.
- Tests `test_mic_input.py` (18): config presets/validation/round-trip, activity hysteresis/sustain/close/noise/clip/stale/calibration/roles, monitor with a fake meter (levels → roles, unplug resumes, format change drops evidence, bad config never launches, device listing), safety contracts (default off, permissions, import whitelist, no recording APIs in Swift, dialog doesn't auto-start, not wired to decisions). Pass in Linux VM. The dialog itself needs macOS/Qt to run.
- Rollback: leave the setting off / don't open the dialog; full revert = remove the mic files, the settings button and handler, spec/entitlement entries.

## Done — Prompt 8 / live mic evidence in observer decisions (2026-09-16, built, untested on hardware)

All modes advisory; still no playback authority.

- Observer (`transition_observer.py`): mic modes `off` / `observe` / `singer_protection` / `singer_protection_host_ducking` (`set_mic_mode`); `mic(roles, t_mono)` and `mic_unavailable(reason)`. Debounced presence per group with monotonic timestamps: on after 0.5 s continuous activity, off after 1.2 s quiet (minimum hold). Singers = singer1/singer2/singers (either duet mic counts); host = host. A `suspect_noise` channel is **untrusted** until it goes silent. All-stale reports or unavailability drop evidence immediately.
- Proposals: singers active at the BGM cue → `hold_bgm`; at the early-end cue → `hold` (`singer_mic_active`); both proposed once the mics go quiet (`after_hold`). Host active at the BGM cue → `bgm_fade_in_low`. Between songs / song tail: host on → `bgm_duck`, off → `bgm_raise`. `song_end` compare adds `singer_mic_at_end` / `host_mic_at_end`. Evidence events: `singer_mic`, `host_mic`, `mic` (untrusted / trusted / unavailable).
- App: `_IA_MIC`, `_ia_mic_configure(settings)` (session-long `MicMonitor` when `ia_mic_awareness_enabled` and config valid; restarted on dialog Save), `_ia_mic_tick()` from a new `_ia_observe(self, "tick")` at the top of `update_time_left` (O(1), once per report; state/reason changes → `mic_unavailable`). Setting `ia_mic_observer_mode` (default `singer_protection_host_ducking`); the dialog gained a "Transition suggestions" mode picker.
- Replay fixtures 12–17: held note holds BGM and end, duet second singer keeps the hold, host talks after the performance (low entry → raise → duck), noise burst and feedback channel ignored, unplug mid-hold, stale and silent channels. 17/17 pass.
- Tests: 46 across mic/observer/events pass in Linux VM (API whitelist and mic-feeds-observer-only contract updated).
- Gap: no main-show-screen mic status indicator yet. Status is in the Live Mic Inputs dialog and `sound_health` log events. Add it with the M6 UI work, or sooner if wanted.
- Rollback: `ia_mic_awareness_enabled` false (no helper) or `ia_mic_observer_mode` off.

## Decisions and dependency upgrade — 2026-09-16

All three `docs/2.0/plan.md` open questions are now closed:

- **Distribution:** SingWS Pro and 1.x coexist **permanently**; 1.x is the free
  edition, maintained indefinitely on Intel/older macOS — not "about a year".
- **Licence:** Pro is **open source and free**; monetization is online-only
  features, resolved separately by the operator. GPL source obligation is
  satisfied by construction and BASS's free tier applies. Remaining item is
  technical, not commercial: GPL mpv linked against proprietary BASS is
  incompatible regardless of price. An LGPL libmpv rebuild is recorded as
  **cleanup, not a blocker** — verify CDG/MP4 playback survives it.
- **Stems (MS3):** whole-library separation is impossible (~130,824 tracks
  ≈ 2.6 TB vs ~175 GB free), so it is per-song. The operator needs **instant
  stems** for last-minute requests and instrumental-on-demand; both decide
  *before* the song starts, so **no mid-song swap** is in scope and **2 stems
  suffice**. Design: progressive look-ahead (~1–2 s to first audio), mandatory
  fallback to the original audio if the separator falls behind, mixed through
  the existing BASS deck engine.

Milestones renamed `M0–M7` → `MS0–MS7` so they stop colliding with Apple
silicon model names (an "M1"/"M3" ambiguity that actually caused confusion).

**Qt 6.9.1 → 6.11.0** (PyQt6 6.11.0 / PyQt6-Qt6 6.11.2), numpy 2.5.1 → 2.5.3,
scipy 1.18.0 → 1.18.1. `constraints-macos12.txt` → `constraints-macos15.txt`;
`build_singws_mac_arm64.sh` follows it and its macOS 12 wording is corrected.
Qt was the **only** package the old macOS 12 floor held back ("6.10.0+ requires
13.0"); the others were already current. All 99 Qt 6.11 arm64 binaries verify at
minos <= macOS 15.0.

**Suite is identical on 6.9.1 and 6.11.0: 1129 passed, 51 subtests, 0 failures**
(`tools/run_tests.sh`, M1 Max, macOS 27).

**Rendering is NOT verified and cannot be by this suite** — live-show rule 4.
The 6.9 → 6.11 jump can move native NSView stacking, Qt Quick surfaces and
window geometry, all of which have regressed on this stack before. Build the app
and look at it before trusting Qt 6.11. No build was made.

### Test environment notes (2026-09-16)

- `tools/run_tests.sh` runs **pytest**, not bare `unittest`; AGENTS.md said
  otherwise and a correct venv still failed to start.
- The long-standing "no venv can construct a QApplication" claim blamed a
  PyQt6/PyQt6-Qt6 version split. **Wrong cause.** `/Users/daniel/Documents/SingWS/.venv`
  is a *copied* venv whose `sys.prefix` resolves to `.venv-repair`, so Qt gets an
  empty plugin path whatever the versions are. A fresh `qtvenv` works in both
  offscreen and cocoa — and 6.11.0 bindings against 6.11.2 frameworks work fine,
  which is a version split.
- **Resolved.** The secondary native stage wanted a `.venv-universal` that does
  not exist here, so `run_tests.sh` exited 1 even on a green run — and
  `release.sh` gates on that. It now defaults to `.venv-native`, exports
  `DYLD_FALLBACK_LIBRARY_PATH` for python-mpv, and the whole runner **exits 0**:
  1129 passed + 51 subtests, then 86 native passed.
- **`mutagen` was the real cause of the long-standing `test_phrase_detect`
  failure**, not the environment in the vague sense AGENTS.md claimed.
  `media_helpers.probe_duration_seconds()` swallows the ImportError and returns
  0.0 for every file, which silently disables `detect_trailing_silence()` (0.0
  is its deliberate fail-safe, so nothing raises). Shipped builds are unaffected
  — the spec lists mutagen as a hiddenimport — but it was missing from the
  documented venv recipe and from the pin set. Both fixed.
  Worth a follow-up: that bare `except Exception` turns a missing dependency
  into silently degraded end-of-song detection rather than a loud failure.

## Added scope

- M7 hotkeys / Stream Deck / command registry — see `docs/2.0/plan.md`.

## Done — Prompt 9 part 1 / Assisted-mode gate audit (2026-09-16)

`docs/intelligent_audio/ASSISTED_MODE_GATE.md`. **Decision: NO-GO**, as the
roadmap requires when evidence is incomplete — and it is entirely absent. The
audit is meant to read a representative sample of exported observer logs; there
are none anywhere under `~/SingWS` or `~/SingWSPro`, because until 2026-09-16
no build of SingWS Pro existed that could run, so the observer has never watched
a song. The 17 replay scenarios pass, but they prove the logic matches its design,
not that the design matches a real room.

The document records the criteria anyway so data collection can be aimed at them:
approved scope limited to dead-air removal after lyrics **and** audio have ended;
classifier and mic evidence may veto an action but never justify one; lyrics
outrank silence unconditionally; the observer's no-playback-access property is
not relaxed (Assisted must be a separate consumer, not the observer calling the
transport); rollback must be a settings change, never a rebuild.

**Do not run the Prompt 9 implementation prompt.** It stops on a No-Go gate by
design. What moves it to Go: install a Pro build, run real shows in observer mode,
review proposals against outcomes — especially the false-early-end rate, the one
that cuts a singer off in front of a room — then re-run the audit.

## First SingWS Pro build (2026-09-16)

The app had never been built from this repo. It builds, launches and renders now:
Qt 6.11.0, 903 ms startup, host window correct, audience ticker drawing above the
video surface, no errors and no crash report. Scratch `SINGWS_HOME`; nothing
touched `/Applications` or 1.x data. DMG `SingWS-Pro-2.0.0.0-arm64-installer.dmg`.

Build blockers fixed: the mpv bridge dylib and both Swift helpers had never been
compiled; `tools/verify_macos_arch.py` failed because Python 3.11+ sets
`sys.path[0]` to the script's own directory, so repo-root native modules were not
importable.

Two runtime dependencies were silently missing from the bundle, both the same
shape — a lazy import, a quiet fallback, and no test touching the real dependency:

- **mutagen** (fixed earlier): `probe_duration_seconds` returned 0.0 for every
  file, disabling trailing-silence detection.
- **CoreLocation**: imported lazily inside the venue-location helper, so
  PyInstaller never saw it. The frozen app took the "CoreLocation is not
  available" branch every time. Now a spec hiddenimport and pinned.

**Worth a follow-up:** that pattern has now bitten twice in one day. These
fallbacks should log loudly, not silently.

Separately, the **1.x show app is not affected** by the CoreLocation fix — it
already bundles it. Its logs show `authorization_status=0` /
`kCLErrorDomain Code=1`: macOS has never authorized it for location. That is an
operator grant in System Settings, and ad-hoc re-signed rebuilds do not inherit it.

Found and not fixed: the rotation card clips "No singer is active" and "Queue a
song to start the rotation." at a 1192 px window width — plain QLabels with no
wrap or elide. Text metrics are identical on Qt 6.9 and 6.11, so it is a
pre-existing layout squeeze, not a Qt regression.

## Done — Prompt 10 / vocal-effects architecture (2026-09-16, design only)

`docs/intelligent_audio/VOCAL_EFFECTS_ARCHITECTURE.md`. No effects code written.

Two corrections to the roadmap brief, both deliberate:

- **Hardware order reversed.** The brief describes a "Primary Signature 10 signal
  path"; Prompt 7 already decided Ui24R primary / Signature 10 backup, and that
  still holds. The Signature 10 is designed for as a first-class fallback because
  it is the mixer that actually exists — the Ui24R is still not owned.
- **The render callback cannot be Swift.** The brief says "Swift helper using
  AVAudioEngine". The process should be Swift; the callback must not be — ARC can
  retain/release, allocate and lock on any object touch. Recommendation is a HAL
  I/O AudioUnit with a C render callback, hosting Apple AUs via `AudioUnitRender`.
  AVAudioEngine may build the graph but must not own the callback.

Design summary: wet-only return, so the dry voice never passes through the
computer and a helper crash costs the effect, never the vocal. Separate process
and — importantly — its **own supervisor instance**, not `SoundMonitor`'s, so a
classifier stall cannot couple into the effects failure counter. Parameters cross
into the callback as an atomically published immutable snapshot. Apple AUs cover
reverb, delay and EQ; **macOS ships no chorus AU**, so chorus/doubling needs a
small modulated delay line of our own (~40 lines of C, no dependency).

The latency finding that drives the plan: wet sums acoustically with dry, so
round-trip delay is a fixed comb filter on the combined signal. Reverb and
slapback are indifferent to that; **chorus/doubling may be unusable** and is
gated on a by-ear validation on real hardware. Stages: VFX0 passthrough (unity
gain, measured round-trip) → VFX1 reverb → VFX2 supervision → VFX3 delay+tempo
→ VFX4 ducking → VFX5 chorus (may be abandoned).

**Signing problem, stated rather than deferred:** the brief asks for hardened
runtime and notarization; the build ad-hoc signs and does not notarize. Today's
location finding is the proof of consequence — TCC grants do not survive ad-hoc
re-signed rebuilds, and an effects helper needs microphone permission. Resolve
signing before effects reach a show. Not a blocker for VFX0.

Seven decisions need real hardware and none should be guessed; they are listed in
§12 of the document.

## Done — Prompt 11 / VFX0 passthrough + VFX1 reverb (2026-09-16, not run on audio hardware)

`native/vocal_fx/` (`vfx_dsp.c` real-time core, `vfx_engine.c` Core Audio,
`SingWSVocalFX.swift`, `build.sh`) and `vocal_fx.py`. Helper builds arm64; the
spec bundles it when present. Suite: **1145 passed + 51 subtests, then 86 native,
runner exits 0.**

The callback is C, as the architecture requires, and that is enforced
structurally rather than by intent: `build.sh --test` fails if the real-time
core's object file references any allocator, lock or I/O symbol.

Verified without hardware — 13 signal tests plus 16 Python tests: starts
bypassed and silent; **unity gain is bit-exact passthrough** (the VFX0
deliverable); bypass fade never rises, reaches true silence, and its first
buffer is not a hard mute; gain glides without a step; the limiter holds full
scale; device loss and format change are transient and never counted as
failures; repeated crashes bypass for the session; a restart restores the
operator's setting instead of silently staying bypassed; `vocal_fx.py` cannot
import any playback module and two monitors cannot share a failure counter.
Real error paths were exercised: unknown device and input-only device both give
structured errors and correct exit codes, no crash.

**The stream has never run, and could not be run here.** This Mac's built-in mic
and speakers are separate Core Audio devices; full duplex needs one device
carrying both. No aggregate device was created on the operator's machine to fake
it. So the audio path itself is unproven: round-trip latency, 64-frame
stability, reverb by ear, overload under load, and unplug-during-playback are
all unmeasured. **The latency the helper reports is arithmetic from the device's
advertised figures, not a measurement** — treat it as a starting guess.

A manual Signature 10 checklist with volume-down safety steps is at the end of
`VOCAL_EFFECTS_ARCHITECTURE.md`. Step 1 is both Aux knobs on the USB return
channel fully down; that is the feedback route and the only thing preventing a
howl. Amps stay down until step 6 confirms there is none.

Not done deliberately: no Settings UI and no app wiring. Prompt 11 asks for the
helper, passthrough, bypass, diagnostics and one effect; a settings panel is
MS6/M7 work and would have been scope creep. `vocal_fx.is_enabled()` returns
False for absent or malformed settings, so effects are off until something
explicitly turns them on.

Rollback: delete nothing — the helper is a separate optional binary the spec
bundles only if built, and the app has no code path that starts it yet.

## Done — Prompt 12 / final integration audit (2026-09-16)

`docs/intelligent_audio/FINAL_AUDIT.md`. Every claim cites a symbol or test.

**Verdict: not production-ready**, and the gap is evidence rather than code. The
observer has watched zero songs and the effects stream has carried zero audio, so
the two newest subsystems are written but unexercised.

Passed, with citations: isolation by failure domain; no allocator, lock or I/O
symbol reachable from the real-time core (enforced by `build.sh --test`, not by
review); bounded queues with a counted drop policy; stale generations and
backwards clocks rejected; device loss transient everywhere; mic audio never
persisted; model and analyzer versions stamped and checked; Off restores legacy;
all three Swift helpers arm64 in the bundle; settings migrations additive.

The strongest result is that the observer's lack of playback authority is
**structural** — `ast`-based tests assert it imports nothing that can play and
exposes inputs only. `vocal_fx.py` carries the same guard. Assisted mode cannot
exceed its authority because it does not exist; `MODES` is `("off","observer")`.
Note that "human controls always win" is currently true *by absence* and must be
re-audited the moment Assisted exists.

**One defect found and fixed:** `vfx_engine_destroy` disposed its audio units but
never deregistered the two Core Audio property listeners installed in `create`,
leaving Core Audio holding a pointer to freed memory — reachable via `create`'s
own failure path. Same class as the live-show rule 9 watchdog use-after-free, and
invisible to every test because the stream has never run. No regression test was
added: reproducing it needs a live device property change, which is exactly the
capability this audit says is missing. Recorded as a gap rather than papered over.

**Release blockers:** (1) no observer evidence, so the assisted gate stays No-Go;
(2) ad-hoc signing with no hardened runtime and no notarization — today's location
finding proved TCC grants do not survive ad-hoc rebuilds, and the mic/effects
helpers need microphone permission; (3) the effects audio path is unproven, so
VFX0's own deliverable does not exist; (4) the logging queue at `0.2.18.1.py:2649`
is still an unbounded `SimpleQueue`; (5) `vfx_*` keys are absent from `DEFAULTS`,
so effects settings would not persist once a UI lands.

## MS1 key detection — built, measured, and gated off (2026-09-16)

`key_detect.py` (20 tests) and `tools/validate_key_detect.py`. **The detector
does not work on real audio and is disabled**: `VALIDATED = False`, and
`is_usable()` returns False unconditionally, so MS2/MS4/MS5 cannot act on a key
even if written before the fix lands. A test asserts that, so turning it on is a
deliberate change.

The durable result is the harness. Calibration looked like it needed someone to
name keys by ear; it does not. The harness pitch-shifts real tracks by a known
amount and requires the tonic to move by exactly that much — the shift is the
ground truth. Measured: **shift tracking 4/15, octave invariance fails** (±12
semitones preserves every pitch class, yet G major read as E minor and C major).
Synthetic tests pass, so the method works on clean tones and fails on real mixes;
the chroma responds to spectral tilt rather than pitch content. One fix attempt
(band-limited, log-compressed, whitened chroma) improved octave invariance to 5/6
but dropped shift tracking to 1/15, so tuning was stopped rather than continued
against a three-track sample.

Speed is not the problem: 0.44 s/track, about 4 h for the 130,824-track library
on the existing four workers.

## Next task

Prompts 9 part 1 (No-Go), 10 (design) and 11 (VFX0/VFX1 code) are done.

**All twelve roadmap prompts are complete.** What remains is not prompt work.

Two operator tasks, in this order, because everything else waits on them:
1. **Install the Pro build and run real songs** with `ia_instrumentation_enabled`
   on and the observer in `observer` mode; collect the logs. This alone unblocks
   `ASSISTED_MODE_GATE.md` and converts the largest unverified block into evidence.
2. **Work the Signature 10 checklist** (end of `VOCAL_EFFECTS_ARCHITECTURE.md`),
   amps down, and record the measured round-trip latency.

The highest-value code task meanwhile is **moving off ad-hoc signing** (blocker 2).
It is the one item that will otherwise keep re-breaking permissions on every build,
and it has already cost one investigation.

Remaining milestone work from `docs/2.0/plan.md` is unrelated to Intelligent
Audio: MS1 key detection, MS2 transitions, MS3 stems, MS4 pitch/key/tempo UI,
MS5 mixer control, MS6 host UI overhaul, MS7 command registry/hotkeys/Stream Deck.

In parallel, the only thing that unblocks Prompt 9 is show data: install the Pro
build and run real songs with `ia_instrumentation_enabled` on.
