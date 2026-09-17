import importlib.util
import inspect
import io
import json
import math
import os
from pathlib import Path
import struct
from types import SimpleNamespace
import tempfile
import time
import unittest
import wave
import zipfile
from unittest import mock


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_recent_regressions", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_app(module):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = dict(module.DEFAULTS)
    app.queue = []
    app.bg_music = None
    app.karaoke_playing = False
    app._last_sung_singer_display = ""
    app._last_sung_title = ""
    app._current_karaoke_singer_name = ""
    app._current_karaoke_singer_display = ""
    app._current_karaoke_song_path = ""
    app._current_karaoke_semitones = 0
    app._karaoke_tempo_percent = 100
    app.lookup_display_name = lambda path, artist_title_only=False: "Artist • " + str(path).split("/")[-1]
    app._is_karaoke_paused = lambda: False
    app._gst_query_times = lambda: (0, 0)
    return app


class FakeStatusLabel:
    def __init__(self):
        self._text = ""
        self.styles = []

    def setText(self, text):
        self._text = str(text or "")

    def text(self):
        return self._text

    def setStyleSheet(self, style):
        self.styles.append(style)


class FakeSingleShotTimer:
    def __init__(self):
        self.started = []
        self.stopped = 0

    def stop(self):
        self.stopped += 1

    def start(self, delay):
        self.started.append(delay)


