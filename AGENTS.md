# AGENTS.md

## Project purpose

This repository contains SingWS, a Python/PyQt karaoke player application.

The long-term goal is to improve the playback engine while preserving the existing UI and behavior wherever possible.

Current engineering direction:
- Keep the application as a Python/PyQt app.
- Add native modules only where required for realtime audio performance.
- For pitch/time processing, prefer open-source and free solutions.
- Do not introduce paid/proprietary SDK dependencies. Built-in macOS frameworks (e.g. SoundAnalysis, AVAudioEngine, Core Audio, Core ML) are allowed.
- Favor Signalsmith Stretch for realtime pitch/time DSP work.
- Target macOS first unless the task explicitly says otherwise.
- SingWS 2.0 targets Apple Silicon on macOS 15+ only; Intel and older macOS stay on 1.x.
- 2.0 ships as **SingWS Pro**: `SingWS Pro.app`, bundle id `com.singws.pro`, data in `~/SingWSPro`, 4-part versions starting 2.0.0.x, own public repo `DanDemolition/SingWS-Pro` (`main`, `docs/release.json`); never publish to `DanDemolition/SingWS`, which is 1.x. Never read or write `~/SingWS` except the one-time read-only import in `legacy_import.py`.
- Intelligent Audio / vocal effects: the current plan is `docs/intelligent_audio/ROADMAP.md`. The copy in `SingWS_Intelligent_Audio_Package for SingWS 2.0/` is the original reference version (includes superseded Intel requirements) — do not follow it.

---

## High-level architecture rules

1. Python/PyQt owns:
   - UI
   - user interaction
   - views
   - app flow
   - non-realtime logic

2. Native code owns:
   - realtime audio processing
   - low-latency transport
   - sample-accurate timing
   - pitch/tempo DSP
   - output-device interaction if needed

3. Do not move UI logic into C++.

4. Do not rewrite the entire app to force a new architecture.

5. Preserve existing behavior unless the task explicitly asks to replace it.

6. Prefer adapter-style integration over invasive rewrites.

---

## Working style

Always work in small, reviewable steps.

For any non-trivial task:
1. Inspect the repo first.
2. Explain findings briefly.
3. Identify exact insertion points before editing.
4. Propose the smallest safe change.
5. Implement only that change.
6. Stop and report:
   - files changed
   - what was done
   - how to build/test
   - blockers / assumptions
   - next recommended step

Do not do a giant rewrite in one pass.

Do not silently refactor unrelated code.

Do not remove old code paths until the replacement path is proven and testable.

If a feature is risky, add it behind a flag, toggle, or alternate path first.

---

## Planning rules

For complex tasks, plan before coding.

When the task is architectural, ambiguous, or spans multiple files:
- inspect first
- summarize current structure
- produce a short implementation plan
- wait to execute the plan unless the user asked for direct implementation

When a task is large, break it into milestones.

Preferred milestone order for playback-engine work:
1. repo inspection
2. native module scaffolding
3. basic native playback path
4. transport timing
5. Signalsmith integration
6. pitch change
7. tempo change
8. seek/reset/preroll
9. Python UI wiring
10. video sync refinement
11. cleanup / tests / docs

---

## Audio-engine rules

The transport is the source of truth.

Audio is the master clock.
- Video must follow audible playback time.
- UI timers should follow engine-reported time, not guessed wall-clock timing.
- Decoder position is not the same as audible output time.

When implementing pitch/tempo DSP:
- keep pitch and tempo as separate controls
- allow key change without changing tempo
- allow tempo change without changing key
- expose both transport time and audible time to Python if possible

When implementing seek:
- reset DSP state on seek
- clear stale buffered state
- use preroll when required by the DSP engine
- avoid clicks/glitches during seek transitions when practical

When implementing latency-sensitive logic:
- prefer predictable timing over clever abstraction
- document any latency compensation clearly

---

## Signalsmith-specific guidance

