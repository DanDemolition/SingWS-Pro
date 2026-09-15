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

## Added scope

- M7 hotkeys / Stream Deck / command registry — see `docs/2.0/plan.md`.

## Next task

Run `test_transition_events.py` plus the full suite on macOS; then Prompt 3 (Phase 1 deterministic analysis — much already exists in `transition_analysis.py`). Do not begin Phase 1 until Phase 0 is verified on macOS.