class RecentRegressionTests(unittest.TestCase):

    def test_missing_mutagen_is_reported_not_silently_zero(self):
        """probe_duration_seconds returns 0.0 for an unreadable file, which is
        fine, and used to return 0.0 for EVERY file when mutagen was missing,
        which silently disabled trailing-silence detection. The dependency
        failure must be logged; per-file failures stay quiet."""
        import importlib
        import logging
        import media_helpers

        importlib.reload(media_helpers)
        with mock.patch.dict("sys.modules", {"mutagen": None}):
            with self.assertLogs(level=logging.ERROR) as captured:
                self.assertEqual(media_helpers.probe_duration_seconds("/any.mp3"), 0.0)
            self.assertTrue(any("mutagen" in line for line in captured.output))
            self.assertFalse(media_helpers.dependency_status()["mutagen"])
            # Reported once, not per file.
            media_helpers.probe_duration_seconds("/another.mp3")
        importlib.reload(media_helpers)
        self.assertTrue(media_helpers.dependency_status()["mutagen"])
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()
        with open("0.2.18.1.py", "r", encoding="utf-8") as fh:
            cls.singws_source = fh.read()

    def test_defaults_keep_simple_audio_and_ticker_speed(self):
        self.assertTrue(self.singws.DEFAULTS["simple_audio_mode"])
        self.assertIn("ticker_speed_px_per_sec", self.singws.DEFAULTS)
        self.assertGreater(float(self.singws.DEFAULTS["ticker_speed_px_per_sec"]), 0)
        self.assertEqual(int(self.singws.DEFAULTS["video_timing_offset_ms"]), 0)
        self.assertFalse(self.singws.DEFAULTS["next_up_overlay_enabled"])
        self.assertEqual(int(self.singws.DEFAULTS["next_up_overlay_duration_sec"]), 10)
        self.assertIn("lyrics_background_video_opacity", self.singws.DEFAULTS)
        self.assertIn("crash_log_email_to", self.singws.DEFAULTS)

    def test_widget_surfaces_are_double_buffered_by_default_on_macos(self):
        """Single buffering is opt-in; it corrupted the operator window.

        Forcing SwapBehavior::SingleBuffer made the crashing blitBuffer call
        site unreachable, but it also lets the window server read the raster
        surface mid-paint. On 2026-08-09 that showed as torn and stale widgets
        on every build carrying it (0.4.3.9, 0.4.4.0) and on none without it
        (0.4.3.6). Qt's default now wins unless SINGWS_WIDGET_SWAP=single.
        """
        from PyQt6.QtGui import QSurfaceFormat

        original = QSurfaceFormat.defaultFormat()
        try:
            with mock.patch.object(self.singws.sys, "platform", "darwin"), \
                 mock.patch.dict(os.environ, {"SINGWS_WIDGET_SWAP": ""}):
                # Default: Qt keeps its swap chain, so widgets paint cleanly.
                self.assertFalse(self.singws._install_single_buffered_widget_surfaces())
                self.assertNotEqual(
                    QSurfaceFormat.defaultFormat().swapBehavior(),
                    QSurfaceFormat.SwapBehavior.SingleBuffer,
                )

            # The crash workaround is still reachable without a rebuild.
            QSurfaceFormat.setDefaultFormat(original)
            with mock.patch.object(self.singws.sys, "platform", "darwin"), \
                 mock.patch.dict(os.environ, {"SINGWS_WIDGET_SWAP": "single"}):
                self.assertTrue(self.singws._install_single_buffered_widget_surfaces())
                self.assertEqual(
                    QSurfaceFormat.defaultFormat().swapBehavior(),
                    QSurfaceFormat.SwapBehavior.SingleBuffer,
                )
        finally:
            QSurfaceFormat.setDefaultFormat(original)

        # Applied before the QApplication: the format is read when each platform
        # window is created, and the first ones exist during construction.
        main = self.singws_source
        self.assertLess(
            main.index("_install_single_buffered_widget_surfaces()\n\n    app = QApplication(["),
            main.index("app.setApplicationName(APP_DISPLAY_NAME)"),
        )

    def test_quick_views_keep_double_buffering(self):
        """The scene graph is the one surface where single buffering would show."""
        from PyQt6.QtGui import QSurfaceFormat

        class FakeView:
            def __init__(self):
                self._fmt = QSurfaceFormat()

            def format(self):
                return QSurfaceFormat(self._fmt)

            def setFormat(self, fmt):
                self._fmt = fmt

        view = FakeView()
        self.singws._apply_double_buffered_quick_format(view)
        self.assertEqual(view._fmt.swapBehavior(), QSurfaceFormat.SwapBehavior.DoubleBuffer)

        # Every QQuickView must get it, and before it is shown.
        main = self.singws_source
        self.assertEqual(main.count("self._view = QQuickView()"), 4)
        self.assertEqual(
            main.count("self._view = QQuickView()\n        _apply_double_buffered_quick_format(self._view)"),
            4,
        )

    def test_quick_child_surfaces_default_on_on_intel_with_safety_override(self):
        """Intel keeps the v0.4.6.1 visuals unless the operator disables them."""
        intel = lambda: (  # noqa: E731 - three patches, used twice below
            mock.patch.object(self.singws.sys, "platform", "darwin"),
            mock.patch.object(self.singws.platform, "machine", return_value="x86_64"),
            mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "", "SINGWS_QUICK_SURFACES": ""}),
        )
        try:
            self.singws.set_quick_surfaces_override("auto")
            with intel()[0], intel()[1], intel()[2]:
                self.assertTrue(self.singws._native_quick_child_surfaces_supported())
                self.assertTrue(self.singws._rotation_quick_surfaces_supported())

            self.singws.set_quick_surfaces_override("on")
            with intel()[0], intel()[1], intel()[2]:
                self.assertTrue(self.singws._native_quick_child_surfaces_supported())
                self.assertTrue(self.singws._rotation_quick_surfaces_supported())

            self.singws.set_quick_surfaces_override("off")
            with intel()[0], intel()[1], intel()[2]:
                self.assertFalse(self.singws._native_quick_child_surfaces_supported())
                self.assertFalse(self.singws._rotation_quick_surfaces_supported())

            # The env override still wins over the setting, for a one-off test.
            self.singws.set_quick_surfaces_override("on")
            with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "", "SINGWS_QUICK_SURFACES": "0"}):
                self.assertFalse(self.singws._native_quick_child_surfaces_supported())
        finally:
            self.singws.set_quick_surfaces_override("auto")

    def test_intel_uses_proven_painter_ticker_on_detached_surface(self):
        with (
            mock.patch.object(self.singws.sys, "platform", "darwin"),
            mock.patch.object(self.singws.platform, "machine", return_value="x86_64"),
            mock.patch.dict(
                os.environ,
                {"QT_QPA_PLATFORM": "", "SINGWS_QUICK_SURFACES": "", "SINGWS_QUICK_TICKER": ""},
            ),
        ):
            self.singws.set_quick_surfaces_override("auto")
            self.assertTrue(self.singws._native_quick_child_surfaces_supported())
            self.assertTrue(self.singws._native_quick_ticker_supported())
            self.assertTrue(self.singws._detached_quick_ticker_required())

        with mock.patch.dict(os.environ, {"SINGWS_QUICK_TICKER": "1"}):
            self.assertTrue(self.singws._native_quick_ticker_supported())

        video_init = inspect.getsource(self.singws.VideoWindow.__init__)
        show_vfx = inspect.getsource(self.singws.VideoWindow._attach_show_vfx)
        self.assertIn("_native_quick_ticker_supported()", video_init)
        self.assertIn("_native_quick_child_surfaces_supported()", show_vfx)

        video_init = inspect.getsource(self.singws.VideoWindow.__init__)
        ticker_init = inspect.getsource(self.singws.DetachedPainterTicker.__init__)
        ticker_sync = inspect.getsource(self.singws.DetachedPainterTicker.sync_surface_geometry)
        self.assertIn("_detached_quick_ticker_required()", video_init)
        self.assertIn("Ticker(", ticker_init)
        self.assertIn("WindowDoesNotAcceptFocus", ticker_init)
        self.assertIn("setTransientParent", ticker_sync)

    def test_ticker_effects_checkbox_is_live_after_switching_surfaces_on(self):
        """A pending on-at-next-launch state must not grey the control out.

        Otherwise turning GPU surfaces on disables the very checkbox it just
        enabled, which reads as the animated ticker being broken.
        """
        app = SimpleNamespace(video_window=None)
        supported = self.singws.KaraokeApp._ticker_effects_supported
        pending = self.singws.KaraokeApp._ticker_effects_pending_relaunch
        try:
            self.singws.set_quick_surfaces_override("on")
            with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "", "SINGWS_QUICK_SURFACES": ""}):
                self.assertTrue(supported(app))
                self.assertTrue(pending(app))

            self.singws.set_quick_surfaces_override("off")
            with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "", "SINGWS_QUICK_SURFACES": ""}):
                self.assertFalse(supported(app))
                self.assertFalse(pending(app))
        finally:
            self.singws.set_quick_surfaces_override("auto")

        # A live Quick ticker is authoritative and needs no relaunch note.
        live = SimpleNamespace(
            video_window=SimpleNamespace(
                ticker=SimpleNamespace(set_effects_enabled=lambda _enabled: None)
            )
        )
        self.assertTrue(supported(live))
        self.assertFalse(pending(live))

    def test_rotation_summary_uses_index_instead_of_full_library_scan(self):
        source = inspect.getsource(self.singws.KaraokeApp._update_rotation_summary_card)
        self.assertIn("song_index.find_by_path(current_path)", source)
        self.assertNotIn('for t in (getattr(self, "tracks", []) or [])', source)

    def test_polling_restart_waits_asynchronously_for_retirement(self):
        app = SimpleNamespace()
        app.settings = {"base_url": "https://wskar.com", "user": "wsk"}
        calls = []
        app._app_closing = False
        app.is_network_configured = lambda: True
        app._stop_request_polling_async = lambda callback: (calls.append("retire"), callback())
        app.start_request_polling = lambda **kwargs: calls.append(("start", kwargs))
        self.singws.KaraokeApp.restart_request_polling(app)
        self.assertEqual(calls, ["retire", ("start", {"stop_existing": False})])

    def test_venue_profiles_capture_signup_server_settings(self):
        app = SimpleNamespace(settings={
            "base_url": "https://requests.example",
            "user": "downtown",
            "tenant": "downtown",
            "api_key": "venue-secret",
            "header_qr_url": "https://requests.example/downtown",
            "host_controls_pin": "2468",
            "requests_accepting": False,
            "use_waiting_for_add": True,
            "show_request_qr": True,
        })
        app.VENUE_SCOPED_SETTINGS = self.singws.KaraokeApp.VENUE_SCOPED_SETTINGS

        captured = self.singws.KaraokeApp._capture_venue_settings(app)

        for key, value in app.settings.items():
            self.assertIn(key, captured)
            self.assertEqual(captured[key], value)

    def test_venue_live_refresh_avoids_forced_background_fade_and_rebinds_network(self):
        calls = []
        app = SimpleNamespace(
            _apply_runtime_media_settings=lambda: calls.append("media"),
            _update_audio_output_button=lambda: calls.append("audio"),
            schedule_ticker_update=lambda: calls.append("ticker"),
            _apply_idle_background=lambda **kwargs: calls.append(("background", kwargs)),
            _refresh_header_status=lambda: calls.append("status"),
            _update_header_qr_widget=lambda: calls.append("header_qr"),
            _refresh_show_screen_qr=lambda *args, **kwargs: calls.append(("show_qr", args, kwargs)),
            restart_request_polling=lambda: calls.append("polling"),
            _sync_session_location_for_venue=lambda: calls.append("location"),
            _apply_eq_settings_live=lambda reason: calls.append(("eq", reason)),
        )

        self.singws.KaraokeApp._apply_venue_settings_live(app, "Downtown")

        self.assertIn(("background", {"force": False, "advance_slideshow": False}), calls)
        self.assertIn("polling", calls)
        self.assertIn(("show_qr", ("venue_switch",), {"force": True}), calls)
        self.assertIn(("eq", "venue_switch"), calls)

    def test_ui_uses_the_system_arrow_cursor(self):
        """No web-style hand cursors: macOS reserves the pointing hand for links.

        Native controls keep the arrow, and Qt cursors inherit to children with
        nothing in this file ever resetting one, so a single stray call spreads
        further than its widget.
        """
        self.assertNotIn("PointingHandCursor", self.singws_source)

    def test_venue_profiles_scope_eq_and_ticker_settings(self):
        # A room's EQ curve and its ticker branding both belong to the venue,
        # not the laptop.
        scoped = set(self.singws.KaraokeApp.VENUE_SCOPED_SETTINGS)
        for key in ("eq_karaoke", "eq_karaoke_enabled", "eq_bgm", "eq_bgm_enabled"):
            self.assertIn(key, scoped, f"{key} must follow the venue")
        for key in ("ticker_enabled", "ticker_custom_enabled", "ticker_custom_message",
                    "ticker_color", "ticker_speed_px_per_sec", "ticker_size_index",
                    "ticker_bold", "ticker_vfx_enabled", "rotation_announcement_enabled",
                    "rotation_announcement_message"):
            self.assertIn(key, scoped, f"{key} must follow the venue")

        # Round-trip: capture under one venue, change, switch back, get it back.
        app = SimpleNamespace(settings={
            "eq_karaoke": [3.0, -2.0, 0.0],
            "eq_karaoke_enabled": True,
            "eq_bgm": [1.5, 1.5],
            "eq_bgm_enabled": False,
            "ticker_custom_message": "Downtown Bar",
            "ticker_vfx_enabled": True,
            "rotation_announcement_enabled": True,
            "rotation_announcement_message": "$5 margaritas until 10 PM",
        })
        app.VENUE_SCOPED_SETTINGS = self.singws.KaraokeApp.VENUE_SCOPED_SETTINGS
        captured = self.singws.KaraokeApp._capture_venue_settings(app)
        self.assertEqual(captured["eq_karaoke"], [3.0, -2.0, 0.0])
        self.assertEqual(captured["eq_karaoke_enabled"], True)
        self.assertEqual(captured["ticker_custom_message"], "Downtown Bar")
        self.assertEqual(captured["ticker_vfx_enabled"], True)
        self.assertEqual(captured["rotation_announcement_enabled"], True)
        self.assertEqual(captured["rotation_announcement_message"], "$5 margaritas until 10 PM")

    def test_venue_eq_switch_moves_the_live_audio_path(self):
        # Writing the settings is not enough: the previous room's curve would
        # keep playing until the next restart.
        pushed = []

        class _EQ:
            def __init__(self):
                self.gains = None
                self.enabled = None

            def set_all_gains_db(self, g):
                self.gains = list(g)

            def set_enabled(self, on):
                self.enabled = bool(on)

        karaoke_eq, bgm_eq = _EQ(), _EQ()
        bass = SimpleNamespace(set_eq=lambda eq: pushed.append(("bass_eq", eq is bgm_eq)))
        app = SimpleNamespace(
            settings={
                "eq_karaoke": [4.0, 0.0, -1.0],
                "eq_karaoke_enabled": True,
                "eq_bgm": [2.0, -2.0],
                "eq_bgm_enabled": True,
            },
            karaoke_eq=karaoke_eq,
            bgm_eq=bgm_eq,
            bg_music=SimpleNamespace(_bass_engine=bass),
            _simple_audio_mode=lambda: False,
            _push_mpv_audio_processing=lambda reason: pushed.append(("mpv", reason)),
            _log_bgm_eq_route=lambda reason: pushed.append(("bgm_route", reason)),
        )

        self.singws.KaraokeApp._apply_eq_settings_live(app, "venue_switch")

        self.assertEqual(karaoke_eq.gains, [4.0, 0.0, -1.0])
        self.assertTrue(karaoke_eq.enabled)
        self.assertEqual(bgm_eq.gains, [2.0, -2.0])
        self.assertTrue(bgm_eq.enabled)
        # The karaoke chain must be rebuilt; mpv has no EQ object to re-point.
        self.assertIn(("mpv", "venue_eq:venue_switch"), pushed)
        self.assertIn(("bass_eq", True), pushed)

    def test_venue_eq_switch_does_not_build_engines(self):
        # _ensure_eq_engines imports scipy/numpy; a venue switch must not drag
        # that in for operators who never open the EQ.
        app = SimpleNamespace(
            settings={"eq_karaoke": [1.0], "eq_karaoke_enabled": True},
            karaoke_eq=None,
            bgm_eq=None,
            _ensure_eq_engines=lambda: self.fail("venue switch must not build EQ engines"),
        )
        self.singws.KaraokeApp._apply_eq_settings_live(app, "venue_switch")
        self.assertIsNone(app.karaoke_eq)

    def test_venue_eq_respects_simple_audio_mode(self):
        # Same rule as the EQ dialog: Simple Audio Mode keeps BGM EQ out of the
        # chain entirely.
        pushed = []

        class _EQ:
            def set_all_gains_db(self, g):
                pass

            def set_enabled(self, on):
                pass

        bgm_eq = _EQ()
        app = SimpleNamespace(
            settings={"eq_bgm": [1.0], "eq_bgm_enabled": True},
            karaoke_eq=None,
            bgm_eq=bgm_eq,
            bg_music=SimpleNamespace(
                _bass_engine=SimpleNamespace(set_eq=lambda eq: pushed.append(eq))
            ),
            _simple_audio_mode=lambda: True,
            _log_bgm_eq_route=lambda reason: None,
        )
        self.singws.KaraokeApp._apply_eq_settings_live(app, "venue_switch")
        self.assertEqual(pushed, [None], "Simple Audio Mode must detach the BGM EQ")

    def test_filesystem_probe_timeout_keeps_gui_path_bounded(self):
        started = time.monotonic()
        result = self.singws._bounded_filesystem_call(
            lambda: time.sleep(2.0) or True,
            default=False,
            timeout_sec=0.03,
            label="regression blocked provider",
        )
        elapsed = time.monotonic() - started

        self.assertFalse(result)
        self.assertLess(elapsed, 0.25)

    def test_background_video_settings_do_not_enumerate_folder_on_button_click(self):
        source = inspect.getsource(self.singws.KaraokeApp.configure_settings)
        refresh_source = source.split("def _refresh_bg_video_folder_label():", 1)[1].split(
            "bg_video_folder_row.addWidget", 1
        )[0]
        choose_source = source.split("def on_choose_bg_video_folder():", 1)[1].split(
            "bg_video_folder_btn.clicked.connect", 1
        )[0]
        self.assertNotIn("scan_background_video_folder", refresh_source)
        self.assertNotIn("scan_background_video_folder", choose_source)

    def test_log_package_redacts_credentials_and_skips_old_files(self):
        with tempfile.TemporaryDirectory() as td:
            old_logs_dir = self.singws.LOGS_DIR
            self.singws.LOGS_DIR = Path(td)
            try:
                recent = Path(td) / "singws_recent.log"
                recent.write_text("api_key=secret token=abc password: hunter2 ok", encoding="utf-8")
                old = Path(td) / "singws_old.log"
                old.write_text("old secret", encoding="utf-8")
                old_time = time.time() - (5 * 86400)
                old.touch()
                import os
                os.utime(old, (old_time, old_time))

                package, files, error = self.singws.prepare_log_email_package(days=3)
                self.assertEqual(error, "")
                self.assertIsNotNone(package)
                self.assertEqual([p.name for p in files], ["singws_recent.log"])
                with zipfile.ZipFile(package, "r") as zf:
                    text = zf.read("singws_recent.log").decode("utf-8")
                self.assertNotIn("secret", text)
                self.assertNotIn("hunter2", text)
                self.assertIn("api_key=***", text)
                self.assertIn("token=***", text)
                self.assertIn("password: ***", text)
            finally:
                self.singws.LOGS_DIR = old_logs_dir

    def test_rotation_data_omits_empty_singer_and_keeps_active_numbers_contiguous(self):
        app = make_app(self.singws)
        app._first_active_entry_for_singer = lambda singer: next((s for s in singer.get("songs", []) if not s.get("skipped", False)), None)
        app._queue_singer_display_for_entry = lambda singer, entry: singer.get("name", "")
        app.queue = [
            {"name": "Dan", "songs": [{"song_info": "/tmp/a.mp3", "skipped": False}]},
            {"name": "Steve", "songs": []},
            {"name": "Bill", "songs": [{"song_info": "/tmp/b.mp3", "skipped": False}]},
        ]

        rotation = self.singws.KaraokeApp.get_rotation_data(app)["rotation"]
        self.assertEqual([row["name"] for row in rotation], ["Dan", "Bill"])
        self.assertEqual([row["number"] for row in rotation], [1, 2])
        self.assertEqual([row["rotation_position"] for row in rotation], [1, 2])
        self.assertTrue(all(row["active"] for row in rotation))

    def test_server_rotation_uses_karafun_song_title_not_provider_id(self):
        app = make_app(self.singws)
        entry = {
            "song_info": "karafun_streaming:kf_7271",
            "path": "karafun_streaming:kf_7271",
            "provider": "karafun_streaming",
            "provider_track_id": "kf_7271",
            "artist": "Bruno Mars",
            "title": "Risk It All",
            "display_name": "Bruno Mars - Risk It All - KaraFun",
            "skipped": False,
        }
        app.queue = [{"name": "Daniel", "songs": [entry]}]
        app._first_active_entry_for_singer = lambda singer: singer["songs"][0]
        app._queue_singer_display_for_entry = lambda singer, _entry: singer["name"]

        rotation = app.get_rotation_data()["rotation"]

        self.assertEqual(rotation[0]["songs"], ["Bruno Mars • Risk It All"])
        self.assertNotIn("kf_7271", rotation[0]["songs"][0])

    def test_host_rotation_state_empty_defaults(self):
        app = make_app(self.singws)
        state = app._host_control_state()
        rotation = state["rotation"]
        self.assertEqual(rotation["last"]["singer"], "")
        self.assertEqual(rotation["current"]["singer"], "")
        self.assertEqual(rotation["next"]["singer"], "")

    def test_host_rotation_current_and_next_are_different_items(self):
        app = make_app(self.singws)
        app.karaoke_playing = True
        app._current_karaoke_singer_name = "George"
        app._current_karaoke_singer_display = "George"
        app._current_karaoke_song_path = "/tmp/current.mp3"
        app._current_karaoke_semitones = 0
        app._karaoke_tempo_percent = 100
        app.queue = [
            {
                "name": "George",
                "skipped": False,
                "songs": [
                    {"song_info": "/tmp/current.mp3", "title": "Current", "artist": "Artist", "skipped": False},
                    {"song_info": "/tmp/next.mp3", "title": "Next", "artist": "Artist", "skipped": False},
                ],
            }
        ]

        rotation = app._host_control_state()["rotation"]
        self.assertEqual(rotation["current"]["singer"], "George")
        self.assertEqual(rotation["next"]["singer"], "George")
        self.assertNotEqual(rotation["current"]["item_id"], rotation["next"]["item_id"])
        self.assertEqual(rotation["next"]["title"], "Next")

    def test_next_up_overlay_payload_uses_next_song_and_on_deck(self):
        app = make_app(self.singws)
        app._current_karaoke_singer_name = "Ada"
        app._current_karaoke_song_path = "/tmp/current.mp3"
        app.queue = [
            {
                "name": "Ada",
                "skipped": False,
                "songs": [
                    {"song_info": "/tmp/current.mp3", "artist": "Artist", "title": "Current", "skipped": False},
                    {"song_info": "/tmp/next.mp3", "artist": "Artist", "title": "Next", "skipped": False},
                ],
            },
            {
                "name": "Bo",
                "skipped": False,
                "songs": [
                    {"song_info": "/tmp/bo.mp3", "artist": "Other", "title": "On Deck", "skipped": False},
                ],
            },
        ]

        payload = app._next_up_transition_payload_from_queue()
        self.assertEqual(payload["singer"], "Ada")
        self.assertEqual(payload["title"], "Next")
        self.assertEqual(payload["artist"], "Artist")
        self.assertEqual(payload["on_deck"], "Bo")

    def test_next_up_overlay_setting_gate_and_duration(self):
        app = make_app(self.singws)
        calls = []

        class FakeArea:
            def show_next_up_overlay(self, payload, duration):
                calls.append((payload, duration))

        app.video_window = SimpleNamespace(video_area=FakeArea())
        app.settings["next_up_overlay_enabled"] = True
        app.settings["next_up_overlay_duration_sec"] = 7
        payload = {"singer": "Ada", "title": "Song", "artist": "Artist", "on_deck": "Bo"}

        self.assertTrue(app._show_next_up_transition_overlay(payload, reason="test"))
        self.assertEqual(calls, [(payload, 7.0)])

        app.settings["next_up_overlay_enabled"] = False
        self.assertFalse(app._show_next_up_transition_overlay(payload, reason="test"))
        self.assertEqual(len(calls), 1)

    def test_next_up_overlay_pressing_play_does_not_show(self):
        app = make_app(self.singws)
        calls = []

        class FakeArea:
            def show_next_up_overlay(self, payload, duration):
                calls.append((payload, duration))

        app.video_window = SimpleNamespace(video_area=FakeArea())
        app.settings["next_up_overlay_enabled"] = True
        app._next_up_overlay_completion_token = 1
        app._next_up_overlay_consumed_token = 0
        app._next_up_overlay_pending_payload = {"singer": "Ada", "title": "Next", "artist": "Artist", "on_deck": ""}

        singer = {"name": "Ada"}
        entry = {"song_info": "/tmp/next.mp3", "title": "Next", "artist": "Artist"}

        self.assertFalse(
            app._consume_next_up_overlay_for_transition(
                singer,
                entry,
                title="Next",
                artist="Artist",
                reason="test_play",
            )
        )
        self.assertEqual(calls, [])
        self.assertEqual(app._next_up_overlay_consumed_token, 1)

    def test_next_up_overlay_pause_resume_seek_restart_do_not_show(self):
        app = make_app(self.singws)
        calls = []

        class FakeArea:
            def show_next_up_overlay(self, payload, duration):
                calls.append((payload, duration))

        app.video_window = SimpleNamespace(video_area=FakeArea())
        app.settings["next_up_overlay_enabled"] = True
        singer = {"name": "Ada"}
        entry = {"song_info": "/tmp/next.mp3", "title": "Next", "artist": "Artist"}
        for idx, reason in enumerate(("pause_resume", "seek", "same_song_restart"), start=1):
            app._next_up_overlay_completion_token = idx
            app._next_up_overlay_consumed_token = 0
            app._next_up_overlay_pending_payload = {"singer": "Ada", "title": "Next", "artist": "Artist", "on_deck": ""}
            self.assertFalse(
                app._consume_next_up_overlay_for_transition(
                    singer,
                    entry,
                    title="Next",
                    artist="Artist",
                    reason=reason,
                )
            )
        self.assertEqual(calls, [])

    def test_song_end_uses_short_outro_not_next_up_countdown(self):
        app = make_app(self.singws)
        calls = []

        class FakeArea:
            def show_next_up_overlay(self, payload, duration):
                calls.append(("next", payload, duration))

            def show_song_outro_vfx(self, singer, title, artist, style):
                calls.append(("outro", singer, title, artist, style))
                return True

        app.video_window = SimpleNamespace(video_area=FakeArea())
        app.settings["next_up_overlay_enabled"] = True
        app.settings["next_up_overlay_duration_sec"] = 10
        app._current_karaoke_singer_name = "Ada"
        app._current_karaoke_song_path = "/tmp/current.mp3"
        app.queue = [
            {
                "name": "Ada",
                "skipped": False,
                "songs": [
                    {"song_info": "/tmp/current.mp3", "artist": "Artist", "title": "Current", "skipped": False},
                    {"song_info": "/tmp/next.mp3", "artist": "Artist", "title": "Next", "skipped": False},
                ],
            },
            {
                "name": "Bo",
                "skipped": False,
                "songs": [
                    {"song_info": "/tmp/bo.mp3", "artist": "Other", "title": "On Deck", "skipped": False},
                ],
            },
        ]

        self.assertTrue(app._mark_next_up_overlay_pending_after_completion(reason="test_end"))
        self.assertEqual(calls[0][0:2], ("outro", "Ada"))
        self.assertIn(calls[0][4], self.singws.SHOW_TRANSITION_EFFECTS)
        self.assertEqual(app._next_up_overlay_pending_payload, {})

        self.assertTrue(app._mark_next_up_overlay_pending_after_completion(reason="test_end_duplicate"))
        self.assertEqual(len(calls), 2)
        self.assertNotIn("next", [call[0] for call in calls])

    def test_karafun_outro_uses_active_metadata_not_synthetic_path(self):
        app = make_app(self.singws)
        app._current_karaoke_singer_display = "Daniel"
        app._current_karaoke_song_path = "karafun_streaming:kf_7271"
        app._current_karaoke_artist = "Bruno Mars"
        app._current_karaoke_title = "Risk It All"

        payload = app._song_outro_payload_from_current()

        self.assertEqual(payload, {
            "singer": "Daniel",
            "artist": "Bruno Mars",
            "title": "Risk It All",
        })

    def test_song_outro_does_not_require_a_next_singer(self):
        app = make_app(self.singws)
        calls = []

        class FakeArea:
            def show_next_up_overlay(self, payload, duration):
                calls.append(("next", payload, duration))

            def show_song_outro_vfx(self, singer, title, artist, style):
                calls.append(("outro", singer, title, artist, style))
                return True

        app.video_window = SimpleNamespace(video_area=FakeArea())
        app.settings["next_up_overlay_enabled"] = True
        app._current_karaoke_singer_name = "Ada"
        app._current_karaoke_song_path = "/tmp/current.mp3"
        app.queue = [
            {
                "name": "Ada",
                "skipped": False,
                "songs": [
                    {"song_info": "/tmp/current.mp3", "artist": "Artist", "title": "Current", "skipped": False},
                ],
            },
        ]

        self.assertTrue(app._mark_next_up_overlay_pending_after_completion(reason="test_end_no_next"))
        self.assertEqual(calls[0][0:2], ("outro", "Ada"))

    def test_settings_save_scheduler_debounces_ui_thread_writes(self):
        app = make_app(self.singws)
        calls = []
        app.save_settings = lambda: calls.append("save")

        class FakeTimer:
            def __init__(self):
                self.started = []

            def start(self, delay):
                self.started.append(delay)

        class FakeApp:
            def thread(self):
                return "ui-thread"

        fake_timer = FakeTimer()
        app._save_settings_timer = fake_timer

        with mock.patch.object(self.singws.QApplication, "instance", return_value=FakeApp()), \
             mock.patch.object(self.singws.QThread, "currentThread", return_value="ui-thread"):
            app._schedule_save_settings(700)
            app._schedule_save_settings(250)

        self.assertEqual(calls, [])
        self.assertEqual(fake_timer.started, [700, 250])

    def test_library_volume_analysis_collects_karaoke_and_bgm(self):
        app = make_app(self.singws)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cdg = root / "karaoke.cdg"
            mp3 = root / "karaoke.mp3"
            bgm = root / "bgm.mp3"
            package = root / "zipped-karaoke.zip"
            cdg.write_text("", encoding="utf-8")
            mp3.write_text("audio", encoding="utf-8")
            bgm.write_text("audio", encoding="utf-8")
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("inside/song.cdg", b"cdg")
                archive.writestr("inside/song.mp3", b"mp3")

            app.tracks = [
                {"path": str(cdg), "display": "Karaoke Song"},
                {"path": str(package), "display": "Zipped Song"},
            ]
            app.bg_music = SimpleNamespace(playlist=[str(bgm)])
            app.bg_manager = SimpleNamespace(current_playlist=[{"path": str(bgm)}])
            app.settings = {
                "bg_import_folders": [],
                "simple_audio_mode": False,
                "karaoke_normalize_enabled": True,
                "bg_normalize_enabled": True,
            }

            with mock.patch.object(self.singws.Path, "home", return_value=root):
                with mock.patch.object(self.singws, "loudness_gain_db_cached", return_value=None):
                    items = app._library_loudness_analysis_items(force=False)

                paths = [item[1] for item in items]
                self.assertEqual(paths.count(str(mp3)), 1)
                self.assertEqual(paths.count(str(bgm)), 1)
                self.assertEqual(paths.count(str(package)), 1)
                self.assertTrue(any(item[0] == "Karaoke" for item in items))
                self.assertTrue(any(item[0] == "BGM" for item in items))

                with mock.patch.object(
                    self.singws,
                    "loudness_gain_db_cached",
                    side_effect=lambda path: 0.0 if path == str(mp3) else None,
                ):
                    incremental = app._library_loudness_analysis_items(force=False)
                    forced = app._library_loudness_analysis_items(force=True)

            # A valid LUFS row no longer hides missing transition metadata;
            # the full scan backfills boundaries while preserving that LUFS.
            self.assertIn(str(mp3), [item[1] for item in incremental])
            self.assertIn(str(mp3), [item[1] for item in forced])

    def test_loudness_measurement_uses_bundled_libmpv_job(self):
        with mock.patch(
            "libmpv_media_jobs.measure_loudness_lufs",
            return_value=(-20.5, -1.2),
        ) as measure:
            lufs, peak_db = self.singws._measure_loudness_lufs("/tmp/song.mp3")

        self.assertEqual(lufs, -20.5)
        self.assertEqual(peak_db, -1.2)
        measure.assert_called_once_with("/tmp/song.mp3", timeout=120.0)

    def test_fast_loudness_measurement_uses_spread_sections(self):
        import libmpv_media_jobs

        with mock.patch.object(
            libmpv_media_jobs,
            "measure_loudness_lufs",
            return_value=(-17.0, -1.0),
        ) as measure:
            result = libmpv_media_jobs.measure_loudness_fast_lufs(
                "/tmp/song.mp3", timeout=25.0)

        self.assertEqual(result, (-17.0, -1.0))
        timeline = measure.call_args.args[0]
        self.assertTrue(timeline.startswith("edl://"))
        self.assertEqual(timeline.count("length=12"), 5)
        for start in (0, 45, 90, 135, 180):
            self.assertIn(f"start={start}", timeline)
        self.assertEqual(measure.call_args.kwargs, {"timeout": 25.0})

    def test_libmpv_pcm_output_does_not_precreate_destination(self):
        source = Path("libmpv_media_jobs.py").read_text(encoding="utf-8")
        decode = source[source.index("def decode_audio_wav("):]
        decode = decode[:decode.index("def measure_loudness_lufs")]
        self.assertLess(decode.index("os.unlink(output)"), decode.index("job = OfflineMpvJob()"))
        self.assertLess(decode.index("job = OfflineMpvJob()"), decode.index('job.option("ao", "pcm")'))

    def test_loudness_measurement_prefers_native_lavfi_without_wav(self):
        import libmpv_media_jobs

        with mock.patch.object(
            libmpv_media_jobs,
            "_measure_loudness_lavfi",
            return_value=(-16.4, -0.7),
        ) as native, mock.patch.object(
            libmpv_media_jobs,
            "decode_audio_wav",
            side_effect=AssertionError("native measurement should avoid WAV rendering"),
        ):
            result = libmpv_media_jobs.measure_loudness_lufs(
                "/tmp/song.mp3", timeout=37.0)

        self.assertEqual(result, (-16.4, -0.7))
        native.assert_called_once_with(
            "/tmp/song.mp3", timeout=37.0,
            start_seconds=None, duration_seconds=None)

    def test_loudness_measurement_falls_back_when_native_filter_unavailable(self):
        import libmpv_media_jobs

        with mock.patch.object(
            libmpv_media_jobs,
            "_measure_loudness_lavfi",
            side_effect=RuntimeError("filter unavailable"),
        ), mock.patch.object(
            libmpv_media_jobs,
            "decode_audio_wav",
            return_value="/tmp/rendered.wav",
        ) as decode, mock.patch.object(
            libmpv_media_jobs,
            "_measure_wav_lufs",
            return_value=(-18.2, -1.1),
        ), mock.patch.object(libmpv_media_jobs.os, "unlink") as unlink:
            result = libmpv_media_jobs.measure_loudness_lufs(
                "/tmp/song.mp3", timeout=44.0)

        self.assertEqual(result, (-18.2, -1.1))
        decode.assert_called_once_with(
            "/tmp/song.mp3", sample_rate=48000, channels=2,
            start_seconds=None, duration_seconds=None, timeout=44.0)
        unlink.assert_called_once_with("/tmp/rendered.wav")

    def test_loudness_failure_reports_no_decodable_audio_and_both_causes(self):
        import libmpv_media_jobs

        with mock.patch.object(
            libmpv_media_jobs, "_measure_loudness_lavfi",
            side_effect=RuntimeError("no integrated loudness"),
        ), mock.patch.object(
            libmpv_media_jobs, "decode_audio_wav",
            side_effect=RuntimeError("no PCM audio"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "no decodable audio.*no integrated loudness.*no PCM audio",
            ):
                libmpv_media_jobs.measure_loudness_lufs("/tmp/video-only.mp4")

    def test_async_loudness_skips_mp3g_archive_until_audio_is_extracted(self):
        with mock.patch.object(self.singws, "_diag") as diag, mock.patch.object(
            self.singws, "_loudness_workers_allowed"
        ) as workers_allowed, mock.patch.object(
            self.singws.threading, "Thread"
        ) as thread:
            self.singws.analyze_loudness_async("/tmp/Singer - Song.ZIP")

        workers_allowed.assert_not_called()
        thread.assert_not_called()
        diag.assert_called_once_with(
            "[LOUDNESS] analysis skipped reason=archive_requires_extraction "
            "file=Singer - Song.ZIP"
        )

    def test_libmpv_loudness_measurement_streams_wav_in_bounded_chunks(self):
        import libmpv_media_jobs

        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "tone.wav"
            with wave.open(str(wav_path), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(48000)
                frames = bytearray()
                for index in range(48000):
                    sample = int(16384 * math.sin(2.0 * math.pi * 1000.0 * index / 48000.0))
                    frames.extend(struct.pack("<hh", sample, sample))
                output.writeframes(frames)

            real_open = libmpv_media_jobs.wave.open
            requests = []

            class TrackingWave:
                def __init__(self, wrapped):
                    self.wrapped = wrapped

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.wrapped.close()

                def __getattr__(self, name):
                    return getattr(self.wrapped, name)

                def readframes(self, count):
                    requests.append(count)
                    return self.wrapped.readframes(count)

            with mock.patch.object(
                libmpv_media_jobs.wave,
                "open",
                side_effect=lambda path, mode: TrackingWave(real_open(path, mode)),
            ):
                lufs, peak_db = libmpv_media_jobs._measure_wav_lufs(str(wav_path))

        self.assertTrue(requests)
        self.assertLessEqual(max(requests), 65536)
        self.assertIsNotNone(lufs)
        self.assertAlmostEqual(peak_db, -6.02, places=1)

    def test_session_location_failure_backs_off_and_uses_saved_coordinates(self):
        app = make_app(self.singws)
        app.settings.update({
            "base_url": "https://example.test",
            "user": "venue",
            "api_key": "secret",
            "session_location_latitude": "47.6000",
            "session_location_longitude": "-122.3000",
            "session_location_auto_detect": True,
            "session_location_detected_at": 0,
        })
        app.save_settings = lambda: None

        with mock.patch.object(
            app,
            "_detect_current_device_location",
            return_value=(None, "temporary failure"),
        ) as detect:
            first = app._session_location_payload(allow_auto_detect=True)
            second = app._session_location_payload(allow_auto_detect=True)

        self.assertEqual(detect.call_count, 1)
        self.assertEqual(first["location_source"], "manual_fallback")
        self.assertEqual(second["latitude"], 47.6)
        self.assertGreater(app.__dict__["_session_location_retry_after"], time.monotonic())

    def test_background_clock_logging_is_sampled_without_delaying_eos_poll(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        poll = source[source.index("    def _poll_end(self):"):]
        poll = poll[:poll.index("    def stop(self, reason")]
        self.assertIn("if self._poll_count % 8 == 0:", poll)
        self.assertIn("if self._poll_count % 240 == 0:", poll)
        self.assertLess(poll.index("if self._poll_count % 240 == 0:"), poll.index("if self.plugin.backgroundVideoAtEnd():"))

    def test_library_volume_worker_cancel_stops_before_next_track(self):
        worker = self.singws.AnalyzeLibraryWorker([
            ("Karaoke", "/tmp/one.mp3", "One"),
            ("Karaoke", "/tmp/two.mp3", "Two"),
        ])
        measured = []

        def fake_measure(path, cancel_check=None, mode="full", session=None):
            measured.append(path)
            worker.cancel()
            self.assertTrue(cancel_check())
            return -20.0, -2.0

        with mock.patch("libmpv_media_jobs.IsolatedLoudnessSession.measure_karaoke_transition",
                             side_effect=lambda path, **kw: fake_measure(path, worker.is_cancelled) + (180.0, 0.0, 180.0)), \
             mock.patch.object(self.singws, "_loudness_file_sig", return_value=(1, 2)), \
             mock.patch.object(self.singws, "_loudness_save_cache"):
            worker.run()

        self.assertEqual(measured, ["/tmp/one.mp3"])

    def test_library_volume_worker_extracts_zip_mp3_and_caches_by_archive(self):
        with tempfile.TemporaryDirectory() as td:
            package = Path(td) / "song.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("nested/song.cdg", b"cdg")
                archive.writestr("nested/song.mp3", b"mp3 audio")
            worker = self.singws.AnalyzeLibraryWorker([
                ("Karaoke", str(package), "Zipped Song", str(package)),
            ])
            measured = []

            def fake_measure(path, cancel_check=None, mode="full", session=None):
                measured.append(path)
                self.assertTrue(Path(path).is_file())
                self.assertTrue(str(path).endswith(".mp3"))
                return -18.0, -2.0

            with mock.patch("libmpv_media_jobs.IsolatedLoudnessSession.measure_karaoke_transition",
                             side_effect=lambda path, **kw: fake_measure(path, worker.is_cancelled) + (180.0, 0.0, 180.0)), \
                 mock.patch.object(self.singws, "_loudness_save_cache"), \
                 mock.patch.dict(self.singws._loudness_cache, {}, clear=True):
                worker.run()
                cached = dict(self.singws._loudness_cache[str(package)])

            self.assertEqual(len(measured), 1)
            self.assertFalse(Path(measured[0]).exists())
            self.assertEqual(cached["size"], package.stat().st_size)
            self.assertEqual(cached["mode"], "full")

    def test_fast_loudness_result_never_applies_positive_gain(self):
        path = "/tmp/fast-song.mp3"
        with mock.patch.object(self.singws, "_loudness_file_sig", return_value=(1, 2)), \
             mock.patch.dict(self.singws._loudness_cache, {
                 path: {"i": -24.0, "peak_db": -12.0, "mtime": 1, "size": 2, "mode": "fast"},
             }, clear=True):
            gain = self.singws.loudness_gain_db_cached(path)

        self.assertEqual(gain, 0.0)

    def test_full_scan_upgrades_fast_cache_and_backfills_missing_transitions(self):
        app = make_app(self.singws)
        app.tracks = [{"path": "/tmp/song.mp3", "display": "Song"}]
        app._karaoke_normalize_active = lambda: True
        app._bg_normalize_setting_active = lambda: False
        app._any_loudness_normalization_active = lambda: True
        app._karaoke_normalize_bypass_reason = lambda: ""

        with mock.patch.object(os.path, "exists", return_value=True), \
             mock.patch.object(self.singws, "loudness_gain_db_cached", return_value=0.0), \
             mock.patch.object(self.singws, "loudness_info_cached", return_value={"mode": "fast"}):
            fast_items = app._library_loudness_analysis_items(mode="fast")
            full_items = app._library_loudness_analysis_items(mode="full")
        with mock.patch.object(os.path, "exists", return_value=True), \
             mock.patch.object(self.singws, "loudness_gain_db_cached", return_value=0.0), \
             mock.patch.object(self.singws, "loudness_info_cached", return_value={"mode": "full"}):
            completed_items = app._library_loudness_analysis_items(mode="full")

        self.assertEqual(fast_items, [])
        self.assertEqual(len(full_items), 1)
        self.assertEqual(len(completed_items), 1)

        complete = SimpleNamespace(audio_start=0.2, audio_end=180.0)
        with mock.patch.object(os.path, "exists", return_value=True), \
             mock.patch.object(self.singws, "loudness_gain_db_cached", return_value=0.0), \
             mock.patch.object(self.singws, "loudness_info_cached", return_value={"mode": "full"}), \
             mock.patch.object(self.singws, "_transition_analysis_cached_sync", return_value=complete):
            fully_complete_items = app._library_loudness_analysis_items(mode="full")
        self.assertEqual(fully_complete_items, [])

    def test_processing_text_auto_dismisses_by_default(self):
        app = make_app(self.singws)
        app.processing_label = FakeStatusLabel()
        app._processing_notification_timer = FakeSingleShotTimer()
        app._processing_notification_text = ""

        self.singws.KaraokeApp._set_processing_text(app, "Import Complete")

        self.assertEqual(app.processing_label.text(), "Import Complete")
        self.assertEqual(app._processing_notification_text, "Import Complete")
        self.assertEqual(app._processing_notification_timer.started, [self.singws.PROCESSING_NOTIFICATION_TIMEOUT_MS])

        self.singws.KaraokeApp._clear_processing_notification(app)

        self.assertEqual(app.processing_label.text(), "")
        self.assertEqual(app._processing_notification_text, "")

    def test_processing_progress_can_opt_out_of_auto_dismiss(self):
        app = make_app(self.singws)
        app.processing_label = FakeStatusLabel()
        app._processing_notification_timer = FakeSingleShotTimer()
        app._processing_notification_text = "old message"

        self.singws.KaraokeApp._set_processing_text(app, "Building search index…", auto_dismiss_ms=None)

        self.assertEqual(app.processing_label.text(), "Building search index…")
        self.assertEqual(app._processing_notification_text, "")
        self.assertEqual(app._processing_notification_timer.started, [])
        self.assertEqual(app._processing_notification_timer.stopped, 1)

    def test_mp3g_queue_duration_reaches_timer_and_transport(self):
        queue_source = inspect.getsource(self.singws.KaraokeApp.play_next_file)
        zip_source = inspect.getsource(self.singws.KaraokeApp.play_mp3g_zip)
        wrapper_source = inspect.getsource(self.singws.KaraokeApp.play_cdg_mp3_dual)

        self.assertIn("self.probe_mp3g_duration(dur_path)", queue_source)
        self.assertGreaterEqual(queue_source.count("duration_seconds=current_song_dur"), 4)
        self.assertIn("duration_seconds=effective_duration", zip_source)
        self.assertIn("duration_seconds=duration_seconds", wrapper_source)
        mpv_source = inspect.getsource(self.singws.KaraokeApp._start_mpv_karaoke_transport)
        self.assertIn("self._arm_audio_end_floor(audio_path, duration_seconds)", mpv_source)

    def test_server_off_background_follows_waitlist_state(self):
        app = make_app(self.singws)
        app._is_requests_accepting_cached = lambda: bool(app.settings["requests_accepting"])
        app._is_waitlist_enabled_cached = lambda: bool(app.settings["use_waiting_for_add"])
        app._default_background_path = lambda: "/default.png"

        with tempfile.TemporaryDirectory() as folder:
            normal = os.path.join(folder, "normal.png")
            waitlist_on = os.path.join(folder, "waitlist-on.png")
            waitlist_off = os.path.join(folder, "waitlist-off.png")
            for path in (normal, waitlist_on, waitlist_off):
                Path(path).touch()
            app.settings.update({
                "background_image_path": normal,
                "background_closed_waitlist_on_image_path": waitlist_on,
                "background_closed_waitlist_off_image_path": waitlist_off,
                "background_slideshow_enabled": False,
            })

            app.settings["requests_accepting"] = True
            app.settings["use_waiting_for_add"] = True
            self.assertEqual(self.singws.KaraokeApp._resolve_idle_background_path(app), normal)

            app.settings["requests_accepting"] = False
            self.assertEqual(self.singws.KaraokeApp._resolve_idle_background_path(app), waitlist_on)

            app.settings["use_waiting_for_add"] = False
            self.assertEqual(self.singws.KaraokeApp._resolve_idle_background_path(app), waitlist_off)

    def test_waitlist_toggle_refreshes_idle_background(self):
        source = inspect.getsource(self.singws.KaraokeApp._set_waitlist_enabled_local)
        self.assertIn("self._apply_idle_background(force=False", source)

    # --- 2026-08-16 show: loudness scan leak, stalls, crash dialog ----------

    def test_loudness_session_reuses_one_mpv_core(self):
        """One core per pass, not one per track.

        A fresh core leaked ~1 MB per track that mpv_terminate_destroy never
        returned, growing the app to 8.6 GB across a five-hour library scan.
        """
        import libmpv_media_jobs

        created = []

        class FakeJob:
            def __init__(self):
                created.append(self)
                self.closed = False
                self.loads = 0

            def option(self, *_a):
                pass

            def initialize(self):
                pass

            def request_log_messages(self, _level):
                pass

            def command(self, *_a):
                self.loads += 1

            def wait_for_end(self, _timeout, messages=None):
                if messages is not None:
                    messages.append("I: -14.0 LUFS  Peak: -1.0 dBFS")

            def close(self):
                self.closed = True

        with mock.patch.object(libmpv_media_jobs, "OfflineMpvJob", FakeJob):
            with libmpv_media_jobs.LoudnessSession() as session:
                for _ in range(25):
                    self.assertEqual(session.measure("/tmp/song.mp3"), (-14.0, -1.0))

        self.assertEqual(len(created), 1, "a core was created per track")
        self.assertEqual(created[0].loads, 25)
        self.assertTrue(created[0].closed, "session core was not released")

    def test_loudness_session_disables_itself_after_repeated_failures(self):
        """An old libmpv without ebur128 must fall back, not fail every track."""
        import libmpv_media_jobs

        created = []

        class BrokenJob:
            def __init__(self):
                created.append(self)

            def option(self, *_a):
                pass

            def initialize(self):
                raise RuntimeError("no ebur128 in this build")

            def request_log_messages(self, _level):
                pass

            def close(self):
                pass

        with mock.patch.object(libmpv_media_jobs, "OfflineMpvJob", BrokenJob):
            session = libmpv_media_jobs.LoudnessSession()
            for _ in range(3):
                self.assertTrue(session.usable)
                with self.assertRaises(RuntimeError):
                    session.measure("/tmp/song.mp3")
            self.assertFalse(session.usable, "session kept retrying a dead core")
            with self.assertRaises(RuntimeError):
                session.measure("/tmp/song.mp3")
        # Three attempts, then it stops constructing cores entirely.
        self.assertEqual(len(created), 3)

    def test_measure_loudness_falls_back_when_session_fails(self):
        """A failing session must not lose the measurement."""
        class DeadSession:
            usable = True

            def measure(self, *_a, **_k):
                raise RuntimeError("core died")

            def measure_fast(self, *_a, **_k):
                raise RuntimeError("core died")

        with mock.patch("libmpv_media_jobs.measure_loudness_lufs",
                        return_value=(-13.0, -1.5)) as plain:
            lufs, peak = self.singws._measure_loudness_lufs(
                "/tmp/song.mp3", session=DeadSession())
        self.assertEqual((lufs, peak), (-13.0, -1.5))
        plain.assert_called_once()

    def test_library_scan_holds_while_karaoke_plays(self):
        """Scanning under a live song caused 744 GUI stalls on 2026-08-16."""
        playing = {"value": True}
        measured = []

        worker = self.singws.AnalyzeLibraryWorker(
            [("Karaoke", "/tmp/one.mp3", "One")],
            should_hold=lambda: playing["value"],
        )
        worker._PLAYBACK_HOLD_POLL_S = 0.01
        held = []
        worker.holding.connect(held.append)

        def fake_measure(path, cancel_check=None, mode="full", session=None):
            measured.append(path)
            return -20.0, -2.0

        def release():
            # Nothing may be measured until karaoke stops.
            self.assertEqual(measured, [])
            playing["value"] = False

        import threading
        threading.Timer(0.05, release).start()

        with mock.patch("libmpv_media_jobs.IsolatedLoudnessSession.measure_karaoke_transition",
                             side_effect=lambda path, **kw: fake_measure(path, worker.is_cancelled) + (180.0, 0.0, 180.0)), \
             mock.patch.object(self.singws, "_loudness_file_sig", return_value=(1, 2)), \
             mock.patch.object(self.singws, "_loudness_save_cache"):
            worker.run()

        self.assertEqual(measured, ["/tmp/one.mp3"], "scan never resumed")
        self.assertEqual(held, [True, False], "hold state was not reported")

    def test_library_scan_hold_is_interrupted_by_cancel(self):
        """Cancelling during a hold must not block until playback ends."""
        worker = self.singws.AnalyzeLibraryWorker(
            [("Karaoke", "/tmp/one.mp3", "One")],
            should_hold=lambda: True,
        )
        worker._PLAYBACK_HOLD_POLL_S = 0.01
        measured = []

        def fake_measure(path, cancel_check=None, mode="full", session=None):
            measured.append(path)
            return -20.0, -2.0

        import threading
        threading.Timer(0.05, worker.cancel).start()

        with mock.patch("libmpv_media_jobs.IsolatedLoudnessSession.measure_karaoke_transition",
                             side_effect=lambda path, **kw: fake_measure(path, worker.is_cancelled) + (180.0, 0.0, 180.0)), \
             mock.patch.object(self.singws, "_loudness_save_cache"):
            worker.run()

        self.assertEqual(measured, [], "cancelled scan still measured a track")

    def test_log_package_leaves_no_partial_zip_behind(self):
        """A failed package must not orphan an unopenable ZIP.

        On 2026-08-16 packaging died partway and left a 712-byte file with no
        end-of-central-directory record, indistinguishable from a real bundle.
        """
        import zipfile as zf_mod

        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            (logs / "singws_2026-08-16.log").write_text("hello", encoding="utf-8")

            real_zipfile = zf_mod.ZipFile

            class ExplodingZip(real_zipfile):
                def writestr(self, *_a, **_k):
                    raise MemoryError("out of memory mid-package")

            with mock.patch.object(self.singws, "LOGS_DIR", logs), \
                 mock.patch.object(self.singws, "_recent_log_files",
                                   return_value=[logs / "singws_2026-08-16.log"]), \
                 mock.patch.object(self.singws, "flush_log_queue"), \
                 mock.patch.object(zf_mod, "ZipFile", ExplodingZip):
                package, _files, error = self.singws.prepare_log_email_package(days=3)

            # The caller must be told, and must not be handed a broken archive.
            self.assertIsNone(package)
            self.assertIn("Failed to package logs", error)
            leftovers = [p.name for p in logs.iterdir() if p.suffix in (".zip", ".partial")]
            self.assertEqual(leftovers, [], f"partial package left behind: {leftovers}")

    def test_crash_log_email_thread_logs_its_own_failure(self):
        """A throw here killed the daemon thread with no log line at all."""
        work = self.singws.maybe_auto_send_crash_logs
        source = inspect.getsource(work)
        self.assertIn("def _work():", source)
        body = source[source.index("def _work():"):]
        self.assertIn("try:", body)
        self.assertIn("auto crash send failed", body)


class KaraFunHistoryReAddTests(unittest.TestCase):
    """A KaraFun song in a singer's history has to be re-addable.

    Its path is the synthetic "karafun_streaming:<id>" reference, which is
    never present in the library index or tracks list, so the local-only
    lookup in _resolve_history_song_track always failed and the operator got
    "Could not match this history song to a local track". The stored catalog
    id is a display reference: playback drives KaraFun.app by artist/title
    search, so an id the server has since renumbered must not block the add.
    """

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def _app(self):
        app = make_app(self.singws)
        # An empty library: these entries are never in it, which is the point.
        app.tracks = []
        app._track_path_index = {}
        app._track_path_index_signature = None
        return app

    def test_karafun_history_song_resolves_without_a_local_track(self):
        app = self._app()
        song = {
            "artist": "Sugarcult",
            "title": "Memory",
            "provider": "karafun_streaming",
            "provider_track_id": "kf_2219191",
            "songid": "kf_2219191",
            "song_type": "KARAFUN_STREAMING",
            "path": "karafun_streaming:kf_2219191",
        }
        track = self.singws.KaraokeApp._resolve_history_song_track(app, song, "Dan")
        self.assertIsInstance(track, dict)
        self.assertEqual(track["provider"], "karafun_streaming")
        self.assertEqual(track["artist"], "Sugarcult")
        self.assertEqual(track["title"], "Memory")
        self.assertEqual(track["availability_status"], "externally_controlled")
        self.assertEqual(track["path"], "karafun_streaming:kf_2219191")

    def test_history_row_recorded_with_provider_local_still_resolves(self):
        """Some rows kept provider='local' with the streaming path and songid."""
        app = self._app()
        song = {
            "artist": "Sugarcult",
            "title": "Memory",
            "provider": "local",
            "provider_track_id": "",
            "songid": "kf_3168777",
            "song_type": "KARAFUN_STREAMING",
            "path": "karafun_streaming:kf_3168777",
        }
        track = self.singws.KaraokeApp._resolve_history_song_track(app, song, "Dan")
        self.assertIsInstance(track, dict)
        self.assertEqual(track["provider"], "karafun_streaming")
        self.assertEqual(track["provider_track_id"], "kf_3168777")

    def test_reference_is_recovered_from_the_path_when_nothing_else_has_it(self):
        app = self._app()
        song = {
            "artist": "Gigi Perez",
            "title": "Sailor Song",
            "path": "karafun_streaming:kf_3178824",
        }
        track = self.singws.KaraokeApp._resolve_history_song_track(app, song, "Shawn")
        self.assertIsInstance(track, dict)
        self.assertEqual(track["provider_track_id"], "kf_3178824")

    def test_playback_search_never_uses_the_catalog_id(self):
        """The id may be stale; the KaraFun handoff must search artist/title."""
        app = self._app()
        entry = {
            "artist": "Sugarcult",
            "title": "Memory",
            "provider_track_id": "kf_2219191",
            "display_name": "Sugarcult - Memory - KaraFun",
        }
        queries = self.singws.KaraokeApp._karafun_search_queries_for_entry(app, entry)
        self.assertTrue(queries)
        for query in queries:
            self.assertNotIn("kf_", query.lower())
        self.assertEqual(queries[0], "Sugarcult Memory")

    def test_a_local_karafun_branded_file_is_still_matched_locally(self):
        """A KARAFUN-branded MP4 on disk must not be mistaken for a stream."""
        app = self._app()
        song = {
            "artist": "Sugarcult",
            "title": "Bouncing Off the Walls",
            "provider": "local",
            "songid": "KARAFUN",
            "disc_id": "KARAFUN",
            "song_type": "MP4",
            "path": "/Music/KARAFUN - Sugarcult - Bouncing Off the Walls.mp4",
        }
        built = self.singws.KaraokeApp._karafun_track_from_history_song(app, song)
        self.assertIsNone(built, "a local file must not be rebuilt as a streaming reference")

    def test_a_plain_local_song_is_untouched_by_the_karafun_path(self):
        app = self._app()
        song = {
            "artist": "Johnny J",
            "title": "Wasting My Time",
            "provider": "local",
            "song_type": "ZIP",
            "path": "/Music/KV 08154 - Default - Wasting My Time.zip",
        }
        self.assertIsNone(self.singws.KaraokeApp._karafun_track_from_history_song(app, song))

    def test_a_karafun_row_with_no_artist_or_title_is_not_invented(self):
        app = self._app()
        song = {"provider": "karafun_streaming", "path": "karafun_streaming:kf_1"}
        self.assertIsNone(self.singws.KaraokeApp._karafun_track_from_history_song(app, song))


class BrandPickerEquivalenceTests(unittest.TestCase):
    """"KARAOKE VERSION" and "KV" are one brand, and must cost one slot.

    They always collapsed once stored -- canonical_disc_brand maps both to KV
    and normalize_disc_priority dedupes them -- but the pickers were built from
    raw disc ids, so the same brand was offered under many separate names.
    """

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_karaoke_version_and_kv_are_the_same_brand(self):
        canonical = self.singws.canonical_disc_brand
        for spelling in (
            "KV", "KARAOKE VERSION", "Karaoke Version", "karaokeversion",
            "KARAOKE-VERSION", "KARAOKE_VERSION",
        ):
            self.assertEqual(canonical(spelling), "KV", f"{spelling!r} should canonicalize to KV")

    def test_disc_numbered_variants_collapse_to_the_brand(self):
        canonical = self.singws.canonical_disc_brand
        for spelling in ("KARAOKE VERSION 00", "KV 00", "KV 43403", "KV92015"):
            self.assertEqual(canonical(spelling), "KV", f"{spelling!r} should canonicalize to KV")

    def test_listing_both_spellings_consumes_one_priority_slot(self):
        normalize = self.singws.normalize_disc_priority
        self.assertEqual(normalize("KV, KARAOKE VERSION"), ["KV"])
        self.assertEqual(normalize("KARAOKE VERSION, KV, SOUND CHOICE, SC"), ["KV", "SC"])
        # The cap is 10 brands; duplicates must not eat into it.
        crowded = "KARAOKE VERSION, KV, KARAOKEVERSION, SC, SOUND CHOICE, ZM, ZOOM, CB, PT, SF, SBI, ME, CC"
        picked = normalize(crowded, max_items=10)
        self.assertEqual(len(picked), len(set(picked)), "priority list must not contain duplicates")
        self.assertEqual(picked.count("KV"), 1)
        self.assertIn("CC", picked, "room freed by dedupe should reach the later brands")

    def test_brand_picker_offers_each_brand_once(self):
        tracks = (
            [{"disc_id": "KARAOKE VERSION 00"}] * 5
            + [{"disc_id": "KV"}] * 3
            + [{"disc_id": "KV 43403"}, {"disc_id": "KVMP4 00"}]
            + [{"disc_id": "SOUND CHOICE"}, {"disc_id": "SC 1234"}]
            + [{"disc_id": "WSK"}, {"disc_id": ""}, {}]
        )
        choices = self.singws.library_brand_choices(tracks)
        self.assertEqual(choices.count("KV"), 1, "Karaoke Version must appear once, not once per disc")
        self.assertNotIn("KARAOKE VERSION 00", choices)
        self.assertNotIn("KV 43403", choices)
        self.assertIn("SC", choices)
        self.assertIn("WSK", choices, "an unaliased brand present in the library is still offered")
        self.assertEqual(len(choices), len(set(choices)))

    def test_brand_picker_is_ordered_by_library_coverage(self):
        tracks = [{"disc_id": "SC"}] * 2 + [{"disc_id": "KARAOKE VERSION 00"}] * 9 + [{"disc_id": "WSK"}]
        self.assertEqual(self.singws.library_brand_choices(tracks), ["KV", "SC", "WSK"])

    def test_per_disc_codes_are_not_offered_as_brands(self):
        tracks = [{"disc_id": "THCOL04 01"}, {"disc_id": "PAN2006 01"}, {"disc_id": "SFMW849 01"}]
        choices = self.singws.library_brand_choices(tracks)
        self.assertNotIn("THCOL04 01", choices)
        self.assertNotIn("PAN2006 01", choices)

    def test_a_cancelled_build_returns_nothing(self):
        tracks = [{"disc_id": "KV"}] * 4096
        self.assertEqual(self.singws.library_brand_choices(tracks, should_cancel=lambda: True), [])


class KaraFunWrongSongTests(unittest.TestCase):
    """2026-08-16 22:31: asked for Sugarcult - Memory, played Memory from Cats.

    The log shows the right query WAS tried, twice, and both attempts died on
    "Invalid index" because KaraFun's window was still being built one second
    after launch. Each failure fell through to the next, looser query, so the
    third attempt searched the bare title and matched a different song.
    """

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_window_not_ready_errors_are_transient(self):
        transient = self.singws.KaraokeApp._is_karafun_ui_not_ready_error
        for message in (
            'Can\u2019t get splitter group 1 of window "Discover" of application process "KaraFun". Invalid index.',
            'Can\u2019t get item 2 of every window of application process "KaraFun". Invalid index.',
            "NSAppleScriptErrorNumber = \"-1719\"",
        ):
            self.assertTrue(transient(message), f"should be treated as transient: {message[:60]}")

    def test_permission_errors_are_not_treated_as_transient(self):
        """These must still abort and prompt, never spin on a retry."""
        transient = self.singws.KaraokeApp._is_karafun_ui_not_ready_error
        self.assertFalse(transient("NSAppleScriptErrorNumber = -1743 not authorized to send apple events"))
        self.assertFalse(transient("osascript is not allowed assistive access (-25211)"))
        self.assertFalse(transient(""))

    def test_a_transient_error_retries_the_same_query(self):
        source = inspect.getsource(self.singws.KaraokeApp._automate_karafun_search_and_play)
        self.assertIn("_is_karafun_ui_not_ready_error", source)
        self.assertIn("ui_retry", source)
        self.assertIn("retrying same query", source)

    def test_search_script_matches_the_artist_row_when_the_artist_is_known(self):
        app = make_app(self.singws)
        script = "\n".join(self.singws.KaraokeApp._karafun_search_script(
            app, query="Memory", safe_title="Memory", safe_artist="Sugarcult", require_exact_title=True,
        ))
        self.assertIn("Sugarcult", script, "the artist must reach the matcher")
        self.assertIn("artistMatched", script)
        self.assertIn('return "FOUND|"', script)
        self.assertIn('return "TITLE_ONLY|"', script,
                      "a title-only hit must be reported as the weaker verdict")

    def test_script_is_unchanged_when_no_artist_is_known(self):
        app = make_app(self.singws)
        script = "\n".join(self.singws.KaraokeApp._karafun_search_script(
            app, query="Memory", safe_title="Memory", require_exact_title=True,
        ))
        self.assertNotIn("artistMatched", script)
        self.assertNotIn("TITLE_ONLY", script)
        self.assertIn('return "FOUND|"', script)

    def test_an_artistless_query_only_accepts_an_artist_verified_row(self):
        source = inspect.getsource(self.singws.KaraokeApp._automate_karafun_search_and_play)
        self.assertIn("query_has_artist", source)
        self.assertIn('accepted = {"FOUND"}', source)
        self.assertIn("artist_unconfirmed", source)

    def test_the_queries_still_run_specific_to_general(self):
        app = make_app(self.singws)
        entry = {"artist": "Sugarcult", "title": "Memory"}
        queries = self.singws.KaraokeApp._karafun_search_queries_for_entry(app, entry)
        self.assertEqual(queries[0], "Sugarcult Memory")
        self.assertIn("Memory", queries)
        self.assertLess(queries.index("Sugarcult Memory"), queries.index("Memory"))


class KaraFunCompletionClockTests(unittest.TestCase):
    """Background music came up ~30s before the song ended.

    The duration fallback counted from the handoff, but KaraFun spent ~45s
    launching, handing off the renderer and going fullscreen before a note
    played, so the clock hit zero mid-outro, the rotation advanced and the BG
    deck started over the ending.
    """

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()
        cls.source = inspect.getsource(cls.singws.KaraokeApp._start_karafun_completion_monitor)

    def test_the_fallback_counts_from_confirmed_playback(self):
        self.assertIn("playback_confirmed_at", self.source)
        self.assertIn("fallback_origin = playback_clock_origin if playback_clock_origin is not None else started",
                      self.source)

    def test_an_explicit_post_handoff_play_keeps_its_exact_start_clock(self):
        self.assertIn('if bool(entry.get("karafun_playback_clock_started_at"))', self.source)
        self.assertIn("playback_clock_origin = (", self.source)

    def test_the_fallback_clock_cannot_rebase_itself(self):
        """The fallback's own countdown is not evidence that playback began."""
        self.assertIn("if not remaining_from_fallback:", self.source)
        body = self.source[self.source.index("if not remaining_from_fallback:"):]
        self.assertIn("_confirm_playback()", body[:160])

    def test_real_playback_signals_do_rebase(self):
        self.assertIn("if playing_hint_count >= 2:", self.source)
        self.assertIn("_confirm_playback()", self.source)

    def test_the_launch_overhead_is_logged(self):
        self.assertIn("duration fallback rebased", self.source)

    def test_replaying_the_2026_08_16_timeline_no_longer_completes_early(self):
        """235s song; KaraFun took 45s to start. It must not complete at 235s."""
        started = 0.0
        fallback_duration = 235
        playback_confirmed_at = 45.0  # first playing=1 reading in the log

        def remaining_at(now, origin):
            return fallback_duration - int(now - origin)

        # Old behaviour: counted from the handoff.
        self.assertLessEqual(remaining_at(233.0, started), 5,
                             "old clock reached the completion threshold at ~233s")
        # New behaviour: the same instant still has most of the outro left.
        self.assertGreater(remaining_at(233.0, playback_confirmed_at), 5)
        self.assertEqual(remaining_at(233.0, playback_confirmed_at), 47)
        # And it does still complete, 45s later.
        self.assertLessEqual(remaining_at(278.0, playback_confirmed_at), 5)


class BackgroundMusicSoundboardHandoffTests(unittest.TestCase):
    """An Exitlude pad overlapping EOS twice left BGM silent after the callback entered."""

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()
        cls.source = inspect.getsource(cls.singws.KaraokeApp._start_bg_with_fade)

    def test_resume_waits_until_the_soundboard_releases_bass(self):
        self.assertIn("soundboard_active = any(", self.source)
        self.assertIn('getattr(pad, "_playing", False)', self.source)
        self.assertIn("self._schedule_bg_resume(250, reason=resume_reason)", self.source)
        self.assertLess(self.source.index("if soundboard_active:"),
                        self.source.index('self.bg_music.ensure_audible("bg_resume")'))

    def test_successful_resume_arms_real_playback_verification(self):
        self.assertIn("_bg_resume_verify_gen", self.source)
        self.assertIn("self._verify_bg_resume_started(gen)", self.source)
        verifier = inspect.getsource(self.singws.KaraokeApp._verify_bg_resume_started)
        self.assertIn("bg.is_effectively_playing()", verifier)
        self.assertIn("playback verification failed; resetting deck and retrying", verifier)
        self.assertIn('self._bg_resume_reason = "verified_retry"', verifier)
        self.assertIn('self._start_bg_with_fade_safe("verified_retry")', verifier)
        self.assertIn("playback still inactive after verified retry", verifier)


class TickerSurfaceReassertTests(unittest.TestCase):
    """The ticker vanished again after a KaraFun song returned the show screen.

    The ticker and the mpv hosts are native child views: they stack by creation
    order, not by the Qt widget tree, so raising the show window can bury a
    ticker that is still updating. Only the mpv-reveal and rotation-open paths
    re-raised it; every KaraFun restore path called vw.raise_() and did not.
    Closing and reopening the show screen was the operator's manual repair.
    """

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()
        cls.restore = inspect.getsource(cls.singws.KaraokeApp._restore_show_screen_from_karafun)

    def test_every_karafun_restore_path_reasserts_the_ticker(self):
        raises = self.restore.count("vw.raise_()")
        reasserts = self.restore.count("_schedule_show_ticker_reassert")
        self.assertGreaterEqual(raises, 2, "sanity: the restore still raises the show window")
        self.assertGreaterEqual(
            reasserts, raises,
            "every path that raises the show window must re-raise the ticker",
        )

    def test_the_already_fullscreen_path_reasserts_too(self):
        """This path returns early without raising, and still re-orders surfaces."""
        body = self.restore[self.restore.index("if vw.isFullScreen():"):]
        head = body[:body.index("return")]
        self.assertIn("_schedule_show_ticker_reassert", head)

    def test_the_fullscreen_ladder_runs_longer_than_a_plain_reveal(self):
        self.assertIn("delays=(0, 120, 400, 900)", self.restore)

    def test_the_reassert_scheduler_is_shared(self):
        source = inspect.getsource(self.singws.KaraokeApp._schedule_show_ticker_reassert)
        self.assertIn("QTimer.singleShot", source)
        self.assertIn("_reassert_show_ticker_surface", source)
        reveal = inspect.getsource(self.singws.KaraokeApp._set_native_video_hosts_visible) \
            if hasattr(self.singws.KaraokeApp, "_set_native_video_hosts_visible") else ""
        if reveal:
            self.assertIn("_schedule_show_ticker_reassert", reveal)


class LoudnessScanPlaybackHoldTests(unittest.TestCase):
    """The operator has to scan during songs; the pass is too slow otherwise.

    Holding under live playback is the right default (an unheld pass produced
    744 GUI stalls on 2026-08-16), but a five-hour show is nearly wall-to-wall
    playback, so a held scan barely advances. It is a setting now.
    """

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_holding_is_the_default(self):
        self.assertTrue(self.singws.DEFAULTS["loudness_scan_holds_for_playback"])

    def test_the_worker_never_holds_when_given_no_hold_callable(self):
        worker = self.singws.AnalyzeLibraryWorker([], mode="fast", should_hold=None)
        self.assertTrue(worker._hold_for_playback(), "no hold callable must not block the pass")

    def test_the_worker_holds_then_resumes_when_playback_ends(self):
        playing = {"value": True}
        worker = self.singws.AnalyzeLibraryWorker([], mode="fast", should_hold=lambda: playing["value"])
        worker._PLAYBACK_HOLD_POLL_S = 0.01

        def release():
            time.sleep(0.05)
            playing["value"] = False

        import threading as _threading
        _threading.Thread(target=release, daemon=True).start()
        self.assertTrue(worker._hold_for_playback())

    def test_a_held_scan_stays_cancellable(self):
        worker = self.singws.AnalyzeLibraryWorker([], mode="fast", should_hold=lambda: True)
        worker._PLAYBACK_HOLD_POLL_S = 0.01
        worker.cancel()
        self.assertFalse(worker._hold_for_playback(), "cancel must break the hold")

    def test_the_setting_reaches_the_worker(self):
        source = inspect.getsource(self.singws.KaraokeApp._start_library_loudness_scan) \
            if hasattr(self.singws.KaraokeApp, "_start_library_loudness_scan") else ""
        if not source:
            with open("0.2.18.1.py", "r", encoding="utf-8") as fh:
                source = fh.read()
        self.assertIn("loudness_scan_holds_for_playback", source)
        self.assertIn("if hold_for_playback else None", source)

    def test_the_dialog_crash_guard_is_in_the_callback_body(self):
        """The four "crashes" on 2026-08-16 came from this exact retry."""
        source = inspect.getsource(self.singws.KaraokeApp._bring_analyze_dialog_to_front)
        body = source[source.index("def _raise_again"):]
        self.assertIn("except RuntimeError:", body)


class SearchCoalescingTests(unittest.TestCase):
    """Searching "Eiffel 65" returned nothing though 9 copies are in the library.

    search_tracks() clears the results list, and when a worker is still running
    it stashes the query as _pending_search_query and returns, relying on the
    next results_ready to drain it. But it also calls requestInterruption() on
    that worker, and run() checks the flag immediately after its first emit and
    returns without emitting again. A keystroke landing in that window left the
    query stashed with nothing left to deliver it, and the list already empty.
    """

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_the_data_is_really_there(self):
        """Guards against 'it must be a bad index' being re-diagnosed."""
        run = inspect.getsource(self.singws.SongSearchThread.run)
        self.assertIn("song_index.search_songs", run)

    def test_run_stops_emitting_once_interrupted(self):
        """The behaviour that strands the pending query."""
        run = inspect.getsource(self.singws.SongSearchThread.run)
        self.assertIn("if self.isInterruptionRequested() or not self.fuzzy:", run)
        head = run[:run.index("if self.isInterruptionRequested() or not self.fuzzy:")]
        self.assertIn("self.results_ready.emit", head,
                      "the strict emit happens before the interruption check")

    def test_the_worker_exit_also_drains_the_pending_query(self):
        source = inspect.getsource(self.singws.KaraokeApp.search_tracks)
        self.assertIn("finished.connect", source)
        self.assertIn("_start_pending_search_if_needed", source)

    def test_the_drain_started_thread_also_drains_on_exit(self):
        source = inspect.getsource(self.singws.KaraokeApp._start_pending_search_if_needed)
        self.assertIn("finished.connect", source)

    def test_stale_results_still_drain(self):
        """The pre-existing drain path must not be lost."""
        source = inspect.getsource(self.singws.KaraokeApp._apply_db_search_results)
        stale = source[source.index("Ignore stale results"):]
        self.assertIn("_start_pending_search_if_needed(job_id)", stale[:400])

    def test_dropped_rows_are_no_longer_silent(self):
        source = inspect.getsource(self.singws.KaraokeApp._apply_db_search_results)
        self.assertIn("dropped", source)
        self.assertIn("row render failed", source)
        self.assertNotIn("                    except Exception:\n                        continue", source)


class LoudnessFailureMemoryTests(unittest.TestCase):
    """Undecodable files were re-analysed on every pass.

    One show's logs show 40 files that decode to nothing and 11 SKK006 ZIPs
    with no readable MP3 inside, each retried 5-6 times. Only successes were
    ever cached, on a job that already takes many hours.
    """

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "broken.mp3")
        with open(self.path, "wb") as fh:
            fh.write(b"not really audio")
        self.singws._loudness_cache.clear()
        self.singws._loudness_cache_loaded = True

    def tearDown(self):
        self.singws._loudness_cache.clear()
        self.tmp.cleanup()

    def test_a_failure_is_remembered(self):
        self.assertFalse(self.singws.loudness_failed_cached(self.path))
        self.singws._loudness_mark_failed(self.path, "no decodable audio")
        self.assertTrue(self.singws.loudness_failed_cached(self.path))
        self.assertEqual(
            self.singws._loudness_cache[self.path]["failure_version"],
            self.singws._LOUDNESS_FAILURE_CACHE_VERSION,
        )

    def test_a_replaced_file_is_retried(self):
        self.singws._loudness_mark_failed(self.path, "no decodable audio")
        self.assertTrue(self.singws.loudness_failed_cached(self.path))
        with open(self.path, "wb") as fh:
            fh.write(b"a different, larger file that might actually decode")
        self.assertFalse(
            self.singws.loudness_failed_cached(self.path),
            "changing the file must clear the failure record",
        )

    def test_a_failure_record_is_not_mistaken_for_a_measurement(self):
        self.singws._loudness_mark_failed(self.path, "no decodable audio")
        self.assertIsNone(self.singws.loudness_info_cached(self.path))

    def test_a_successful_measurement_is_not_reported_as_failed(self):
        sig = self.singws._loudness_file_sig(self.path)
        self.singws._loudness_cache[self.path] = {
            "i": -14.0, "peak_db": -1.0, "mtime": sig[0], "size": sig[1],
        }
        self.assertFalse(self.singws.loudness_failed_cached(self.path))
        self.assertIsNotNone(self.singws.loudness_info_cached(self.path))

    def test_an_unknown_file_is_not_reported_as_failed(self):
        self.assertFalse(self.singws.loudness_failed_cached(os.path.join(self.tmp.name, "nope.mp3")))
        self.assertFalse(self.singws.loudness_failed_cached(""))

    def test_the_scan_skips_files_that_already_failed(self):
        source = inspect.getsource(self.singws.AnalyzeLibraryWorker.run)
        self.assertIn("loudness_failed_cached(cache_key)", source)
        self.assertIn("skipped += 1", source)
        self.assertIn("_loudness_mark_failed", source)

    def test_the_zip_is_blacklisted_rather_than_its_temp_extraction(self):
        """For a ZIP, the measured path is a temp file that is deleted after."""
        measure = inspect.getsource(self.singws._measure_loudness_lufs)
        self.assertNotIn("_loudness_mark_failed", measure,
                         "marking here would record a temp path that no longer exists")
        run = inspect.getsource(self.singws.AnalyzeLibraryWorker.run)
        self.assertIn("_loudness_mark_failed(cache_key", run)

    def test_a_cancelled_scan_does_not_record_failures(self):
        """Cancelling mid-file must not blacklist a perfectly good track."""
        source = inspect.getsource(self.singws.AnalyzeLibraryWorker.run)
        for marker in ("_loudness_mark_failed(cache_key, \"no measurable loudness\")",
                       "_loudness_mark_failed(cache_key, str(e))"):
            idx = source.index(marker)
            preceding = source[:idx]
            self.assertIn("if not self.is_cancelled():", preceding[-300:],
                          "failure recording must be guarded by a cancellation check")

    def test_a_valid_zip_is_not_trapped_by_the_2026_08_29_failure_cache(self):
        with tempfile.TemporaryDirectory() as td:
            package = Path(td) / "valid.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("song.mp3", b"audio")
                archive.writestr("song.cdg", b"graphics")
            sig = self.singws._loudness_file_sig(str(package))
            with mock.patch.dict(self.singws._loudness_cache, {
                str(package): {
                    "failed": True,
                    "reason": "ZIP does not contain exactly one readable MP3",
                    "mtime": sig[0],
                    "size": sig[1],
                },
            }, clear=True), mock.patch.object(self.singws, "_loudness_cache_loaded", True):
                self.assertFalse(self.singws.loudness_failed_cached(str(package)))

    def test_a_structurally_invalid_zip_stays_failure_cached(self):
        with tempfile.TemporaryDirectory() as td:
            package = Path(td) / "invalid.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("song.cdg", b"graphics")
            sig = self.singws._loudness_file_sig(str(package))
            with mock.patch.dict(self.singws._loudness_cache, {
                str(package): {
                    "failed": True,
                    "reason": "ZIP does not contain exactly one readable MP3",
                    "mtime": sig[0],
                    "size": sig[1],
                },
            }, clear=True), mock.patch.object(self.singws, "_loudness_cache_loaded", True):
                self.assertTrue(self.singws.loudness_failed_cached(str(package)))

    def test_legacy_ambiguous_failures_are_retried_once(self):
        with tempfile.TemporaryDirectory() as td:
            package = Path(td) / "poisoned.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("song.mp3", b"audio")
                archive.writestr("song.cdg", b"graphics")
            sig = self.singws._loudness_file_sig(str(package))
            legacy = {
                "failed": True,
                "reason": "no measurable loudness",
                "mtime": sig[0],
                "size": sig[1],
            }
            current = {**legacy, "failure_version": self.singws._LOUDNESS_FAILURE_CACHE_VERSION}
            with mock.patch.object(self.singws, "_loudness_cache_loaded", True):
                self.singws._loudness_cache[str(package)] = legacy
                self.assertFalse(self.singws.loudness_failed_cached(str(package)))
                self.singws._loudness_cache[str(package)] = current
                self.assertTrue(self.singws.loudness_failed_cached(str(package)))

            media = str(Path(td) / "video.mp4")
            Path(media).write_bytes(b"video")
            sig = self.singws._loudness_file_sig(media)
            self.singws._loudness_cache[media] = {
                "failed": True,
                "reason": "Turbo helper failed: offline analysis helper response timed out",
                "mtime": sig[0],
                "size": sig[1],
            }
            self.assertFalse(self.singws.loudness_failed_cached(media))

    def test_failed_qml_texture_layer_is_removed_and_detached_painter_is_isolated(self):
        qml = self.singws.QML_TICKER_RT_SOURCE
        self.assertNotIn("id: movingNameLayer", qml)
        self.assertNotIn("layer.enabled: true", qml)
        self.assertIn("target: nameText", qml)
        init = inspect.getsource(self.singws.DetachedPainterTicker.__init__)
        sync = inspect.getsource(self.singws.DetachedPainterTicker.sync_surface_geometry)
        painter = inspect.getsource(self.singws.Ticker.paintEvent)
        self.assertIn("WA_DontCreateNativeAncestors", init)
        self.assertIn("WA_NativeWindow", init)
        self.assertIn("setTransientParent", sync)
        self.assertIn("self._order_surface_above_output(host)", sync)
        self.assertIn("queue_flash_started", painter)
        self.assertIn("timer_pulse_started", painter)
        self.assertIn("drawRoundedRect", painter)


try:  # the native mpv backend loads only where its bridge is available
    import mpv_playback_iina as _mpv_playback_mod
    import mpv_karaoke_transport as _mpv_transport_mod
    _MPV_BACKENDS_IMPORTABLE = True
except Exception:  # pragma: no cover - depends on the venv in use
    _mpv_playback_mod = None
    _mpv_transport_mod = None
    _MPV_BACKENDS_IMPORTABLE = False


@unittest.skipUnless(_MPV_BACKENDS_IMPORTABLE, "native mpv backend unavailable")
class CdgVisualOffsetTests(unittest.TestCase):
    """The live in-process backend applies the calibrated visual offset."""

    def test_the_native_backend_accepts_an_offset(self):
        mpv_playback = _mpv_playback_mod
        self.assertTrue(hasattr(mpv_playback.MpvPlaybackPlugin, "setVideoOffsetMs"))

    def test_the_offset_maps_to_native_audio_delay(self):
        mpv_playback = _mpv_playback_mod
        setter = inspect.getsource(mpv_playback.MpvPlaybackPlugin.setVideoOffsetMs)
        self.assertIn("singws_bridge_set_audio_delay", setter)
        self.assertIn("self._video_offset_ms / 1000.0", setter)

    def test_the_offset_is_clamped_and_defaults_to_zero(self):
        mpv_playback = _mpv_playback_mod
        plugin = mpv_playback.MpvPlaybackPlugin.__new__(mpv_playback.MpvPlaybackPlugin)
        plugin._video_offset_ms = 0
        plugin._handle = None
        plugin.log = lambda *_a, **_k: None
        self.assertEqual(plugin._video_offset_ms, 0)
        mpv_playback.MpvPlaybackPlugin.setVideoOffsetMs(plugin, 750)
        self.assertEqual(plugin._video_offset_ms, 750)
        mpv_playback.MpvPlaybackPlugin.setVideoOffsetMs(plugin, 99999)
        self.assertEqual(plugin._video_offset_ms, 3000, msg="must clamp to +/-3000ms")
        mpv_playback.MpvPlaybackPlugin.setVideoOffsetMs(plugin, -99999)
        self.assertEqual(plugin._video_offset_ms, -3000)
        mpv_playback.MpvPlaybackPlugin.setVideoOffsetMs(plugin, None)
        self.assertEqual(plugin._video_offset_ms, 0)

    def test_the_transport_forwards_rather_than_discarding(self):
        mpv_karaoke_transport = _mpv_transport_mod
        source = inspect.getsource(mpv_karaoke_transport.MpvKaraokeTransport.set_video_offset_ms)
        self.assertIn('hasattr(plugin, "setVideoOffsetMs")', source)
        self.assertIn("plugin.setVideoOffsetMs(value)", source)


class WaitlistAutomaticPromotionTests(unittest.TestCase):
    """Waitlisted requests always require an explicit host decision."""

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_reconcile_has_no_automatic_empty_slot_promotion(self):
        reconcile = inspect.getsource(self.singws.KaraokeApp._reconcile_remote_requests)
        self.assertNotIn("_promote_waitlisted_phone_replacement_into_empty_slot", reconcile)
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertNotIn("promoted phone replacement into preserved slot", source)


class KaraFunAutoStartRecoveryTests(unittest.TestCase):
    """2026-08-16 01:07: Los Enanitos Verdes never auto-started.

    The search succeeded (FOUND, correct 03:42 duration) and the result was
    activated. Then, with fast start on, the code did not probe KaraFun at all
    -- it hard-coded "PLAYING", logged 'play click skipped already playing',
    and skipped both the play click and the 12-attempt verify loop. KaraFun was
    not playing: 15s later the monitor read idle=1 playing=0, and it stayed
    that way until the operator pressed play 32s in.
    """

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()
        cls.monitor = inspect.getsource(cls.singws.KaraokeApp._start_karafun_completion_monitor)
        cls.automation = inspect.getsource(cls.singws.KaraokeApp._automate_karafun_search_and_play)

    def test_fast_start_records_that_playback_was_only_assumed(self):
        self.assertIn('entry["karafun_playback_assumed"] = True', self.automation)
        self.assertIn("assumed playback", self.automation)

    def test_the_play_control_is_shared_not_inlined(self):
        """The monitor needs the same press the automation path uses."""
        self.assertTrue(hasattr(self.singws.KaraokeApp, "_karafun_press_play_control"))
        press = inspect.getsource(self.singws.KaraokeApp._karafun_press_play_control)
        self.assertIn("_macos_native_mouse_click", press)
        self.assertIn('"PLAY|"', press)
        self.assertIn("_karafun_press_play_control()", self.automation)

    def test_managed_handoff_starts_the_selected_result_not_the_disabled_player(self):
        self.assertTrue(hasattr(self.singws.KaraokeApp, "_karafun_activate_result_for_playback"))
        activate = inspect.getsource(
            self.singws.KaraokeApp._karafun_activate_result_for_playback
        )
        self.assertIn('starts with "Results for "', activate)
        self.assertIn('perform action "AXRaise" of mainWindow', activate)
        self.assertIn("_macos_native_double_click", activate)
        self.assertIn("_karafun_activate_result_for_playback(", self.automation)

    def test_the_monitor_retries_the_matched_result_when_the_assumption_was_wrong(self):
        self.assertIn("playback_assumed", self.monitor)
        self.assertIn("recovery_pressed", self.monitor)
        self.assertIn("karafun_result_activation_point", self.automation)
        self.assertIn("karafun_result_activation_point", self.monitor)
        self.assertIn("_karafun_activate_result_for_playback", self.monitor)
        self.assertIn("_karafun_press_play_control()", self.monitor)
        self.assertIn("playback never started after", self.monitor)

    def test_monitor_reads_playback_menu_when_fullscreen_hides_control_window(self):
        self.assertIn('menu bar item "Playback"', self.monitor)
        self.assertIn('playbackToggleName is "pause"', self.monitor)
        self.assertIn('return "STATE|PLAYING"', self.monitor)
        self.assertLess(
            self.monitor.index('menu bar item "Playback"'),
            self.monitor.index('if (count of windows) is 0 then return ""'),
        )

    def test_recovery_presses_only_once(self):
        """A repeated press would toggle play/pause and silence a playing song."""
        self.assertIn("recovery_pressed = True", self.monitor)
        idx = self.monitor.index("recovery_pressed = True")
        guard = self.monitor[:idx]
        self.assertIn("not recovery_pressed", guard[-400:])

    def test_recovery_does_not_fire_once_playback_is_confirmed_or_reporting_playing(self):
        idx = self.monitor.index("recovery_pressed = True")
        guard = self.monitor[:idx]
        self.assertIn("playback_confirmed_at is None", guard[-400:])
        # The first live poll arrives around 14 seconds. Re-activating the
        # result despite its PLAYING state restarts an already audible song.
        self.assertIn("not playing_reported", guard[-400:])

    def test_recovery_is_skipped_entirely_when_play_was_actually_clicked(self):
        """Only the fast-start path assumes; the slow path really clicks play."""
        idx = self.monitor.index("recovery_pressed = True")
        self.assertIn("playback_assumed", self.monitor[:idx][-400:])

    def test_the_operator_is_warned_when_recovery_also_fails(self):
        self.assertIn("KaraFun is not playing this song", self.monitor)
        self.assertIn("notify=True", self.monitor)
        self.assertIn("KARAFUN_PLAYBACK_ALERT_DELAY_S", self.monitor)

    def test_the_delays_leave_room_for_a_slow_start(self):
        """KaraFun took ~19s from activation to playing in the working cases."""
        app = self.singws.KaraokeApp
        self.assertGreaterEqual(app.KARAFUN_PLAYBACK_RECOVERY_DELAY_S, 10.0)
        self.assertLess(app.KARAFUN_PLAYBACK_RECOVERY_DELAY_S, app.KARAFUN_PLAYBACK_ALERT_DELAY_S)
        self.assertLessEqual(app.KARAFUN_PLAYBACK_ALERT_DELAY_S, 60.0)


class PostShowAnalysisSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_external_karafun_reference_never_starts_local_loudness_decode(self):
        with mock.patch.object(self.singws, "_loudness_load_cache") as load_cache, \
             mock.patch.object(self.singws, "_measure_loudness_lufs") as measure:
            self.singws.analyze_loudness_async("karafun_streaming:kf_123")
        load_cache.assert_not_called()
        measure.assert_not_called()

    def test_playback_transition_cache_read_does_not_wait_for_save_lock(self):
        source = inspect.getsource(self.singws.transition_analysis_cached)
        loaded_branch = source[:source.index("with _transition_analysis_cache_lock")]
        self.assertIn("_transition_analysis_cache.get", loaded_branch)

    def test_isolated_worker_protocol_returns_transition_payload(self):
        import libmpv_media_jobs

        class FakeSession:
            def measure_transition(self, source, timeout=0):
                self.request = (source, timeout)
                return -14.0, -1.0, [-60.0, -12.0]

            def close(self):
                pass

        request = io.StringIO(json.dumps({
            "source": "/tmp/song.mp3", "mode": "transition", "timeout": 9.0,
        }) + "\n" + json.dumps({"command": "quit"}) + "\n")
        response = io.StringIO()
        with mock.patch.object(libmpv_media_jobs, "LoudnessSession", FakeSession):
            self.assertEqual(libmpv_media_jobs.run_isolated_analysis_worker(request, response), 0)
        line = response.getvalue().strip()
        self.assertTrue(line.startswith(libmpv_media_jobs._ANALYSIS_RESULT_PREFIX))
        payload = json.loads(line[len(libmpv_media_jobs._ANALYSIS_RESULT_PREFIX):])
        self.assertEqual(payload["envelope"], [-60.0, -12.0])

    def test_isolated_worker_protocol_returns_video_tail_metrics(self):
        import libmpv_media_jobs

        class FakeSession:
            def close(self):
                pass

        expected = [{"timestamp": 9.0, "mean_luma": 0.2, "difference": 0.1}]
        request = io.StringIO(json.dumps({
            "source": "/tmp/song.mp4", "mode": "video_tail",
            "duration": 12.0, "timeout": 9.0,
        }) + "\n" + json.dumps({"command": "quit"}) + "\n")
        response = io.StringIO()
        with mock.patch.object(libmpv_media_jobs, "LoudnessSession", FakeSession), \
             mock.patch.object(libmpv_media_jobs, "sample_video_tail_metrics", return_value=expected) as sampler:
            self.assertEqual(libmpv_media_jobs.run_isolated_analysis_worker(request, response), 0)
        sampler.assert_called_once_with(
            "/tmp/song.mp4", duration_seconds=12.0, timeout=9.0,
        )
        line = response.getvalue().strip()
        payload = json.loads(line[len(libmpv_media_jobs._ANALYSIS_RESULT_PREFIX):])
        self.assertEqual(payload["samples"], expected)

    def test_batch_worker_uses_recyclable_isolated_session(self):
        source = inspect.getsource(self.singws.AnalyzeLibraryWorker.run)
        self.assertIn("IsolatedLoudnessSession", source)
        self.assertIn("session.measure_video_tail", source)

    def test_karaoke_batch_uses_compact_transition_analysis(self):
        source = inspect.getsource(self.singws.AnalyzeLibraryWorker.run)
        self.assertIn("measure_karaoke_transition", source)
        self.assertIn("build_karaoke_transition_analysis", source)
        self.assertIn("_loudness_append_checkpoint", source)

    def test_isolated_analyzer_contains_decoder_noise_and_caches_bad_files(self):
        import libmpv_media_jobs

        start = inspect.getsource(libmpv_media_jobs.IsolatedLoudnessSession._start)
        self.assertIn("stderr=subprocess.DEVNULL", start)
        batch = inspect.getsource(self.singws.AnalyzeLibraryWorker.run)
        self.assertIn("loudness_failed_cached(cache_key)", batch)
        self.assertIn('_loudness_mark_failed(cache_key, "no measurable loudness")', batch)

    def test_batch_transition_results_flush_once_instead_of_every_track(self):
        source = inspect.getsource(self.singws.AnalyzeLibraryWorker.run)
        self.assertIn("persist=False", source)
        self.assertIn("_transition_analysis_cache.save()", source)


if __name__ == "__main__":
    unittest.main()