Use Signalsmith Stretch as the preferred open-source pitch/time engine unless the task explicitly says otherwise.

Integrate Signalsmith behind a small backend interface so the DSP engine can be swapped later if needed.

Do not spread DSP-specific logic all over the codebase.

Preferred wrapper shape:
- NativeAudioEngine
- StretchProcessor / DSP adapter
- transport state separated from UI state

If Signalsmith is not yet integrated:
- first build a stub native engine with the final intended Python-facing API
- then wire real playback
- then add DSP
- then refine timing/sync

---

## Python/native bridge rules

Preferred bridge: pybind11.

Expose a small stable Python API.

Preferred Python-side engine surface:
- load(path)
- play()
- pause()
- stop()
- seek_seconds(value)
- set_pitch_semitones(value)
- set_tempo_ratio(value)
- current_transport_time()
- current_audible_time()

Do not expose unnecessary low-level C++ details to the Python UI.

Keep the Python-facing API clean and easy to test.

---

## Build and dependency rules

Minimize new dependencies.

Before adding any dependency:
- explain why it is needed
- explain whether it is runtime or build-time only
- prefer well-supported, lightweight, open-source dependencies

Do not add paid SDKs.

Do not add platform-specific complexity unless needed for the current milestone.

Target macOS first for native playback work, but avoid locking the codebase into unnecessary macOS-only abstractions when a thin platform layer would work.

---

## Code change rules

Make the smallest safe edits first.

Prefer:
- narrow diffs
- clear naming
- comments only where they add real value
- straightforward control flow

Avoid:
- broad file moves
- cosmetic cleanup unrelated to the task
- renaming large surfaces without strong justification
- speculative abstractions

If changing an existing file:
- preserve style already used in that file unless it is clearly harmful
- avoid mixing unrelated cleanup with functional changes

---

## Testing and verification

After each milestone, provide:
1. what changed
2. exact files changed
3. exact commands to build
4. exact commands to run
5. how to verify success
6. what is not finished yet

When possible, add the smallest practical verification:
- smoke test
- import test
- build test
- basic playback test
- simple regression test

Do not claim something works unless it has been verified or clearly marked unverified.

Be explicit about assumptions and blockers.

---

## This is live show software

SingWS runs paying shows in front of rooms of people. A regression here is not a
failed test, it is a KJ standing in front of an audience with a black screen.
The rules below were written after 2026-08-09, when a night of changes shipped
without ever being launched broke a live show.

### 1. Find out what is actually running before diagnosing anything

The source tree is not what the operator is using. Check the installed bundle
first, every time:

    defaults read /Applications/SingWS.app/Contents/Info.plist CFBundleShortVersionString
    ls -ld /Applications/SingWS.app          # when it was installed

The version string is not enough — several builds share one version. Confirm
which *code* is in the bundle. PyInstaller zlib-compresses the frozen bytecode,
so grep finds nothing; decompress and search for a marker unique to the change
(try all four zlib headers: `\x78\x01`, `\x78\x5e`, `\x78\x9c`, `\x78\xda`).
For a bundled dylib, compare the code section, which codesigning does not alter:

    otool -s __TEXT __text <dylib> | tail -n +3 | shasum -a256

On 2026-08-09 a fix was diagnosed, blamed, and reverted while the operator was
running a build that never contained it.

### 2. A build that is not installed proves nothing

Never reason about tree code while the operator tests an older binary. Either
install it or say plainly that it is not installed. Building and then discussing
the fix as though it were live is how an evening gets lost.

### 3. Never hand an unlaunched build to a live show

Run it first. Tests passing is not the same as the app working: none of the
regressions on 2026-08-09 — squashed artwork, a buried ticker, a black video
area — were reachable by any test in the suite.

If it cannot be launched (mid-show, no hardware), say so explicitly and let the
operator decide, rather than implying it was validated.

### 4. Rendering and layout changes cannot be validated by tests

