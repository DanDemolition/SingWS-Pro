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

## Added scope

- M7 hotkeys / Stream Deck / command registry — see `docs/2.0/plan.md`.

## Next task

Prompt 9 part 1: Assisted-mode gate document (audit only). Its Go decision needs real observer logs, so it will be recorded as No-Go/pending until the consolidated macOS + show test pass.
