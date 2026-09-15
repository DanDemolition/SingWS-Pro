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

## Added scope

- M7 hotkeys / Stream Deck / command registry — see `docs/2.0/plan.md`.

## Next task

Run the full macOS test suite (see AGENTS.md) to record a real baseline, then design the 2.0 update channel, then Prompt 2 (Phase 0 instrumentation).