Aspect ratio, stacking, cursors, shaders, window geometry: look at them. Take a
screenshot (`screencapture -x -D <display>`). Never change shader geometry or
surface ordering during a show.

### 5. Do not change deliberate behaviour on inference

A comment saying "aspect ratio is deliberately ignored" is a decision, not a
bug. "It looks wrong to me" is not evidence. Ask. On 2026-08-09 a deliberate
edge-to-edge fill was reverted to letterboxing because a vague report was read
as confirmation.

### 6. Native child surfaces stack by creation order

Qt Quick overlays, the ticker, and mpv views are native NSViews on macOS. Their
z-order follows creation order, NOT the Qt widget hierarchy, and nothing in the
widget tree clips them. Deferring the creation of one past another silently puts
it on top — this hid the ticker on one launch and the whole video area on the
next. Anything that reorders surface creation must re-raise what it displaced,
and must be verified on screen.

### 7. Tests must never touch live show data

`~/SingWS` holds the log, settings.json, the queue and singer history. Importing
the main module opens the live log; anything reaching `save_settings()` or
`save_data()` overwrites the operator's real state. Always run the suite with a
scratch root:

    SINGWS_HOME=$(mktemp -d) ...

`tools/run_tests.sh` does this. Confirm afterwards that the live log line count
did not move.

**Operator rehearsal preference (2026-08-31):** use the actual
`/Applications/SingWS.app` with its normal profile and existing server connection.
The operator explicitly rejected keeping a separate SingWS Test app/profile.
For future manual rehearsals, back up the existing app and data, install the
candidate locally under the normal app identity, and keep publication separate.
Do not copy old rehearsal history into the normal profile. This preference does
not change the scratch `SINGWS_HOME` requirement for automated tests above.

### 8. Native crashes are not in the app log

`SingWS/logs/*.log` only catches Python exceptions. Real segfaults land in
`~/Library/Logs/DiagnosticReports/SingWS-*.ips`. Read the faulting thread's
stack there before concluding there have been no crashes.

### 9. Diagnostics must not cost more than the fault

An application-wide Qt event filter in Python taxes every paint and timer in the
app; walking the main thread's live frames from a watchdog thread is a
use-after-free. Both shipped as stall diagnostics on 2026-08-09; one tripled the
stall count and the other segfaulted the app. Keep this class of instrumentation
opt-in and default off (`stall_event_attribution`, `stall_stack_capture`).

### 10. Prefer the smallest change that fixes the reported fault

Speculative performance work caused every regression that night, while the
actual faults were three pre-existing bugs. When a change is not demanded by the
report in hand, leave it out.

---

## This machine

Facts about the development Mac that are not visible from the source tree, and
that have each cost a session's worth of wrong reasoning at least once.

### Development machines

**SingWS 2.0** is developed and built on an **Apple Silicon** MacBook Pro and
targets arm64, macOS 15+ only. Do not add Intel or pre-15 compatibility code to 2.0.

Measured 2026-09-16: **Apple M1 Max, 10 cores, 32 GB RAM, macOS 27**, ~175 GB free
of 926 GB. Performance budgets in the roadmap assume a *base* M1, so this machine
has headroom — do not quietly raise a budget because it is fast here. The free disk
figure is the real ceiling on any stems cache.

**This is no longer the Intel Mac described below.** Those notes are 1.x history,
kept because 1.x still ships Intel builds; they are not facts about the machine
you are running on.

**SingWS 1.x (0.4.7.x)** still ships Intel and older-macOS builds and is
maintained **permanently** as the free edition (decided 2026-09-16). The notes below about an Intel dev Mac apply to 1.x work only:

arm64 binaries cannot execute on the Intel Mac — `lipo`-thinning an arm64 python
gives "bad CPU type in executable". `build_all.sh` comments assume an Apple
Silicon host. `verify_macos_arch.py --require arm64 --require x86_64` passing
checks which slices a binary contains, not which one can run.

**The Cowork linked shell is a Linux VM on the Mac, not macOS.** It can run
pure-Python tests only (no PyQt6, mpv, BASS, or Core Audio). Full suites must
run on macOS itself.

### Soundcraft Ui mixer is not owned yet

The 2.0 digital-mixer target is a Soundcraft Ui24R (network control + multichannel USB audio) that the operator
does not have yet. Build and test against the virtual mixer simulator; never
claim hardware verification for mixer features.

### Running the tests

    ./tools/run_tests.sh

from the repo (or worktree) root. It creates and cleans a scratch `SINGWS_HOME`
automatically — always required, see live-show rule 7 — and picks an interpreter,
preferring `$ROOT/qtvenv`. **The suites run under `pytest`, not bare `unittest`.**
Earlier revisions of this section said there was no pytest; that is wrong, and
`run_tests.sh` will fail with `No module named pytest` if the venv lacks it.

**Building the venv (2026-09-16, verified on the M1 Max):**

    python3 -m venv qtvenv
    ./qtvenv/bin/pip install -c constraints-macos15.txt PyQt6 psutil requests \
        numpy qrcode pillow scipy pytest

`constraints-macos15.txt` is the pin set the arm64 build enforces, so the test
venv and the build agree. It replaced `constraints-macos12.txt` on 2026-09-16:
that file existed only to hold the macOS 12 floor for 1.x, and the floor was the
sole reason Qt was pinned to 6.9.1. 2.0 runs **Qt 6.11**.

**A fresh venv constructs a QApplication in both `offscreen` and `cocoa`.**
The long-standing claim that *no* venv could — blamed here for a long time on a
PyQt6 / PyQt6-Qt6 version split — was wrong about the cause. The real cause is
**copied venvs**: `/Users/daniel/Documents/SingWS/.venv` reports
`sys.prefix = .../.venv-repair` and loads PyQt6 from that other directory, so Qt
resolves its plugin path to `""` and finds nothing, whatever the versions are.
Matching versions was never the issue. Do not copy or rename a venv directory;
build a new one. That checkout now holds 13 assorted `.venv*` directories of
unknown provenance — prefer a fresh `qtvenv` in the worktree you are working in.

**A fresh worktree needs two symlinks**, or ~13 tests fail for reasons that have
nothing to do with your change (`bundled libmpv is unavailable`, and PHP
`Failed opening required .../SingWS-Server/...`). Both targets are gitignored or
untracked, so `git worktree add` does not bring them:

    ln -sfn /Users/daniel/Documents/SingWS/native_dual_view/Frameworks \
        native_dual_view/Frameworks      # 59 arm64 mpv dylibs, 38 MB
    ln -sfn /Users/daniel/Documents/SingWS/SingWS-Server SingWS-Server

**Recorded baseline — branch `2.0` at the MS0 foundation, 2026-09-16:**
**1045 passed, 2 failed, 34 subtests, ~66 s.** Identical on Qt 6.9.1 and Qt 6.11.0,
so the Qt upgrade moved nothing the suite can see — but see the rendering caveat
in `constraints-macos15.txt`: it cannot see rendering at all. The two failures are pre-existing
test debt that fails identically on `main`, so they are not a 2.0 regression:

- `test_karafun_fullscreen.py::FullscreenTests::test_failed_verification_does_not_claim_success_or_click_again`
- `test_rotation_tv_design.py::RotationTvDesignTests::test_decorative_rotation_effects_pause_during_karaoke`

Before blaming a change for either, run it against `main` the same way first.

**Source-text assertions break on renames.** Several tests `.index()` literal
source strings and fail with `ValueError: substring not found` rather than
anything descriptive. The SingWS Pro rename broke two this way
(`setApplicationName("SingWS")` → `APP_DISPLAY_NAME`, and the `~/SingWS` →
`~/SingWSPro` data folder). Expect more when renaming; they are stale tests, not
behaviour regressions.

### The CDG timing offset is wired but NOT calibrated

`MpvKaraokeTransport.set_video_offset_ms()` used to be an unconditional no-op,
so the calibrated `FFMPEG_CDG_BASE_OFFSET_MS` (+750ms after the baseline
migration) never reached mpv. Symptom: CDG lyrics ran ~750ms out and the
Display tab's fine tuning did nothing.

Both backends can apply it now. The in-process (IINA) backend maps it onto
mpv's `audio-delay` (`mpv_playback_iina.py`). The follower backend adds it to
the master reference its video followers chase — `delta = (master +
self._video_offset_s) - t` in `MpvPlaybackPlugin._sync_loop`
(`mpv_playback_iina.py`), fed by `setVideoOffsetMs`.

**Confirmed correct on screen by the operator on 2026-08-18.** The wiring is
also covered by `CdgVisualOffsetTests`, which pins the plumbing and the clamp.
The long-standing "uncalibrated, do not ship" caveat is closed — stop citing
the offset as the reason mpv cannot be the default.

**There is no longer an engine choice to make.** `karaoke_engine` survives in
exactly two places: the `DEFAULTS` entry (`"mpv"`, kept only so old settings
files deserialize) and a launch-time migration that force-sets it to `"mpv"`
whatever was saved. There is no Settings checkbox for it and no FFmpeg karaoke
path left — the native mpv core has owned playback since the 0.4.5.0 cleanup.
Earlier revisions of this section told agents to keep the default on `ffmpeg`
and preserve an engine checkbox; both instructions are obsolete and were still
being acted on as late as 2026-08-18. Do not reintroduce them.

---

## Reporting format

Use this output structure for substantial tasks:

1. Repo findings
2. Plan
3. Changes made
4. Files changed
5. Build/test commands
6. Verification status
7. Blockers / assumptions
8. Next recommended step

Keep reports concise but specific.

---

## What to avoid

Do not:
- rewrite the whole app when the task only needs an incremental change
- replace current playback prematurely
- guess architecture details without inspecting the code
- claim realtime safety without reasoning about the audio path
- put heavy DSP work into the Python UI layer
- hide broken build steps
- continue making large changes after the requested milestone is complete

---

## Preferred approach for this repo

When asked to improve playback:
- first find the current playback and transport code
- preserve the current app structure
- add a native extension module incrementally
- keep Python as the application shell
- keep native code focused on performance-critical audio logic
- stop after each milestone and report clearly

## Queue and server synchronization rules

Host actions are authoritative.

If the host:
- deletes a song
- reorders songs
- replaces a song
- moves a song between sections

the server must adopt the host state.

The server must never recreate songs that the host intentionally removed.

Reconnects, sync operations, queue rebuilds, or startup restoration must not override host actions.

Requests should transition state rather than creating replacement objects.

Every request must have a permanent unique identifier that survives:
- reconnects
- application restarts
- websocket reconnects
- queue rebuilds
- waitlist transitions
- server synchronization passes

If a request already exists in memory or storage, update the existing request rather than creating a new one.

Duplicate detection should:
- preserve the oldest accepted copy
- discard newer duplicates automatically
- preserve singer order and request timestamps where possible

Queue synchronization should favor state transitions over object recreation.

Preferred request lifecycle:

- requested
- pending
- waitlisted
- accepted
- active rotation
- currently singing
- completed
- skipped
- removed

A request should move through these states rather than being destroyed and recreated during synchronization.

The host application is the source of truth for active rotation state unless the task explicitly requires otherwise.

Requests must not be identified solely by:
- singer name
- song title
- artist
- queue position

Multiple requests may share those values legitimately.

Duplicate detection should use the permanent request identifier rather than metadata comparisons whenever possible.

Request identifiers must be generated once at request creation time and must never change during the request lifetime.
