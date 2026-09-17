import ast
import pathlib
import re
import sys
import types
import textwrap
import unittest
import importlib.util
from unittest import mock

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication


MAIN_SOURCE = pathlib.Path("0.2.18.1.py").read_text(encoding="utf-8")
BRIDGE_SOURCE = pathlib.Path("native/mpv_bridge/bridge.mm").read_text(encoding="utf-8")

def function_source(name: str) -> str:
    pattern = rf"(?ms)^    def {re.escape(name)}\(.*?^    def "
    match = re.search(pattern, MAIN_SOURCE)
    if match:
        return match.group(0).rsplit("\n    def ", 1)[0]
    pattern = rf"(?ms)^    def {re.escape(name)}\(.*?^class "
    match = re.search(pattern, MAIN_SOURCE)
    if match:
        return match.group(0).rsplit("\nclass ", 1)[0]
    raise AssertionError(f"Could not find function {name}")


class PerformanceSafetyTests(unittest.TestCase):
    def test_native_bridge_synchronizes_shared_frame_without_blocking_gui(self):
        render_start = BRIDGE_SOURCE.index("- (void)renderFrame")
        render_end = BRIDGE_SOURCE.index("- (void)presentView", render_start)
        render = BRIDGE_SOURCE[render_start:render_end]
        present_start = render_end
        present_end = BRIDGE_SOURCE.index("- (void)shutdown", present_start)
        present = BRIDGE_SOURCE[present_start:present_end]

        self.assertIn("glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE,0)", render)
        self.assertIn("glWaitSync(_frameFence,0,GL_TIMEOUT_IGNORED)", present)
        self.assertNotIn("glFinish(", render)

    def test_background_player_has_no_retired_gstreamer_path(self):
        start = MAIN_SOURCE.index("class BackgroundMusicPlayer(QObject)")
        end = MAIN_SOURCE.index("class BackgroundMusicManager", start)
        player = MAIN_SOURCE[start:end]
        self.assertNotIn("Gst.", player)
        self.assertNotIn("gst_bg_pipeline", player)
        self.assertNotIn("gst_crossfade_pipeline", player)

    def test_adaptive_bgm_crossfade_is_cached_and_fail_safe(self):
        player = MAIN_SOURCE[
            MAIN_SOURCE.index("class BackgroundMusicPlayer(QObject)"):
            MAIN_SOURCE.index("class BackgroundMusicManager")
        ]
        self.assertIn("def _adaptive_crossfade_duration_ms", player)
        self.assertIn("transition_analysis_cached(current_file)", player)
        self.assertIn("select_bgm_crossfade_seconds", player)
        self.assertIn('return configured, "safe_fallback"', player)
        self.assertIn("selected_duration_ms", player)
        cache = MAIN_SOURCE[MAIN_SOURCE.index("def transition_analysis_cached"):]
        cache = cache[:cache.index("def _loudness_workers_allowed")]
        self.assertIn("threading.Thread", cache)
        self.assertIn("return None", cache)

    def test_full_loudness_scan_also_populates_transition_metadata(self):
        self.assertIn("session.measure_transition(", MAIN_SOURCE)
        self.assertIn("build_audio_transition_analysis(", MAIN_SOURCE)
        self.assertIn("_transition_analysis_store(\n                            transition_record, persist=False", MAIN_SOURCE)
        self.assertIn("existing_loudness.get(\"mode\") == \"full\"", MAIN_SOURCE)
        media_jobs = pathlib.Path("libmpv_media_jobs.py").read_text(encoding="utf-8")
        self.assertIn("asetnsamples=n=4800:p=1", media_jobs)
        self.assertIn("lavfi.astats.Overall.RMS_level", media_jobs)
        self.assertIn("def measure_transition", media_jobs)

    def test_turbo_scan_uses_four_isolated_workers_and_holds_for_playback(self):
        start = function_source("_start_analyze_library_items")
        self.assertIn('worker_count = min(4, len(items)) if mode == "turbo" else 1', start)
        self.assertIn('parallel=(mode == "turbo")', start)
        self.assertIn('True if mode in {"transition", "turbo"}', start)
        self.assertIn("AnalyzeLibraryParallelCoordinator", start)
        settings = MAIN_SOURCE[MAIN_SOURCE.index('QPushButton("Turbo Full Scan")'):]
        self.assertIn('mode="turbo"', settings)

    def test_turbo_serializes_mp4_visual_decodes_and_wires_cancellation(self):
        worker = MAIN_SOURCE[
            MAIN_SOURCE.index("class AnalyzeLibraryWorker(QObject)"):
            MAIN_SOURCE.index("class AnalyzeLibraryParallelCoordinator(QObject)")
        ]
        self.assertIn("_mp4_visual_analysis_sem.acquire(timeout=0.25)", worker)
        self.assertIn("_mp4_visual_analysis_sem.release()", worker)
        self.assertIn("cancel_check=self.is_cancelled", worker)
        self.assertIn("session.measure_video_tail", worker)
        self.assertNotIn("from libmpv_media_jobs import sample_video_tail_metrics", worker)
        self.assertIn("persist=False, checkpoint=True", worker)
        self.assertIn("MP4 visual analysis started", worker)
        self.assertIn("MP4 visual analysis finished", worker)
        start = function_source("_start_analyze_library_items")
        self.assertIn("worker.stage.connect(_on_stage)", start)
        media_jobs = pathlib.Path("libmpv_media_jobs.py").read_text(encoding="utf-8")
        self.assertIn("raise InterruptedError(\"libmpv offline decode cancelled\")", media_jobs)
        self.assertIn("job.wait_for_end(timeout, cancel_check=cancel_check)", media_jobs)
        self.assertIn('job.option("audio", "no")', media_jobs)

    def test_turbo_helper_timeout_does_not_retry_the_same_file(self):
        worker = MAIN_SOURCE[
            MAIN_SOURCE.index("class AnalyzeLibraryWorker(QObject)"):
            MAIN_SOURCE.index("class AnalyzeLibraryParallelCoordinator(QObject)")
        ]
        self.assertIn("helper_timeout = 45.0 if self.parallel else 120.0", worker)
        self.assertIn('raise AnalysisHelperError(f"Analysis helper failed: {exc}") from exc', worker)

    def test_duplicate_manager_is_review_first_and_recoverable(self):
        self.assertIn('QPushButton("Duplicate Song Manager")', MAIN_SOURCE)
        manager = function_source("_show_duplicate_song_results")
        self.assertIn("Nothing is selected automatically", manager)
        self.assertIn("Different lyric rendering", manager)
        self.assertIn('"Select All Duplicates"', manager)
        self.assertIn("cleanup_eligible = bool(group.get(\"eligible\") and not is_keeper)", manager)
        self.assertIn("child.setFlags(child.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)", manager)
        self.assertGreaterEqual(manager.count("bool(item.data(0, cleanup_role))"), 2)
        self.assertIn("Move Selected to Recovery", manager)
        self.assertIn('APP_USER_DIR / "duplicate-recovery"', manager)
        self.assertIn("removed_paths=sorted(removed)", manager)

    def test_missing_transition_backfill_is_resumable_and_holds_for_playback(self):
        self.assertIn('QPushButton("Analyze Missing Transition Data")', MAIN_SOURCE)
        self.assertIn("def _library_transition_analysis_items", MAIN_SOURCE)
        self.assertIn('mode="transition"', MAIN_SOURCE)
        self.assertIn('True if mode in {"transition", "turbo"}', MAIN_SOURCE)
        enumerate_source = function_source("_library_transition_analysis_items")
        self.assertIn("audio_complete", enumerate_source)
        self.assertIn("visual_complete", enumerate_source)
        self.assertIn("if force or not (audio_complete and visual_complete)", enumerate_source)

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("singws_main_perf", "0.2.18.1.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.singws = module

    def test_preview_overlay_refresh_does_not_force_synchronous_repaint(self):
        source = function_source("_refresh_preview_overlay_binding")
        self.assertNotIn(".repaint(", source)
        self.assertIn(".update()", source)

    def test_python_video_surfaces_do_not_create_native_child_windows(self):
        preview_start = MAIN_SOURCE.index("class PreviewVideoAreaWidget")
        preview_end = MAIN_SOURCE.index("class PreviewWindow", preview_start)
        preview_source = MAIN_SOURCE[preview_start:preview_end]
        ticker_start = MAIN_SOURCE.index("class Ticker(QFrame)")
        ticker_end = MAIN_SOURCE.index("class DetachedPainterTicker", ticker_start)
        ticker_source = MAIN_SOURCE[ticker_start:ticker_end]
        self.assertNotIn("WA_NativeWindow", preview_source)
        self.assertNotIn("WA_DontCreateNativeAncestors", preview_source)
        self.assertNotIn("WA_NativeWindow", ticker_source)
        self.assertNotIn("WA_DontCreateNativeAncestors", ticker_source)

    def test_removed_gstreamer_detach_does_not_create_parking_window(self):
        detach = function_source("_detach_video_sinks_now")
        parking = function_source("_get_sink_parking_winid")
        recover = function_source("_recover_idle_output")
        self.assertNotIn("set_window_handle", detach)
        self.assertNotIn("QWidget", parking)
        self.assertNotIn("winId", parking)
        self.assertNotIn("_get_sink_parking_winid", recover)

    def test_bgm_pulse_styles_are_cached(self):
        source = function_source("_apply_bg_pulse_style")
        self.assertIn("_bg_player_frame_css_cache", source)
        self.assertIn("_bg_now_playing_kicker_css_cache", source)
        self.assertIn("round(self._bg_pulse_value(), 1)", source)

    def test_debounced_save_timer_uses_playback_safe_wrapper(self):
        self.assertIn("_save_data_timer.timeout.connect(self._save_data_scheduled)", MAIN_SOURCE)
        scheduled = function_source("_save_data_scheduled")
        # The worker is used unconditionally now, not only while karaoke is
        # playing: the snapshot still happens on the UI thread so durability is
        # unchanged, and an idle host stutters just as visibly as a playing one.
        self.assertIn("_start_save_data_worker", scheduled)
        self.assertNotIn("karaoke_playing", scheduled)
        worker = function_source("_start_save_data_worker")
        self.assertIn("threading.Thread", worker)
        self.assertIn("singws-save-data", worker)

    def test_periodic_autosave_does_not_block_the_gui_thread(self):
        # A direct timeout->save_data connection was a synchronous encode and
        # write on the GUI thread once a minute for the whole show.
        self.assertNotIn("auto_save_timer.timeout.connect(self.save_data)", MAIN_SOURCE)
        self.assertIn("auto_save_timer.timeout.connect(lambda: self._schedule_save_data(0))", MAIN_SOURCE)

    def test_shutdown_flushes_the_queue_synchronously(self):
        # Saves are debounced and run on a worker, so quitting must force one
        # last synchronous write or the final edits are lost.
        # function_source() finds the first closeEvent in the file (the
        # rotation window's), so match the main window's flush directly.
        self.assertIn("[SHUTDOWN] final queue save failed", MAIN_SOURCE)

    def test_queue_rebuild_suspends_repaints(self):
        source = function_source("update_queue_display")
        self.assertIn("queue_display.setUpdatesEnabled(False)", source)
        self.assertIn("queue_display.setUpdatesEnabled(True)", source)

    def test_cached_loudness_failures_do_not_flood_gui_progress(self):
        items = [
            (f"/tmp/{i}.zip", f"/tmp/{i}.zip", f"Track {i}", f"/tmp/{i}.zip")
            for i in range(5000)
        ]
        worker = self.singws.AnalyzeLibraryWorker(items)
        progress = []
        worker.progress.connect(lambda done, total, name: progress.append(done))
        fake_jobs = types.ModuleType("libmpv_media_jobs")
        fake_jobs.AnalysisHelperError = RuntimeError
        fake_jobs.AnalysisTrackError = ValueError
        fake_jobs.IsolatedLoudnessSession = lambda: types.SimpleNamespace(close=lambda: None)

        with mock.patch.dict(sys.modules, {"libmpv_media_jobs": fake_jobs}), \
             mock.patch.object(self.singws, "_loudness_workers_allowed", return_value=True), \
             mock.patch.object(self.singws, "loudness_failed_cached", return_value=True), \
             mock.patch.object(self.singws.time, "monotonic", return_value=1.0):
            worker.run()

        self.assertEqual(progress, [1, len(items)])

    def test_loudness_item_enumeration_runs_before_progress_ui_on_worker(self):
        analyze = function_source("analyze_library")
        start = function_source("_start_analyze_library_items")
        worker_start = MAIN_SOURCE.index("class AnalyzeLibraryPreparationWorker")
        worker_end = MAIN_SOURCE.index("class WaveformDecodeWorker", worker_start)
        worker = MAIN_SOURCE[worker_start:worker_end]
        self.assertIn("AnalyzeLibraryPreparationWorker", analyze)
        self.assertIn("prep_thread.start()", analyze)
        self.assertNotIn("_library_loudness_analysis_items", analyze)
        self.assertIn("_library_loudness_analysis_items", worker)
        self.assertIn("QProgressDialog", start)

    def test_queue_add_defers_metadata_lookup_during_playback(self):
        source = function_source("_add_song_to_queue")
        self.assertIn("karaoke_playing", source)
        self.assertIn("[QUEUE-ADD] metadata lookup deferred during playback", source)
        self.assertLess(source.index("karaoke_playing"), source.index("self.lookup_display_name(fallback_path)"))

    def test_next_up_prescan_has_disabled_worker_gate(self):
        source = function_source("_schedule_next_up_prescan")
        self.assertIn("_lead_silence_prescan_enabled", source)
        prescan = function_source("_prescan_next_track")
        self.assertIn("_lead_silence_prescan_enabled", prescan)
        self.assertIn("_next_prescan_inflight", prescan)
        gate = function_source("_lead_silence_prescan_enabled")
        self.assertIn("end_silence_trim_enabled", gate)
        self.assertNotIn("_performance_mode", gate)
        self.assertNotIn("_safe_mode", gate)

    def test_playback_worker_diagnostics_are_logged(self):
        refresh = function_source("_request_queue_display_refresh")
        self.assertIn("_log_active_background_workers", refresh)
        diag = function_source("_log_active_background_workers")
        self.assertIn("[PLAYBACK-WORKERS]", diag)
        self.assertIn("loudness_inflight", diag)
        self.assertIn("lead_silence_prescan", diag)
        self.assertIn("now - last < 60.0", diag)
        self.assertIn("_active_worker_log_last_signature", diag)

    def test_memory_telemetry_is_periodic_and_growth_sensitive(self):
        start = function_source("_start_memory_telemetry")
        self.assertIn("timer.start(5 * 60 * 1000)", start)
        self.assertIn("timer.timeout.connect(self._log_memory_telemetry)", start)
        telemetry = function_source("_log_memory_telemetry")
        self.assertIn("psutil.Process(os.getpid()).memory_info().rss", telemetry)
        self.assertIn("growth_mb >= 128.0", telemetry)
        self.assertIn("30 * 60", telemetry)
        self.assertIn("[MEMORY]", telemetry)
        self.assertIn("queue_songs=", telemetry)

    def test_performance_is_recorded_on_completion_not_on_start(self):
        # Marking at song start meant a song changed or removed mid-play still
        # counted as sung, so the server answered re-adds with "You already sang
        # this song tonight" -- and it freed the singer's slot before their song
        # had finished.
        # Start path stashes instead of completing.
        play = function_source("play_next_file")
        self.assertIn("self._pending_performance = {", play)
        self.assertNotIn("_complete_remote_request", play)
        # Real media end commits it, before stop_playback tears state down.
        finish = function_source("_finish_media_end_cleanup")
        self.assertIn('_commit_pending_performance(reason="song_completed")', finish)
        # Any stop that is not a media end must discard it. Target the karaoke
        # stop_playback specifically -- the BGM player defines one too.
        start = MAIN_SOURCE.index("def stop_playback(self, skip_confirmation=False):")
        stop = MAIN_SOURCE[start:MAIN_SOURCE.index("\n    def ", start + 10)]
        self.assertIn('_discard_pending_performance(reason="stop_playback")', stop)
        commit = function_source("_commit_pending_performance")
        self.assertIn("_complete_remote_request", commit)
        self.assertIn("_record_singer_history_play", commit)
        # Pop, so a performance can never be recorded twice.
        self.assertIn('state.pop("_pending_performance", None)', commit)
        discard = function_source("_discard_pending_performance")
        self.assertIn('state.pop("_pending_performance", None)', discard)

    def test_singer_history_export_memoises_unchanged_singers(self):
        # _export_singer_history_payload re-normalised every singer on a daemon
        # thread: 46-79ms of CPU-bound Python, 54 times during the 07-25 show.
        # It holds the GIL, so the Qt event loop cannot run and the UI stalls.
        norm = function_source("_normalize_singer_history_store")
        self.assertIn("cache_scope", norm)
        # Fingerprint the raw record, do not trust updated_at: a mutation that
        # forgets to bump it must still invalidate, or sync silently drops data.
        self.assertIn("json.dumps(", norm)
        self.assertIn("hit[0] == fingerprint", norm)
        # Bare-instance safe: getattr on an uninitialised QWidget raises.
        self.assertIn('object.__getattribute__(self, "__dict__")', norm)
        # Cache must not outgrow the store it mirrors.
        self.assertIn("cache.pop(stale_key, None)", norm)
        export = function_source("_export_singer_history_payload")
        self.assertIn('cache_scope="export"', export)

    def test_end_silence_confirmation_shortens_only_at_track_end(self):
        # The 2.0s floor in _end_trim_threshold_sec is heard as a gap between the
        # karaoke tail and background music: the 07-25 show triggered at
        # silent=2.03s every time. Shorten it only when the track is provably
        # over, never mid-song.
        trim = function_source("_maybe_trim_end_silence")
        self.assertIn("_end_silence_confident_remain_s", trim)
        self.assertIn("_end_silence_confident_min_s", trim)
        # Both conditions are required: little left to play AND lyrics finished
        # (or MP4). Either alone would risk cutting a quiet ending short.
        self.assertIn('and elapsed >= float(visual_end)', trim)
        # Must only ever reduce the wait, never extend it.
        self.assertIn("threshold_s = min(", trim)
        # The CDG "graphics still changing" safeguard must remain downstream.
        self.assertIn("CDG/ZIP safeguard", trim)

    def test_end_silence_threshold_floor_allows_short_confirmation(self):
        # The floor was 2.0 in three places (getter, spin range, save clamp), so
        # the host could not tune below the value that causes the audible gap.
        getter = function_source("_end_trim_threshold_sec")
        self.assertIn("max(0.5, min(60.0, val))", getter)
        self.assertIn("end_threshold_spin.setRange(0.5, 60.0)", MAIN_SOURCE)
        self.assertIn(
            'self.settings["end_silence_trim_threshold_sec"] = max(0.5, min(60.0, float(value)))',
            MAIN_SOURCE,
        )
        # Default must stay at 2.5: widening the range must not change behaviour
        # for anyone who does not deliberately lower it.
        self.assertIn('"end_silence_trim_threshold_sec": 2.5', MAIN_SOURCE)

    def test_memory_telemetry_reports_no_legacy_frame_readers(self):
        telemetry = function_source("_log_memory_telemetry")
        self.assertIn("readers=0 running=0 frames=0 frames_mb=0.0", telemetry)
        self.assertNotIn("reader_pool_stats", telemetry)

    def test_high_frequency_diagnostics_are_rate_limited(self):
        helper_start = MAIN_SOURCE.index("def _diag_rate_limited")
        helper_end = MAIN_SOURCE.index("\ndef ", helper_start + 4)
        helper = MAIN_SOURCE[helper_start:helper_end]
        self.assertIn("_DIAG_RATE_LIMIT_LAST", helper)
        self.assertIn("now - last < max(0.0, float(interval_sec))", helper)
        relay = function_source("_relay_fetch_finished")
        self.assertIn("_diag_rate_limited(", relay)
        recovery = function_source("_network_recovery_tick")
        self.assertIn("_diag_rate_limited(", recovery)

    def test_queue_state_diagnostics_are_rate_limited(self):
        # _update_last_sung_card runs on every queue-display refresh, so an
        # unthrottled [QUEUE-STATE] line floods the log during a show.
        card = function_source("_update_last_sung_card")
        self.assertIn("[QUEUE-STATE]", card)
        self.assertIn("_diag_rate_limited(", card)
        self.assertNotIn('_diag(f"[QUEUE-STATE]', card)

    def test_logging_setup_does_not_duplicate_handlers(self):
        source = MAIN_SOURCE[MAIN_SOURCE.index("def setup_logging"):MAIN_SOURCE.index("# Initialize logging")]
        self.assertIn("_singws_file_handler", source)
        self.assertIn("_singws_console_handler", source)
        self.assertIn("any(bool(getattr(h, \"_singws_file_handler\", False))", source)
        self.assertIn("any(bool(getattr(h, \"_singws_console_handler\", False))", source)
        self.assertLess(source.index("_singws_file_handler"), source.index("logger.addHandler(file_handler)"))
        self.assertLess(source.index("_singws_console_handler"), source.index("logger.addHandler(console_handler)"))

    def test_packaged_app_has_opt_in_smoke_exit(self):
        main_block = MAIN_SOURCE[MAIN_SOURCE.index('if __name__ == "__main__":'):]
        self.assertIn("SINGWS_SMOKE_EXIT_MS", main_block)
        self.assertIn("QTimer.singleShot(smoke_exit_ms, window.close)", main_block)
        self.assertIn("[SMOKE] scheduled app exit", main_block)

    def test_host_control_state_sync_timer_starts_on_ui_thread(self):
        source = function_source("_schedule_host_control_state_sync")
        self.assertIn("QThread.currentThread() != app.thread()", source)
        self.assertIn("self._run_on_ui_thread(self._schedule_host_control_state_sync)", source)
        self.assertLess(source.index("QThread.currentThread()"), source.index("timer.start("))

    def test_ticker_debounce_timer_starts_on_ui_thread(self):
        source = function_source("schedule_ticker_update")
        self.assertIn("QThread.currentThread() != app.thread()", source)
        self.assertIn("self._run_on_ui_thread(self.schedule_ticker_update)", source)
        self.assertLess(source.index("QThread.currentThread()"), source.index("self._ticker_update_timer.start(150)"))
        queue_refresh = function_source("update_queue_display")
        self.assertIn("_owner_thread = self.thread()", queue_refresh)
        self.assertIn("_QThread.currentThread() != _owner_thread", queue_refresh)
        self.assertIn("self._finish_queue_display_refresh_side_effects()", queue_refresh)
        side_effects = function_source("_finish_queue_display_refresh_side_effects")
        self.assertIn("owner_thread = self.thread()", side_effects)
        self.assertIn("self._run_on_ui_thread(self._finish_queue_display_refresh_side_effects)", side_effects)
        self.assertIn("QThread.currentThread() == timer.thread()", side_effects)
        self.assertIn("timer.start(10000)", side_effects)
        rotation_view = function_source("update_rotation_view")
        self.assertIn("owner_thread = self.rotation_view.thread()", rotation_view)
        self.assertIn("self._run_on_ui_thread(self.update_rotation_view)", rotation_view)

    def test_websocket_relay_lifecycle_stays_on_ui_thread(self):
        transport_setting = function_source("_request_transport_setting")
        self.assertIn('os.environ.get("SINGWS_REQUEST_TRANSPORT"', transport_setting)
        self.assertIn('if value == "polling":', transport_setting)
        for name in (
            "_start_request_relay",
            "_stop_request_relay",
            "_start_host_control_relay",
            "_stop_host_control_relay",
        ):
            source = function_source(name)
            self.assertIn("QThread.currentThread() != app.thread()", source)
            self.assertIn("_run_on_ui_thread", source)
        close_start = MAIN_SOURCE.index('    def closeEvent(self, event):\n        """Close main window -> quit whole app.')
        close_end = MAIN_SOURCE.index("\n    def load_data", close_start)
        close = MAIN_SOURCE[close_start:close_end]
        self.assertIn("self._shutdown_network_transports()", close)
        shutdown = function_source("_shutdown_network_transports")
        self.assertIn("_network_transports_shutdown", shutdown)
        self.assertIn("self._stop_request_relay()", shutdown)
        self.assertIn("self._stop_host_control_relay()", shutdown)
        self.assertIn("timer.stop()", shutdown)
        self.assertIn("_QT_APP_SHUTTING_DOWN = True", shutdown)
        self.assertIn("sock.blockSignals(True)", MAIN_SOURCE)
        self.assertIn("sock.close()", MAIN_SOURCE)
        self.assertIn("sock.abort()", MAIN_SOURCE)
        self.assertIn("sock.setParent(None)", MAIN_SOURCE)
        self.assertIn("_retired_sockets", MAIN_SOURCE)
        self.assertIn("QApplication.processEvents()", shutdown)
        self.assertIn("QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)", shutdown)
        request_stop = function_source("_stop_request_relay")
        host_stop = function_source("_stop_host_control_relay")
        self.assertIn("app_closing = bool(getattr(self, \"_app_closing\", False))", request_stop)
        self.assertIn("worker.stop(delete_later=not app_closing)", request_stop)
        self.assertIn("worker.setParent(None)", request_stop)
        self.assertIn("_shutdown_relay_workers", request_stop)
        self.assertIn("app_closing = bool(getattr(self, \"_app_closing\", False))", host_stop)
        self.assertIn("worker.stop(delete_later=not app_closing)", host_stop)
        self.assertIn("worker.setParent(None)", host_stop)
        self.assertIn("_shutdown_relay_workers", host_stop)
        about_to_quit = function_source("_on_app_about_to_quit")
        self.assertIn("self._app_closing = True", about_to_quit)
        self.assertIn("_QT_APP_SHUTTING_DOWN = True", about_to_quit)
        self.assertIn("self._shutdown_network_transports()", about_to_quit)
        main_block = MAIN_SOURCE[MAIN_SOURCE.index('if __name__ == "__main__":'):]
        self.assertIn("app.aboutToQuit.connect(window._on_app_about_to_quit)", main_block)
        run_on_ui = function_source("_run_on_ui_thread")
        self.assertIn('if bool(getattr(self, "_app_closing", False)):', run_on_ui)
        self.assertIn("owner_thread = self.thread()", run_on_ui)

    def test_qt_websocket_shutdown_warnings_are_filtered_only_during_shutdown(self):
        self.assertIn("qInstallMessageHandler", MAIN_SOURCE)
        start = MAIN_SOURCE.index("def _singws_qt_message_handler")
        end = MAIN_SOURCE.index("\n\ntry:\n    _QT_PREVIOUS_MESSAGE_HANDLER", start)
        handler = MAIN_SOURCE[start:end]
        self.assertIn("_QT_APP_SHUTTING_DOWN", handler)
        self.assertIn("_QT_SOCKET_TEARDOWN_WARNING_PARTS", handler)
        self.assertIn("previous(mode, context, message)", handler)
        self.assertIn("sys.__stderr__.write", handler)
        self.assertIn("QNativeSocketEngine", MAIN_SOURCE)
        self.assertIn("QSslSocket", MAIN_SOURCE)
        self.assertIn("QWebSocketDataProcessor", MAIN_SOURCE)

    def test_waitlist_needs_review_and_daw_preview_live_guards(self):
        wait_refresh = function_source("_refresh_waiting_for_add_view")
        self.assertIn('"needs_review": QColor("#86efac")', wait_refresh)
        self.assertIn('if status_kind == "needs_review":', wait_refresh)
        self.assertIn("self._waiting_for_add_pulse_value()", wait_refresh)
        pulse_tick = function_source("_tick_waiting_for_add_pulse")
        self.assertIn("self._needs_review_count() > 0", pulse_tick)
        self.assertIn("needs_review_pulse", pulse_tick)

        daw_stopped = function_source("_mark_daw_preview_playback_stopped")
        self.assertIn("playback_stopped_immediate", daw_stopped)
        self.assertIn("playback_stopped_retry", daw_stopped)
        self.assertNotIn("_post_daw_singer_screen_snapshot(None, active=False", daw_stopped)

        direct_message = function_source("_net_send_direct_message")
        self.assertIn("system_notice: bool = False", direct_message)
        self.assertIn('"system_notice": "1" if system_notice else "0"', direct_message)
        waiting_refresh = function_source("_schedule_waiting_for_add_view_refresh")
        self.assertIn("owner_thread = self.thread()", waiting_refresh)
        self.assertIn("self._run_on_ui_thread(lambda: self._schedule_waiting_for_add_view_refresh", waiting_refresh)
        history_refresh = function_source("_schedule_singer_history_refresh")
        self.assertIn("owner_thread = self.thread()", history_refresh)
        self.assertIn("self._run_on_ui_thread(lambda: self._schedule_singer_history_refresh", history_refresh)
        history_build = function_source("_refresh_singer_history_view")
        self.assertIn("render_limit = 220 if playing else 700", history_build)
        history_apply = function_source("_on_singer_history_directory_built")
        self.assertNotIn("worker_finished_during_playback", history_apply)
        history_details = function_source("_update_singer_history_details")
        self.assertIn("editing_allowed", history_details)
        history_songs_apply = function_source("_on_singer_history_songs_built")
        self.assertNotIn("songs_worker_finished_during_playback", history_songs_apply)
        self.assertIn('if bool(getattr(self, "_app_closing", False)):', function_source("_dispatch_ui_call"))



    def test_native_cdg_path_has_no_python_packet_preload(self):
        preload = function_source("_preload_next_up_cdg")
        self.assertNotIn("threading.Thread", preload)
        self.assertNotIn("preload_cdg_packets", MAIN_SOURCE)
        legacy_add = function_source("add_song_to_singer")
        self.assertIn("_enqueue_cdg_pair_async", legacy_add)
        self.assertNotIn("os.path.exists(mp3_path)", legacy_add)
        pair_check = function_source("_enqueue_cdg_pair_async")
        self.assertIn("threading.Thread", pair_check)
        self.assertNotIn("preload_cdg_packets", pair_check)

    def test_library_path_lookup_uses_an_index(self):
        lookup = function_source("_get_track_obj")
        self.assertIn("_find_track_by_path_ci", lookup)
        indexed = function_source("_find_track_by_path_ci")
        self.assertIn("_track_path_lookup", indexed)
        self.assertNotIn("for t in self.tracks", indexed)

    def test_uncached_loudness_lookup_avoids_file_stat(self):
        for name in ("loudness_info_cached", "loudness_gain_db_cached"):
            start = MAIN_SOURCE.index(f"def {name}")
            end = MAIN_SOURCE.index("\ndef ", start + 4)
            source = MAIN_SOURCE[start:end]
            self.assertLess(source.index("_loudness_cache.get"), source.index("_loudness_file_sig"))

    def test_noop_server_sync_avoids_history_rebuild_and_repeat_request_diagnostics(self):
        history_merge = function_source("_merge_remote_singer_history")
        self.assertIn("if merged_song != local_song:", history_merge)
        self.assertIn("if changed:", history_merge)
        self.assertIn('self._schedule_singer_history_refresh(reason="remote_merge")', history_merge)
        request_diag = function_source("_log_remote_request_diag")
        self.assertIn("_remote_request_diag_signatures", request_diag)
        self.assertIn("cache.get(request_id) == diag_signature", request_diag)
        self.assertIn("len(cache) > 2048", request_diag)



    def test_deferred_remote_adds_save_off_thread_during_playback(self):
        source = function_source("_save_deferred_remote_adds")
        self.assertIn("karaoke_playing", source)
        self.assertIn("_start_deferred_remote_save_worker", source)
        worker = function_source("_start_deferred_remote_save_worker")
        self.assertIn("threading.Thread", worker)
        self.assertIn("singws-save-deferred-remote", worker)

    def test_scan_folder_starts_background_worker_before_inline_fallback(self):
        marker = '    def _legacy_scan_folder(self):\n        """Scan chooser: Quick Update (incremental) or Full Scan."""'
        self.assertIn(marker, MAIN_SOURCE)
        source = MAIN_SOURCE[MAIN_SOURCE.index(marker):]
        source = source[:source.index("\n    def _fmt_mmss")]
        self.assertIn("_start_library_scan_worker", source)
        self.assertLess(source.index("_start_library_scan_worker"), source.index("Instant filename scan"))

    def test_live_state_polish_has_intel_playback_backoff(self):
        source = function_source("_live_state_interval_ms")
        self.assertIn("x86_64", source)
        self.assertIn("return 350", source)
        install = function_source("_install_live_state_polish")
        self.assertIn("self._live_state_interval_ms()", install)

    def test_rotation_level_meter_runs_only_during_active_playback(self):
        app = QApplication.instance() or QApplication([])
        meter = self.singws.BarLevelMeter()
        self.assertFalse(meter._timer.isActive())

        meter.set_active(True)
        self.assertTrue(meter._timer.isActive())

        meter.set_active(False)
        self.assertFalse(meter._timer.isActive())
        self.assertEqual(meter._heights, [0.0] * meter._n_bars)
        meter.deleteLater()
        app.processEvents()

    def test_rotation_meter_animates_without_levels_and_settles_when_paused(self):
        app = QApplication.instance() or QApplication([])
        playing = [True]
        meter = self.singws.BarLevelMeter(
            level_provider=lambda: (None, None),
            playback_provider=lambda: playing[0],
        )
        meter.set_active(True)
        for _ in range(20):
            meter._tick()
        moving = list(meter._heights)
        self.assertGreater(max(moving), 0.1)
        meter._tick()
        self.assertNotEqual(moving, meter._heights)
        playing[0] = False
        for _ in range(20):
            meter._tick()
        self.assertLess(max(meter._heights), 0.04)
        meter.set_active(False)
        self.assertFalse(meter._timer.isActive())
        meter.deleteLater()
        app.processEvents()

    def test_rotation_meter_preserves_measured_silence_and_stale_sample_handling(self):
        app = QApplication.instance() or QApplication([])
        sample = [-60.0, self.singws.time.monotonic()]
        meter = self.singws.BarLevelMeter(
            level_provider=lambda: tuple(sample), playback_provider=lambda: True,
        )
        self.assertEqual(meter._sample_level(), 0.0)
        sample[0] = -12.0
        self.assertGreater(meter._sample_level(), 0.5)
        sample[1] -= 2.0
        self.assertEqual(meter._sample_level(), 0.0)
        meter.deleteLater()
        app.processEvents()

    def test_waitlist_uses_model_backed_view(self):
        self.assertIn("class WaitlistRequestListModel(QAbstractListModel)", MAIN_SOURCE)
        page = function_source("_build_waiting_for_add_page")
        self.assertIn("self.waiting_for_add_model = WaitlistRequestListModel(self)", page)
        self.assertIn("self.waiting_for_add_list = QListView()", page)
        self.assertIn("self.waiting_for_add_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)", page)
        self.assertIn("self.waiting_for_add_list.setTextElideMode(Qt.TextElideMode.ElideNone)", page)
        self.assertIn("self.waiting_for_add_list.setItemDelegate(WrapHeightItemDelegate", page)
        refresh = function_source("_refresh_waiting_for_add_view")
        self.assertIn("waiting_model.setRows(model_rows)", refresh)
        self.assertNotIn("QListWidgetItem(self._waiting_for_add_row_text", refresh)
        sections = function_source("_waiting_for_add_sections")
        self.assertNotIn("_waiting_for_add_active_rotation_rows", MAIN_SOURCE)
        self.assertNotIn('"active_rotation"', sections)
        self.assertNotIn('"active_rotation"', refresh)

    def test_waitlist_nav_uses_pending_acceptance_pulse(self):
        self.assertIn('_build_nav_item("◈", "Waitlist")', MAIN_SOURCE)
        self.assertIn("_waiting_for_add_pulse_timer", MAIN_SOURCE)
        self.assertNotIn("_waiting_for_add_blink_timer", MAIN_SOURCE)
        update = function_source("_update_waiting_for_add_nav_state")
        self.assertIn("_pending_acceptance_count()", update)
        self.assertIn('f"Waitlist ({count})"', update)
        self.assertIn("Waitlist: no pending host action", update)
        self.assertIn("need host action", update)
        self.assertIn("rgba(22, 163, 74", update)
        self.assertIn("_waiting_for_add_nav_css_cache_key", update)

    def test_header_status_shows_request_and_waitlist_state(self):
        init_source = MAIN_SOURCE[MAIN_SOURCE.index("accepting_text = \"Requests Open\""):]
        init_source = init_source[:init_source.index("self.header_location_label")]
        self.assertIn("Requests Open", init_source)
        self.assertIn("Waitlist On", init_source)
        refresh = function_source("_refresh_header_status")
        self.assertIn("Requests Open", refresh)
        self.assertIn("Waitlist On", refresh)
        self.assertIn("Waitlist mode:", refresh)
        self.assertIn('self.settings.get("location_name")', refresh)
        self.assertIn('or self.settings.get("venue_name")', refresh)
        self.assertIn('or self.settings.get("tenant")\n                    or self.settings.get("user")', refresh)

    def test_every_show_window_restore_guards_against_a_detached_screen(self):
        # Restoring saved geometry onto a monitor that is gone leaves a window
        # macOS never assigns a valid backing store, and Qt then segfaults in
        # QBackingStore::flush (2026-08-09 23:15:55, EXC_BAD_ACCESS at 0x0 in
        # QPaintDevice::devicePixelRatio). 0.4.3.9 guarded only the startup
        # path; the re-show and bottom-right paths restored raw.
        lines = MAIN_SOURCE.splitlines()
        unguarded = []
        for i, line in enumerate(lines):
            if "karaoke_window_pos" not in line:
                continue
            window = "\n".join(lines[i:i + 18])
            if "setGeometry" in window and "_rect_on_attached_screen" not in window:
                unguarded.append(i + 1)
        self.assertEqual(
            unguarded, [],
            f"show-window geometry restored without _rect_on_attached_screen at lines {unguarded}",
        )

    def test_show_vfx_is_attached_before_the_ticker(self):
        """Native child surfaces stack by creation order, not widget hierarchy.

        The show VFX layer and the Qt Quick ticker are both native surfaces.
        Deferring the overlay to a timer (0.4.4.0) let the ticker be created
        first, so the overlay stacked on top and the ticker was invisible for a
        whole show while still updating its text. Build the overlay inline, and
        before the ticker.
        """
        video = MAIN_SOURCE[
            MAIN_SOURCE.index("class VideoWindow(QWidget):"):
            MAIN_SOURCE.index("def _attach_show_vfx")
        ]
        self.assertIn("self._attach_show_vfx(self.video_area)", video)
        self.assertNotIn("QTimer.singleShot", video.split("self._attach_show_vfx")[0][-400:])
        self.assertLess(
            video.index("self._attach_show_vfx(self.video_area)"),
            video.index("self.ticker"),
            "the VFX overlay must be created before the ticker",
        )

    def test_daw_preview_can_capture_from_the_mpv_engine(self):
        """The DAW singer-screen preview was blank for the whole mpv path.

        Its only frame source was the retired Python painter transport (the
        engine). Under mpv the picture is a shared GL texture in a native
        NSView, so there is no karaoke_frame and QWidget.grab() -- the last
        fallback -- cannot capture a native child surface.
        """
        capture = function_source("_capture_daw_singer_screen_snapshot")
        self.assertIn('getattr(self, "_mpv_playback", None)', capture)
        self.assertIn('getattr(plugin, "grabFrame", None)', capture)
        # The engine grab must be tried BEFORE the widget-grab fallback that
        # cannot see native surfaces.
        self.assertLess(
            capture.index('getattr(plugin, "grabFrame", None)'),
            capture.index("va.grab().toImage()"),
        )
        # And the engine must actually expose it. CDG needs a read from the
        # retained texture because libmpv screenshot-raw is blank for a
        # caller-owned CDG FBO; normal full-resolution video stays on the
        # non-invasive screenshot path.
        import pathlib
        iina = pathlib.Path("mpv_playback_iina.py").read_text(encoding="utf-8")
        self.assertIn("def grabFrame(self):", iina)
        self.assertIn("singws_bridge_grab_frame", iina)
        self.assertIn("singws_bridge_free_frame", iina)
        bridge = pathlib.Path("native/mpv_bridge/bridge.mm").read_text(encoding="utf-8")
        self.assertIn("screenshot-raw", bridge)
        self.assertIn("if(_isCdg&&_hasFrame)", bridge)
        self.assertIn("glReadPixels(0,0,w,h,GL_BGRA,GL_UNSIGNED_BYTE,out)", bridge)
        self.assertIn("singws_bridge_grab_frame", bridge)
        # Freed exactly once, by the caller that owns it.
        self.assertIn("singws_bridge_free_frame(buf)", iina)

    def test_show_window_restore_refreshes_retained_mpv_views(self):
        """A hidden native mpv child must repaint when Show Karaoke returns."""
        start = MAIN_SOURCE.index("class VideoWindow(QWidget):")
        video = MAIN_SOURCE[start:MAIN_SOURCE.index("class AddToQueueDialog", start)]
        self.assertIn('refresh("show_event")', video)
        refresh = function_source("_refresh_mpv_video_views")
        self.assertIn('getattr(plugin, "refreshVideoViews", None)', refresh)
        self.assertIn("QTimer.singleShot(0, apply)", refresh)
        self.assertIn("QTimer.singleShot(80, apply)", refresh)
        self.assertIn("QTimer.singleShot(250, apply)", refresh)
        bridge = pathlib.Path("native/mpv_bridge/bridge.mm").read_text(encoding="utf-8")
        self.assertIn("- (void)refreshViewsOutput:", bridge)
        self.assertIn("output view reparented to current host", bridge)
        self.assertIn("preview view reparented to current host", bridge)
        self.assertIn("[ctx clearDrawable]", bridge)
        self.assertIn("[ctx setView:view]", bridge)
        self.assertIn("- (void)viewDidMoveToWindow", bridge)
        self.assertIn("nativeViewDidAttach:view", bridge)
        self.assertIn("drawable reattached to window", bridge)
        self.assertIn("[self presentView:_outputView]", bridge)
        self.assertIn("singws_bridge_refresh_views", bridge)

    def test_host_preview_show_and_resize_refresh_retained_mpv_views(self):
        source = MAIN_SOURCE
        start = source.index("class PreviewWindow(QWidget):")
        preview = source[start:source.index("class PerformanceWaveformWidget", start)]
        self.assertIn("self._mpv_surface_refresh_timer.setSingleShot(True)", preview)
        self.assertIn("self._mpv_surface_refresh_timer.start(180)", preview)
        self.assertIn('refresh("host_preview_show_or_resize")', preview)
        self.assertIn("def showEvent(self, event):", preview)
        self.assertIn("def resizeEvent(self, event):", preview)

    def test_screen_topology_change_refreshes_native_video_views(self):
        """Connecting or removing AirPlay must recover the host preview."""
        watcher = function_source("_ensure_screen_change_watcher")
        self.assertIn("surface_refresh_timer.setSingleShot(True)", watcher)
        self.assertIn("surface_refresh_timer.setInterval(600)", watcher)
        self.assertIn(
            'self._refresh_mpv_video_views("screen_topology_change")', watcher
        )
        self.assertIn(
            'app.screenAdded.connect(lambda s: _log("screen added", s, True))',
            watcher,
        )
        self.assertIn(
            'app.screenRemoved.connect(lambda s: _log("screen removed", s, True))',
            watcher,
        )
        self.assertNotIn("karaoke_transport.stop", watcher)
        self.assertNotIn("karaoke_transport.play", watcher)

    def test_cdg_fill_covers_real_host_without_stretching_lyrics(self):
        """The decorative layer covers any display; CDG remains aspect-fit."""
        bridge = pathlib.Path("native/mpv_bridge/bridge.mm").read_text(encoding="utf-8")
        present_start = bridge.index("- (void)presentView:(BridgeVideoView *)view {")
        present = bridge[present_start:bridge.index("- (void)shutdown", present_start)]
        self.assertIn("if(va>ca)", present)
        self.assertIn("glUniform2f(_scaleUniform,1,1)", present)
        self.assertIn("glUniform1i(_sidefillUniform,sidefill)", present)
        self.assertIn("uvx=(GLfloat)span; uox=panelL", present)
        self.assertIn("if(va>ca)sx=(GLfloat)(ca/va)", present)
        self.assertIn("glBlendFunc(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA)", present)
        self.assertIn("glUniform1i(_sidefillUniform,3)", present)
        self.assertIn("6.0/300.0", bridge)
        layer = present[present.index("if(sidefill){"):]
        self.assertLess(
            layer.index("glUniform1i(_sidefillUniform,sidefill)"),
            layer.index("if(va>ca)sx=(GLfloat)(ca/va)"),
        )

    def test_stall_stack_capture_is_opt_in(self):
        # Walking the running main thread's frames from the watchdog thread is
        # a use-after-free; it segfaulted the app on 2026-08-09 23:15:41
        # (EXC_BAD_ACCESS at 0x0 in frame_back_get, thread
        # "singws-main-thread-watchdog"). Timing is safe and stays on.
        self.assertIn('"stall_stack_capture": False', MAIN_SOURCE)
        start = MAIN_SOURCE.index("def _install_main_thread_watchdog")
        end = MAIN_SOURCE.index("# Settings defaults", start)
        watchdog = MAIN_SOURCE[start:end]
        self.assertIn('get("stall_stack_capture", False)', watchdog)
        self.assertIn("if _stack_capture_enabled():", watchdog)
        # The dangerous call must sit behind the gate, never before it.
        # Match the call itself, not the explanation in the comment above it.
        self.assertLess(
            watchdog.index("if _stack_capture_enabled():"),
            watchdog.index("frame = sys._current_frames().get(main_ident)"),
        )
        # Stall detection and recovery timing must survive with capture off.
        self.assertIn("GUI thread recovered after", watchdog)

    def test_stall_event_attribution_is_separately_opt_in(self):
        # An application-wide Python event filter makes every Paint/Timer in the
        # app cross the C++/Python boundary. Enabling it alongside ordinary
        # runtime diagnostics turned the freeze detector into the freeze.
        self.assertIn('"stall_event_attribution": False', MAIN_SOURCE)
        start = MAIN_SOURCE.index("def _install_main_thread_watchdog")
        end = MAIN_SOURCE.index("# Settings defaults", start)
        watchdog = MAIN_SOURCE[start:end]
        self.assertIn('get("stall_event_attribution", False)', watchdog)
        self.assertIn("raise _StallAttributionDisabled", watchdog)
        self.assertIn("except _StallAttributionDisabled:", watchdog)
        # The gate must come before the filter is installed on the application.
        self.assertLess(
            watchdog.index('get("stall_event_attribution", False)'),
            watchdog.index("installEventFilter"),
        )
        # Stall stacks themselves must survive with attribution off.
        self.assertIn("sys._current_frames()", watchdog)

    def test_legacy_stall_probes_are_disabled_once_for_show_safety(self):
        self.assertIn('"show_safe_stall_diagnostics_migrated": False', MAIN_SOURCE)
        migration = MAIN_SOURCE[
            MAIN_SOURCE.index('if not bool(self.settings.get("show_safe_stall_diagnostics_migrated", False))'):
        ]
        self.assertIn('self.settings["stall_event_attribution"] = False', migration)
        self.assertIn('self.settings["stall_stack_capture"] = False', migration)
        self.assertIn('self.settings["show_safe_stall_diagnostics_migrated"] = True', migration)

    def test_tracks_json_is_parsed_off_the_gui_thread(self):
        # ~134k rows is ~800ms of JSON parse, and it ran inside __init__ before
        # the window could paint. Nothing in startup needs it.
        # There are two load_data methods; this is KaraokeApp's.
        app_start = MAIN_SOURCE.index("class KaraokeApp(QWidget):")
        load_start = MAIN_SOURCE.index("    def load_data(self):", app_start)
        load = MAIN_SOURCE[load_start:MAIN_SOURCE.index("\n    def ", load_start + 10)]
        self.assertIn("self._start_tracks_prefetch()", load)
        self.assertNotIn("_load_json_file(TRACKS_PATH", load)
        prefetch = function_source("_start_tracks_prefetch")
        self.assertIn('name="singws-tracks-load"', prefetch)
        self.assertIn("daemon=True", prefetch)
        self.assertIn("_load_json_file(TRACKS_PATH", prefetch)
        # An explicit reassignment during the load must win over the worker.
        self.assertIn('if not state["ready"]:', prefetch)
        # The property must join, so nothing can observe a half-loaded library.
        getter = function_source("tracks")
        self.assertIn("thread.join()", getter)

    def test_empty_library_tripwire_reads_the_tracks_property_state(self):
        # tracks is a property now, so it no longer lands in __dict__; the
        # catastrophic-config warning must not silently stop firing.
        intake = function_source("process_external_request")
        self.assertIn('_state.get("_tracks_state_data")', intake)
        self.assertIn('_tracks_state.get("ready")', intake)
        self.assertIn('_tracks_known and not _tracks_state.get("value")', intake)
        self.assertNotIn('if "tracks" in _state and not _state.get("tracks"):', MAIN_SOURCE)

    def test_location_detection_does_not_freeze_the_ui(self):
        # NSRunLoop.runUntilDate_ pumps native events but not Qt's, so the app
        # froze for up to the full timeout while CoreLocation worked.
        detect = function_source("_detect_current_device_location")
        self.assertIn("qt_app.processEvents(", detect)
        self.assertIn("QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents", detect)
        self.assertIn(
            "from PyQt6.QtCore import QUrl, QItemSelectionModel, QUrlQuery, QEventLoop",
            MAIN_SOURCE,
        )
        # The wait itself is unchanged (a short timeout traded accuracy for
        # speed and was reverted); only the UI freeze during it is fixed.
        self.assertNotIn("SAVE_LOCATION_DETECT_TIMEOUT_SEC", MAIN_SOURCE)
        # A persisted request marker may outlive the app's macOS TCC identity
        # after an update/re-sign. CoreLocation status, not that marker, decides
        # whether authorization should be requested again.
        self.assertNotIn(
            'and not bool(self.settings.get("session_location_permission_requested", False))',
            detect,
        )
        self.assertIn("manager.requestWhenInUseAuthorization()", detect)
        network = function_source("configure_network")
        self.assertIn("detect_location_now(show_result=False)", network)

    def test_remote_reconcile_never_saves_synchronously(self):
        # Reconcile ends by persisting queue + singer history + singer prefs.
        # Doing that inline blocked the GUI thread on every pass that accepted
        # nothing, which is most of them.
        reconcile = function_source("_reconcile_remote_requests")
        tail = reconcile[reconcile.index("_queue_display_batch_dirty = False"):]
        self.assertIn("self._schedule_save_data(1500)", tail)
        self.assertNotIn("self.save_data()", tail)

    def test_perf_diagnostics_reach_the_log_file(self):
        # print() goes nowhere in the packaged .app, so these measurements
        # were invisible in ~/SingWS/logs exactly when they were needed.
        perf = MAIN_SOURCE[
            MAIN_SOURCE.index("def _perf_log_if_slow"):MAIN_SOURCE.index("def _fit_dialog_to_screen")
        ]
        self.assertIn('_diag(f"[PERF-DIAG] {name} took', perf)
        self.assertNotIn('print(f"[PERF-DIAG]', perf)

    def test_diagnostic_logging_is_asynchronous(self):
        # Every _diag line used to be written three times synchronously
        # (stdout, stderr, file) on the GUI thread.
        setup = MAIN_SOURCE[
            MAIN_SOURCE.index("def setup_logging"):MAIN_SOURCE.index("# Initialize logging")
        ]
        # The handler subclasses QueueHandler; assert the behaviour, not the
        # spelling, so a rename does not read as a regression.
        self.assertIn("_BoundedLogQueueHandler(log_queue)", setup)
        self.assertIn("logging.handlers.QueueListener", setup)
        self.assertIn("listener.start()", setup)
        # The queue must be BOUNDED. It was an unbounded SimpleQueue, so a burst
        # of logging grew memory with no ceiling -- a release blocker in
        # docs/intelligent_audio/FINAL_AUDIT.md.
        self.assertIn("queue.Queue(maxsize=_LOG_QUEUE_MAX)", setup)
        self.assertNotIn("queue.SimpleQueue()", setup)

    def test_log_queue_drops_are_bounded_counted_and_reported(self):
        """The queue was an unbounded SimpleQueue, so a burst grew memory with
        no ceiling. Overflow must now drop, count, and say so."""
        import logging as _logging
        import queue as _queue

        q = _queue.Queue(maxsize=3)
        handler = self.singws._BoundedLogQueueHandler(q)

        def record(msg):
            return _logging.LogRecord("t", _logging.INFO, __file__, 0, msg, None, None)

        for i in range(10):
            handler.enqueue(record(f"line {i}"))
        self.assertEqual(q.qsize(), 3, "queue must not grow past its maximum")
        self.assertEqual(handler.dropped, 7, "every dropped record must be counted")

        # Drain, then enqueue again: the gap is reported rather than hidden.
        while not q.empty():
            q.get_nowait()
        handler.enqueue(record("after"))
        drained = []
        while not q.empty():
            drained.append(q.get_nowait())
        messages = [r.getMessage() for r in drained]
        self.assertTrue(any("dropped 7 record(s)" in m for m in messages),
                        f"expected a drop report, got {messages}")
        self.assertEqual(handler.dropped, 0, "the counter resets once reported")
        diag = MAIN_SOURCE[
            MAIN_SOURCE.index("def _diag(msg: str):"):MAIN_SOURCE.index("def _diag_rate_limited")
        ]
        self.assertIn("logging.info(msg)", diag)
        # The tail of the log must survive a crash / shutdown.
        self.assertIn("flush_log_queue()", function_source("log_crash"))
        self.assertIn("flush_log_queue()", function_source("_on_app_about_to_quit"))
        package = MAIN_SOURCE[
            MAIN_SOURCE.index("def prepare_log_email_package"):MAIN_SOURCE.index("def send_log_package_via_smtp")
        ]
        self.assertIn("flush_log_queue()", package)

    def test_singer_history_song_list_uses_model_backed_view(self):
        self.assertIn("class SingerHistorySongListModel(QAbstractListModel)", MAIN_SOURCE)
        self.assertIn("class SingerHistorySongsBuildWorker(QObject)", MAIN_SOURCE)
        model = MAIN_SOURCE[MAIN_SOURCE.index("class SingerHistorySongListModel"):MAIN_SOURCE.index("class SingerHistorySingerListModel")]
        self.assertIn("if rows == self._rows:", model)
        self.assertIn("self.dataChanged.emit", model)
        page = function_source("_build_singer_history_page")
        self.assertIn("self.singer_history_songs_model = SingerHistorySongListModel(self)", page)
        self.assertIn("self.singer_history_songs_list = QListView()", page)
        self.assertIn("self.singer_history_songs_list.doubleClicked.connect", page)
        self.assertIn("self.singer_history_songs_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)", page)
        self.assertIn("self.singer_history_songs_list.setTextElideMode(Qt.TextElideMode.ElideNone)", page)
        self.assertIn("self.singer_history_songs_list.setItemDelegate(WrapHeightItemDelegate", page)
        details = function_source("_update_singer_history_details")
        self.assertIn("SingerHistorySongsBuildWorker", details)
        self.assertIn("worker.moveToThread(thread)", details)
        apply_rows = function_source("_apply_singer_history_song_rows")
        self.assertIn("self.singer_history_songs_model.setRows(rows)", apply_rows)
        self.assertNotIn("QListWidgetItem(self._history_song_display", details)
        selector = function_source("_selected_singer_history_song_key")
        self.assertIn("model.songKeyForIndex(view.currentIndex())", selector)

    def test_singer_history_directory_uses_model_backed_view(self):
        self.assertIn("class SingerHistorySingerListModel(QAbstractListModel)", MAIN_SOURCE)
        self.assertIn("class SingerHistoryDirectoryBuildWorker(QObject)", MAIN_SOURCE)
        self.assertIn("class SingerHistoryBrandChoicesWorker(QObject)", MAIN_SOURCE)
        model = MAIN_SOURCE[MAIN_SOURCE.index("class SingerHistorySingerListModel"):MAIN_SOURCE.index("class SingerHistoryDirectoryBuildWorker")]
        self.assertIn("if rows == self._rows:", model)
        self.assertIn("self.dataChanged.emit", model)
        page = function_source("_build_singer_history_page")
        self.assertIn("self.singer_history_singer_model = SingerHistorySingerListModel(self)", page)
        self.assertIn("self.singer_history_singer_list = QListView()", page)
        self.assertIn("selectionModel().currentChanged.connect", page)
        self.assertIn("self.singer_history_singer_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)", page)
        self.assertIn("self.singer_history_singer_list.setTextElideMode(Qt.TextElideMode.ElideNone)", page)
        self.assertIn("self.singer_history_singer_list.setItemDelegate", page)
        refresh = function_source("_refresh_singer_history_view")
        self.assertIn("SingerHistoryDirectoryBuildWorker", refresh)
        self.assertIn("worker.moveToThread(thread)", refresh)
        apply_rows = function_source("_apply_singer_history_directory_rows")
        self.assertIn("self.singer_history_singer_model.setRows(rows)", apply_rows)
        self.assertNotIn("QListWidgetItem(\"\\n\".join(lines))", refresh)
        self.assertNotIn("self.singer_history_singer_list.addItem", refresh)
        selector = function_source("_selected_singer_history_key")
        self.assertIn("model.singerKeyForIndex(view.currentIndex())", selector)

    def test_singer_history_brand_choices_never_scan_library_on_selection(self):
        choices = function_source("_history_brand_choices")
        combo = function_source("_refresh_singer_history_brand_combo")
        self.assertIn("_ensure_history_brand_choices_async()", choices)
        self.assertIn("SingerHistoryBrandChoicesWorker", MAIN_SOURCE)
        self.assertIn("worker.moveToThread(thread)", function_source("_ensure_history_brand_choices_async"))
        self.assertIn("allow_sync_build=False", combo)
        details = function_source("_update_singer_history_details")
        self.assertNotIn("allow_sync_build=True", details)

    def test_singer_history_brand_choices_cache_key_method_is_not_shadowed(self):
        self.assertIn("def _history_brand_choices_cache_key", MAIN_SOURCE)
        self.assertIn("_history_brand_choices_cached_key", MAIN_SOURCE)
        self.assertNotIn("self._history_brand_choices_cache_key =", MAIN_SOURCE)

    def test_daw_preview_network_failures_have_backoff(self):
        self.assertIn("def _daw_preview_server_backoff_active", MAIN_SOURCE)
        self.assertIn("def _record_daw_preview_server_failure", MAIN_SOURCE)
        self.assertIn("def _record_daw_preview_server_success", MAIN_SOURCE)
        scheduler = function_source("_schedule_daw_singer_screen_snapshot")
        self.assertIn("_daw_preview_server_backoff_active", scheduler)
        self.assertLess(
            scheduler.index("_daw_preview_server_backoff_active"),
            scheduler.index("requests.get("),
        )
        self.assertIn("_record_daw_preview_server_failure", scheduler)
        self.assertIn("_record_daw_preview_server_success", scheduler)
        uploader = function_source("_post_daw_singer_screen_snapshot")
        self.assertIn("_record_daw_preview_server_failure", uploader)
        self.assertIn("_record_daw_preview_server_success", uploader)

    def test_daw_preview_frame_callbacks_honor_viewer_and_server_backoff(self):
        frame_sender = function_source("_maybe_send_daw_snapshot_from_frame")
        self.assertIn("_daw_snapshot_viewer_recent()", frame_sender)
        self.assertIn("_daw_preview_server_backoff_active", frame_sender)
        uploader = function_source("_post_daw_singer_screen_snapshot")
        self.assertIn("_daw_preview_server_backoff_active", uploader)

    def test_daw_preview_does_not_capture_without_recent_viewer(self):
        scheduler = function_source("_schedule_daw_singer_screen_snapshot")
        self.assertIn("should_capture = enabled and viewer_recent", scheduler)
        self.assertNotIn("viewer_recent or playing or bool(force)", scheduler)

    def test_daw_preview_timer_is_adaptive(self):
        init_source = MAIN_SOURCE[MAIN_SOURCE.index("self._daw_snapshot_timer = QTimer(self)"):]
        init_source = init_source[:init_source.index("self.rotation_view = None")]
        self.assertIn('self._retune_daw_snapshot_timer("startup")', init_source)
        self.assertNotIn("self._daw_snapshot_timer.start(1000)", init_source)
        target = function_source("_daw_snapshot_timer_target_ms")
        self.assertIn("return 1000", target)
        self.assertIn("return 5000", target)
        self.assertLess(
            target.index("_daw_preview_server_backoff_until"),
            target.index("_daw_snapshot_viewer_recent()"),
        )
        self.assertIn("_daw_snapshot_viewer_recent()", target)
        self.assertNotIn("karaoke_playing", target)
        tuner = function_source("_retune_daw_snapshot_timer")
        self.assertIn("timer.stop()", tuner)
        self.assertIn("timer.start(target_ms)", tuner)
        scheduler = function_source("_schedule_daw_singer_screen_snapshot")
        self.assertIn("_daw_snapshot_viewer_recent_until", scheduler)
        self.assertIn('_retune_daw_snapshot_timer("viewer_check")', scheduler)
        settings = MAIN_SOURCE[MAIN_SOURCE.index("def on_daw_preview_toggled"):]
        settings = settings[:settings.index("daw_preview_cb.toggled.connect")]
        self.assertIn('_retune_daw_snapshot_timer("settings_toggle")', settings)

    def test_slow_sync_perf_logs_are_rate_limited(self):
        source = self.singws._perf_log_if_slow.__code__.co_consts
        joined = " ".join(str(item) for item in source)
        self.assertIn("server", joined)
        self.assertIn("sync", joined)
        self.assertIn("json", joined)
        self.assertIn("15.0", joined)

    def test_idle_show_screen_background_changes_fade(self):
        self.assertIn("_background_fade_timer = QTimer(self)", MAIN_SOURCE)
        setter = function_source("set_background_image")
        self.assertIn("_background_previous_pixmap", setter)
        self.assertIn("_background_fade_started = time.monotonic()", setter)
        self.assertIn("_background_fade_timer.start(33)", setter)
        forced = function_source("fade_background_from_black")
        self.assertIn("_background_forced_fade_last", forced)
        self.assertIn("_background_previous_pixmap = QPixmap()", forced)
        apply_idle = function_source("_apply_idle_background")
        self.assertIn("fade_background_from_black()", apply_idle)
        self.assertLess(apply_idle.index("self.video_window.set_background_image(path)"), apply_idle.index("fade_background_from_black()"))
        paint = function_source("paintEvent")
        self.assertIn("fade_alpha = self._background_fade_alpha()", paint)
        self.assertIn("painter.setOpacity(fade_alpha)", paint)
        self.assertIn("_draw_background_pixmap(painter, previous)", paint)


    def test_volume_analysis_dialog_is_resurfaced_frontmost(self):
        analyze = function_source("analyze_library")
        start = function_source("_start_analyze_library_items")
        self.assertIn("WindowStaysOnTopHint", start)
        self.assertIn('scan_subject = "transition scan"', start)
        self.assertIn("{scan_subject} for", start)
        self.assertIn("self._bring_analyze_dialog_to_front(_d)", analyze)
        self.assertIn("self._bring_analyze_dialog_to_front(dlg)", start)
        bring = function_source("_bring_analyze_dialog_to_front")
        self.assertIn("dlg.show()", bring)
        self.assertIn("dlg.raise_()", bring)
        self.assertIn("dlg.activateWindow()", bring)
        self.assertIn("QTimer.singleShot", bring)

    def test_analyze_dialog_retry_survives_a_closed_dialog(self):
        """The delayed raise must guard the callback body, not the scheduling.

        Closing the progress dialog inside the 750ms retry window made PyQt
        raise "wrapped C/C++ object of type QProgressDialog has been deleted"
        into the event loop; it was logged as four crashes during the
        2026-08-16 show. Guarding QTimer.singleShot() alone does not help,
        because the failure happens when the callback runs.
        """
        bring = function_source("_bring_analyze_dialog_to_front")
        self.assertNotIn("QTimer.singleShot(delay_ms, lambda", bring)
        callback = bring[bring.index("def _raise_again"):]
        # The three calls that can touch a deleted object must sit inside a
        # try that catches RuntimeError, before the scheduling loop.
        guarded = callback[:callback.index("for delay_ms")]
        self.assertIn("try:", guarded)
        self.assertIn("except RuntimeError:", guarded)
        for call in ("d.show()", "d.raise_()", "d.activateWindow()"):
            self.assertIn(call, guarded)
            self.assertLess(guarded.index("try:"), guarded.index(call))

    def test_cdg_near_black_cleanup_clamps_only_black_pixels(self):
        image = QImage(3, 1, QImage.Format.Format_ARGB32)
        image.setPixelColor(0, 0, QColor(4, 6, 8))
        image.setPixelColor(1, 0, QColor(9, 2, 30))
        image.setPixelColor(2, 0, QColor(220, 220, 220))
        cleaned = self.singws._clean_cdg_near_black(image, 10)
        self.assertEqual(cleaned.pixelColor(0, 0).getRgb()[:3], (0, 0, 0))
        self.assertEqual(cleaned.pixelColor(1, 0).getRgb()[:3], (9, 2, 30))
        self.assertEqual(cleaned.pixelColor(2, 0).getRgb()[:3], (220, 220, 220))
        transparent = self.singws._clean_cdg_near_black(image, 10, transparent_black=True)
        self.assertEqual(transparent.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(transparent.pixelColor(1, 0).alpha(), 255)
        self.assertEqual(transparent.pixelColor(2, 0).alpha(), 255)

    def test_cdg_near_black_cleanup_uses_indexed_palette_fast_path(self):
        image = QImage(3, 1, QImage.Format.Format_Indexed8)
        image.setColorTable([
            QColor(4, 6, 8).rgba(),
            QColor(9, 2, 30).rgba(),
            QColor(220, 220, 220).rgba(),
        ])
        image.setPixel(0, 0, 0)
        image.setPixel(1, 0, 1)
        image.setPixel(2, 0, 2)

        cleaned = self.singws._clean_cdg_near_black(image, 10)
        self.assertEqual(cleaned.format(), QImage.Format.Format_Indexed8)
        self.assertEqual(cleaned.pixelColor(0, 0).getRgb()[:3], (0, 0, 0))
        self.assertEqual(cleaned.pixelColor(1, 0).getRgb()[:3], (9, 2, 30))
        self.assertEqual(cleaned.pixelColor(2, 0).getRgb()[:3], (220, 220, 220))

        transparent = self.singws._clean_cdg_near_black(image, 10, transparent_black=True)
        self.assertEqual(transparent.format(), QImage.Format.Format_Indexed8)
        self.assertEqual(transparent.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(transparent.pixelColor(1, 0).alpha(), 255)
        self.assertEqual(transparent.pixelColor(2, 0).alpha(), 255)

    def test_cdg_near_black_cleanup_scales_to_full_resolution_rgba(self):
        # The RGBA path must handle a full-resolution frame (e.g. an MP4 frame,
        # up to 4K) without the old per-pixel Python loop that froze the GUI
        # thread. This exercises the vectorized clamp on a non-trivial frame and
        # pins its correctness at scale.
        w, h = 640, 480
        image = QImage(w, h, QImage.Format.Format_ARGB32)
        image.fill(QColor(3, 5, 7))                 # near-black background
        for x in range(0, w, 40):
            image.setPixelColor(x, 5, QColor(255, 190, 0))   # bright "lyrics"
        image.setPixelColor(1, 1, QColor(0, 0, 0))  # pure black stays pure black

        cleaned = self.singws._clean_cdg_near_black(image, 10)
        # near-black background clamped to pure black
        self.assertEqual(cleaned.pixelColor(300, 300).getRgb()[:3], (0, 0, 0))
        # bright text preserved
        self.assertEqual(cleaned.pixelColor(0, 5).getRgb()[:3], (255, 190, 0))

        transparent = self.singws._clean_cdg_near_black(image, 10, transparent_black=True)
        self.assertEqual(transparent.pixelColor(300, 300).alpha(), 0)     # bg see-through
        self.assertEqual(transparent.pixelColor(0, 5).alpha(), 255)       # text opaque

    def test_audio_chain_logs_cover_bgm_and_karaoke(self):
        bg_chain = function_source("_bg_dsp_chain_label")
        self.assertIn("master_audio", bg_chain)
        karaoke_log = function_source("_log_karaoke_audio_chain")
        self.assertIn("[KARAOKE-AUDIO]", karaoke_log)
        self.assertIn("normalize", karaoke_log)
        self.assertIn("master_audio", karaoke_log)

    def test_main_thread_stall_watchdog_is_diagnostic_gated(self):
        self.assertIn("def _install_main_thread_watchdog", MAIN_SOURCE)
        start = MAIN_SOURCE.index("def _install_main_thread_watchdog")
        end = MAIN_SOURCE.index("# Settings defaults", start)
        watchdog = MAIN_SOURCE[start:end]
        self.assertIn("SINGWS_NO_WATCHDOG", watchdog)
        self.assertIn("performance_debug_enabled", watchdog)
        self.assertIn("[STALL]", watchdog)
        self.assertIn("sys._current_frames()", watchdog)
        self.assertIn("singws-main-thread-watchdog", watchdog)
        self.assertIn("_install_main_thread_watchdog(self)", MAIN_SOURCE)
        self.assertIn("self._mt_watch_stop = True", MAIN_SOURCE)

    def test_runtime_diagnostics_are_opt_in_by_default(self):
        self.assertIn('"performance_debug_enabled": False', MAIN_SOURCE)
        self.assertIn('"performance_debug_default_migrated": False', MAIN_SOURCE)
        self.assertIn('self.settings["performance_debug_enabled"] = False', MAIN_SOURCE)
        startup = MAIN_SOURCE[
            MAIN_SOURCE.index("# DIAGNOSTIC: Freeze detector"):
            MAIN_SOURCE.index("self.setup_selection_behavior()", MAIN_SOURCE.index("# DIAGNOSTIC: Freeze detector"))
        ]
        self.assertIn('self.settings.get("performance_debug_enabled", False)', startup)
        self.assertIn("SingWSLogger.log_gstreamer_runtime_diagnostics()", startup)
        self.assertIn("SingWSLogger.log_library_stats", startup)

    def test_show_screen_does_not_paint_idle_background_during_active_karaoke(self):
        start = MAIN_SOURCE.index("class VideoAreaWidget")
        end = MAIN_SOURCE.index("class MusicDatabaseWidget", start)
        video_area = MAIN_SOURCE[start:end]
        self.assertIn("def _is_live_karaoke_active", video_area)
        self.assertIn('getattr(owner, "karaoke_playing", False)', video_area)
        self.assertIn('getattr(vw, "idle", True)', video_area)
        paint = function_source("paintEvent")
        self.assertIn("not self._is_live_karaoke_active()", paint)

    def test_active_surface_reset_keeps_native_widgets_alive(self):
        refresh = function_source("_recreate_video_surfaces")
        self.assertIn("clear_karaoke_frame", refresh)
        self.assertIn("surface_refresh", refresh)
        self.assertNotIn("recreate_video_surface(reason)", refresh)
        self.assertNotIn("deleteLater", refresh)

        preview_idle = function_source("_reassert_preview_idle")
        self.assertIn("clear_karaoke_frame", preview_idle)
        self.assertNotIn("recreate_video_surface", preview_idle)

        idle_recovery = function_source("_recover_idle_output")
        self.assertNotIn(".repaint()", idle_recovery)

    def test_idle_overlay_tick_never_forces_synchronous_backing_store_flush(self):
        tick = function_source("_tick_idle_overlay")
        self.assertIn("self.video_area.update()", tick)
        self.assertNotIn("self.video_area.repaint()", tick)


    def test_intel_qimage_outputs_are_not_promoted_native_for_diagnostics(self):
        video_init = MAIN_SOURCE[MAIN_SOURCE.index("class VideoWindow(QWidget):"):MAIN_SOURCE.index("def _attach_show_vfx", MAIN_SOURCE.index("class VideoWindow(QWidget):"))]
        self.assertNotIn("int(self.video_area.winId())", video_init)
        preview_init = MAIN_SOURCE[MAIN_SOURCE.index("class PreviewWindow(QWidget):"):MAIN_SOURCE.index("def winId", MAIN_SOURCE.index("class PreviewWindow(QWidget):"))]
        self.assertNotIn("WA_NativeWindow", preview_init)

    def test_fallback_transition_timeout_is_owned_by_video_area(self):
        start = MAIN_SOURCE.index("class VideoAreaWidget")
        end = MAIN_SOURCE.index("class MusicDatabaseWidget", start)
        video_area = MAIN_SOURCE[start:end]
        self.assertIn("self._fallback_transition_timer = QTimer(self)", video_area)
        self.assertIn(
            "self._fallback_transition_timer.timeout.connect(self._clear_fallback_transition)",
            video_area,
        )
        fallback = function_source("_show_fallback_transition")
        self.assertIn("self._fallback_transition_timer.start(", fallback)
        self.assertNotIn("QTimer.singleShot", fallback)

    def test_removed_slow_computer_settings_are_migrated(self):
        source = MAIN_SOURCE
        self.assertNotIn('"performance_mode": False', source)
        self.assertNotIn('"safe_mode": False', source)
        self.assertNotIn('"mp4_max_height"', source.replace(
            'for obsolete_key in ("performance_mode", "safe_mode", "mp4_max_height")', ""
        ))
        self.assertIn(
            'for obsolete_key in ("performance_mode", "safe_mode", "mp4_max_height")',
            source,
        )
        # mpv is the shipping video path, so nothing may cap decode resolution
        # or force show effects off on Intel any more.
        self.assertNotIn("_effective_mp4_max_height", source)
        self.assertNotIn("_intel_show_effects_backoff_logged", source)

    def test_karaoke_to_bgm_overlap_requires_explicit_setting(self):
        self.assertIn('"karaoke_bgm_crossfade_enabled": False', MAIN_SOURCE)
        self.assertIn('"karaoke_allow_early_silence_trim": False', MAIN_SOURCE)
        overlap_gate = function_source("_karaoke_bgm_crossfade_enabled")
        self.assertIn('settings.get("seamless_transitions_enabled", True)', overlap_gate)
        self.assertIn('settings.get("karaoke_bgm_crossfade_enabled", False)', overlap_gate)
        timer_tick = function_source("update_time_left")
        self.assertIn("crossfade_enabled = self._karaoke_bgm_crossfade_enabled()", timer_tick)
        self.assertIn("if (crossfade_enabled", timer_tick)
        self.assertNotIn("silent_prefire", timer_tick)
        trim = function_source("_maybe_trim_end_silence")
        self.assertIn("self._karaoke_early_silence_trim_enabled()", trim)
        self.assertIn("_karaoke_visual_end_s", trim)
        self.assertIn("visual endpoint unverified", trim)
        self.assertIn("ending karaoke before EOS", trim)
        self.assertIn('_handle_media_end_safe("verified_silent_tail")', trim)

    def test_trailing_silence_uses_shorter_safe_handoff_threshold(self):
        self.assertIn('"end_silence_trim_threshold_sec": 2.5', MAIN_SOURCE)
        self.assertIn("self._end_silence_min_s = 2.5", MAIN_SOURCE)
        self.assertIn("old_threshold in (5.0, 6.0)", MAIN_SOURCE)
        self.assertIn('self.settings["end_silence_trim_threshold_sec"] = 2.5', MAIN_SOURCE)
        trim = function_source("_maybe_trim_end_silence")
        self.assertIn("visual_confidence < 0.85", trim)
        self.assertIn("near_end", trim)

    def test_silence_trim_requires_verified_audio_endpoint(self):
        trim = function_source("_maybe_trim_end_silence")
        unknown_guard = trim.index("if audio_end is None:")
        handoff = trim.index("self._end_silence_tail_handoff_started = True")
        self.assertLess(unknown_guard, handoff)
        self.assertIn("trim suppressed; verified audio endpoint unavailable", trim)
        self.assertIn('_end_audio_unknown_logged', trim)
        self.assertIn("return False", trim[unknown_guard:handoff])

    def test_legacy_silence_trim_keeps_verified_visual_endpoint(self):
        trim = function_source("_maybe_trim_end_silence")
        visual_guard = trim.index("if visual_end is None or visual_confidence < 0.85:")
        handoff = trim.rindex("self._end_silence_tail_handoff_started = True")
        self.assertLess(visual_guard, handoff)
        self.assertIn("visual endpoint unverified", trim)
        arm = function_source("_arm_visual_end_floor")
        self.assertIn("transition_analysis_cached", arm)
        self.assertIn('if mode != "cdg":', arm)
        self.assertIn("transition_analysis.analyze_cdg_visual", arm)
        self.assertIn("MP4 decoding is", arm)
        self.assertIn("physical-duration fallback", arm)
        self.assertIn('self.settings.get("seamless_transitions_enabled", True)', MAIN_SOURCE)
        self.assertIn('QCheckBox("Seamless transitions")', MAIN_SOURCE)
        self.assertIn("using physical EOS fallback", MAIN_SOURCE)
        self.assertIn("threading.Thread", arm)

    def test_scanned_tail_ends_promptly_without_waiting_for_graphics(self):
        timer = mock.Mock()
        namespace = {"time": __import__("time"), "NS_PER_SECOND": 1_000_000_000,
                     "QTimer": timer, "_diag": lambda *args: None,
                     "_ia_record": lambda *args, **kwargs: None}  # Phase 0 passive instrumentation
        exec(textwrap.dedent(function_source("_maybe_trim_end_silence")), namespace)
        trim = namespace["_maybe_trim_end_silence"]
        host = types.SimpleNamespace(
            settings={"karaoke_trim_verified_tail": True},
            _karaoke_early_silence_trim_enabled=lambda: True,
            _end_silence_mode="cdg", _end_silence_min_elapsed_s=20,
            _end_silence_db_threshold=-38, _read_level_db=lambda: None,
            _end_trim_threshold_sec=lambda: 2.5, _karaoke_audio_end_s=100.0,
            _handle_media_end_safe=mock.Mock(),
        )
        ns = 1_000_000_000
        self.assertFalse(trim(host, 115*ns, 99*ns))
        self.assertFalse(trim(host, 115*ns, int(100.1*ns)))
        host._karaoke_audio_end_s = None
        self.assertFalse(trim(host, 115*ns, 101*ns))
        host._karaoke_audio_end_s = 100.0
        host._read_level_db = lambda: -15.0
        self.assertFalse(trim(host, 115*ns, 101*ns))
        host._read_level_db = lambda: None
        self.assertTrue(trim(host, 115*ns, 101*ns))
        self.assertFalse(trim(host, 115*ns, 102*ns))
        timer.singleShot.assert_called_once()
        timer.singleShot.call_args.args[1]()
        host._handle_media_end_safe.assert_called_once_with("verified_silent_tail")

    def test_verified_audio_tail_can_crossfade_while_cdg_final_card_remains(self):
        trim = function_source("_maybe_trim_end_silence")
        audio_crossfade = trim.index("self._prefire_bgm_at_verified_audio_end()")
        visual_guard = trim.index("if visual_end is None or visual_confidence < 0.85:")
        self.assertLess(audio_crossfade, visual_guard)

        prefire = function_source("_prefire_bgm_at_verified_audio_end")
        self.assertIn('self.settings.get("karaoke_auto_advance", False)', prefire)
        self.assertIn("_karaoke_bgm_crossfade_enabled()", prefire)
        self.assertIn("bg_music.fade_in(None, 3000, allow_during_karaoke=True)", prefire)
        self.assertIn("karaoke visuals retained until their safe endpoint", prefire)
        self.assertNotIn("_handle_media_end_safe", prefire)

    def test_verified_silent_tail_completes_karaoke_before_decoder_eos(self):
        trim = function_source("_maybe_trim_end_silence")
        self.assertIn("verified tail complete", trim)
        self.assertIn("_prefire_bgm_at_verified_audio_end", trim)
        self.assertIn(
            "bg_music.fade_in",
            function_source("_prefire_bgm_at_verified_audio_end"),
        )
        self.assertIn('_handle_media_end_safe("verified_silent_tail")', trim)
        self.assertIn("_end_silence_triggered = True", trim)
        self.assertIn('_end_silence_auto_advance_next = bool(', trim)
        self.assertIn('self.settings.get("karaoke_auto_advance", False)', trim)



    def test_large_tracks_json_save_runs_off_the_gui_thread(self):
        persist = function_source("_persist_tracks_json")
        self.assertNotIn("_save_json_atomic", persist)
        self.assertIn("_start_tracks_save_worker", persist)
        worker = function_source("_start_tracks_save_worker")
        self.assertIn("threading.Thread", worker)
        self.assertIn("singws-save-tracks", worker)

    def test_confirmed_manual_stop_unwinds_dialog_before_surface_handoff(self):
        stop = function_source("stop_and_clear_now_singing")
        self.assertIn("_manual_stop_deferred", stop)
        self.assertIn("QTimer.singleShot(75, finish_confirmed_stop)", stop)
        self.assertIn("skip_confirmation=True", stop)





    def test_singer_history_render_is_capped_harder_during_playback(self):
        refresh = function_source("_refresh_singer_history_view")
        self.assertIn("render_limit = 220 if playing else 700", refresh)
        self.assertIn("song_scan_limit = 120 if playing else 500", refresh)

    def test_rotation_fallback_uses_ticker_time_based_scroll_cadence(self):
        start = MAIN_SOURCE.index("class RotationView(QMainWindow)")
        end = MAIN_SOURCE.index("class SoundboardPad", start)
        rotation = MAIN_SOURCE[start:end]
        step_start = rotation.index("    def scroll_step(self):")
        step_end = rotation.index("\n    def ", step_start + 8)
        step = rotation[step_start:step_end]
        self.assertIn("Qt.TimerType.PreciseTimer", rotation)
        self.assertIn("ticker_frame_interval_ms_for_refresh", rotation)
        self.assertIn("time.monotonic()", step)
        self.assertIn("_scroll_speed_px_per_sec", step)
        self.assertIn("* dt", step)
        self.assertNotIn("sb.value() + 1", step)

    def test_rotation_gpu_rail_wraps_entirely_on_render_thread(self):
        start = MAIN_SOURCE.index('QML_ROTATION_RAIL_SOURCE = r"""')
        end = MAIN_SOURCE.index('class RenderThreadRotationRail', start)
        qml = MAIN_SOURCE[start:end]
        animator_start = qml.index("    YAnimator {\n        id: scrollAnim")
        animator = qml[animator_start:qml.index("    SequentialAnimation {", animator_start)]
        self.assertIn("loops: Animation.Infinite", animator)
        self.assertNotIn("onFinished", animator)

    def test_rotation_open_reasserts_only_the_show_screen_ticker(self):
        opened = function_source("open_rotation_view")
        reassert = function_source("_reassert_show_ticker_after_rotation_open")
        shared = function_source("_reassert_show_ticker_surface")
        native_raise = function_source("_raise_ticker_surface")
        self.assertIn("_reassert_show_ticker_after_rotation_open", opened)
        self.assertIn('_reassert_show_ticker_surface("rotation_open")', reassert)
        self.assertIn("video_window.set_ticker_enabled(True)", shared)
        self.assertIn("video_window._raise_ticker_surface()", shared)
        self.assertIn("ticker.raise_()", native_raise)
        self.assertNotIn("rotation_view.ticker", opened + reassert + shared)
        self.assertNotIn("video_window.activateWindow", shared)

    def test_video_window_reasserts_ticker_after_display_geometry_settles(self):
        start = MAIN_SOURCE.index("class VideoWindow(QWidget)")
        end = MAIN_SOURCE.index("class AddToQueueDialog", start)
        video = MAIN_SOURCE[start:end]
        settled = function_source("_reassert_surfaces_after_geometry_change")
        self.assertIn("_geometry_surface_reassert_timer.setSingleShot(True)", video)
        self.assertIn(
            "def resizeEvent(self, event):\n        super().resizeEvent(event)\n"
            "        self._schedule_geometry_surface_reassert()",
            video,
        )
        self.assertIn(
            "def moveEvent(self, event):\n        super().moveEvent(event)\n"
            "        self._schedule_geometry_surface_reassert()",
            video,
        )
        self.assertIn('refresh("video_window_geometry")', settled)
        self.assertIn(
            'reassert("video_window_geometry", delays=(0, 120, 400))', settled
        )

    def test_mpv_reveal_reasserts_ticker_after_native_surface_settles(self):
        reveal = function_source("_set_mpv_hosts_visible")
        self.assertIn("host.raise_()", reveal)
        self.assertIn("_schedule_show_ticker_reassert", reveal)
        self.assertIn('"mpv_host_reveal"', reveal)
        # The bounded retry ladder moved into the shared scheduler when the
        # KaraFun show-screen restore needed the same repair.
        scheduler = function_source("_schedule_show_ticker_reassert")
        self.assertIn("delays=(0, 80, 250)", scheduler)
        self.assertIn("QTimer.singleShot", scheduler)
        self.assertIn("_reassert_show_ticker_surface", scheduler)

    def test_show_screen_ticker_guard_does_not_reorder_audience_parent(self):
        video_start = MAIN_SOURCE.index("class VideoWindow(QWidget)")
        video_end = MAIN_SOURCE.index("class AddToQueueDialog", video_start)
        video = MAIN_SOURCE[video_start:video_end]
        show_event = function_source("showEvent")
        self.assertIn("self._ticker_surface_guard_timer.start(1000)", video)
        guard = function_source("_tick_ticker_surface_guard")
        self.assertIn("self._raise_ticker_surface()", guard)
        self.assertIn("if not self.isVisible():", guard)
        self.assertIn('settings.get("ticker_enabled", True)', guard)
        self.assertNotIn("_reassert_show_window_surface", guard)
        self.assertNotIn("orderFront", guard)
        self.assertIn("QApplication.applicationState() != Qt.ApplicationState.ApplicationActive", guard)
        self.assertIn('reassert("video_window_show", delays=(0, 120, 400))', show_event)
        self.assertIn('reassert_window("video_window_show")', show_event)
        native_raise = function_source("_raise_ticker_surface")
        self.assertIn('getattr(ticker, "_container", None)', native_raise)
        self.assertIn("container.raise_()", native_raise)
        self.assertNotIn("addSubview_positioned_relativeTo_", native_raise)
        self.assertNotIn("NSWindowAbove", native_raise)
        self.assertNotIn("orderFront", native_raise)
        reassert = function_source("_reassert_show_ticker_surface")
        self.assertIn("video_window._raise_ticker_surface()", reassert)
        self.assertIn("native={int(bool(native_reordered))}", reassert)
        parent_reassert = function_source("_reassert_show_window_surface")
        self.assertIn("orderFrontRegardless", parent_reassert)
        self.assertIn("_active_external_karafun", parent_reassert)

    def test_background_ticker_guard_does_not_raise_or_hide_output(self):
        qt = self.singws.Qt
        fake = types.SimpleNamespace(
            isVisible=lambda: True,
            _external_owner=types.SimpleNamespace(settings={"ticker_enabled": True}),
            ticker=types.SimpleNamespace(isVisible=lambda: True),
            _raise_ticker_surface=mock.Mock(),
        )
        with mock.patch.object(self.singws.QApplication, "applicationState",
                               return_value=qt.ApplicationState.ApplicationInactive):
            self.singws.VideoWindow._tick_ticker_surface_guard(fake)
        fake._raise_ticker_surface.assert_not_called()
        with mock.patch.object(self.singws.QApplication, "applicationState",
                               return_value=qt.ApplicationState.ApplicationActive):
            self.singws.VideoWindow._tick_ticker_surface_guard(fake)
        fake._raise_ticker_surface.assert_called_once()

    def test_karafun_focus_restore_respects_other_foreground_apps(self):
        qt = self.singws.Qt
        frontmost = types.SimpleNamespace(localizedName=lambda: "Safari")
        workspace = types.SimpleNamespace(sharedWorkspace=lambda: types.SimpleNamespace(
            frontmostApplication=lambda: frontmost))
        with mock.patch.object(self.singws.QApplication, "applicationState",
                               return_value=qt.ApplicationState.ApplicationInactive), \
             mock.patch.object(self.singws.sys, "platform", "darwin"), \
             mock.patch.dict(sys.modules, {"AppKit": types.SimpleNamespace(NSWorkspace=workspace)}):
            self.assertFalse(self.singws.KaraokeApp._can_restore_host_focus_after_karafun(None))
            frontmost.localizedName = lambda: "KaraFun"
            self.assertTrue(self.singws.KaraokeApp._can_restore_host_focus_after_karafun(None))

    def test_delayed_focus_restore_does_not_activate_background_host(self):
        fake = types.SimpleNamespace(
            activateWindow=mock.Mock(),
            _can_restore_host_focus_after_karafun=lambda: False,
        )
        with mock.patch.object(self.singws.QApplication, "applicationState",
                               return_value=self.singws.Qt.ApplicationState.ApplicationInactive):
            self.singws.KaraokeApp._activate_host_if_foreground(fake)
            self.singws.KaraokeApp._activate_host_window_after_karafun(fake, attempt=1)
        fake.activateWindow.assert_not_called()

    def test_rotation_announcement_is_separate_and_venue_scoped(self):
        ticker_start = MAIN_SOURCE.index("class RotationAnnouncementTicker(QWidget)")
        ticker_end = MAIN_SOURCE.index("class RotationAnimatedBackdrop(QWidget)", ticker_start)
        view_start = MAIN_SOURCE.index("class RotationView(QMainWindow)", ticker_end)
        view_end = MAIN_SOURCE.index("class SoundboardPad", view_start)
        ticker = MAIN_SOURCE[ticker_start:ticker_end]
        view = MAIN_SOURCE[view_start:view_end]
        configure = function_source("configure_ticker")
        venue_start = MAIN_SOURCE.index("VENUE_SCOPED_SETTINGS = (")
        venue_end = MAIN_SOURCE.index("\n    )", venue_start)
        venue_settings = MAIN_SOURCE[venue_start:venue_end]
        self.assertIn("backend_type = DetachedPainterTicker", ticker)
        self.assertIn("backend_type = RenderThreadTicker", ticker)
        self.assertIn("backend_type = Ticker", ticker)
        self.assertIn("self._backend.force_refresh_now()", ticker)
        self.assertIn('"ticker_speed_px_per_sec"', ticker)
        self.assertNotIn("set_scroll_speed(72.0)", ticker)
        self.assertNotIn("time.monotonic()", ticker)
        self.assertIn("self.announcement_ticker = RotationAnnouncementTicker(self)", view)
        self.assertIn("Show announcement ticker on Singer Rotation screen", configure)
        self.assertIn('"rotation_announcement_enabled"', venue_settings)
        self.assertIn('"rotation_announcement_message"', venue_settings)
        self.assertNotIn("video_window.ticker", ticker)

    def test_singer_history_edits_use_debounced_save_path(self):
        source = function_source("_commit_singer_history_change")
        self.assertIn("_schedule_save_data", source)
        self.assertNotIn("self.save_data()", source)

    def test_singer_history_remains_readable_but_edits_are_locked_during_playback(self):
        refresh = function_source("_refresh_singer_history_view")
        details = function_source("_update_singer_history_details")
        directory_apply = function_source("_on_singer_history_directory_built")
        songs_apply = function_source("_on_singer_history_songs_built")
        self.assertNotIn('reason="playback_active"', refresh)
        self.assertNotIn("worker_finished_during_playback", directory_apply)
        self.assertNotIn("songs_worker_finished_during_playback", songs_apply)
        self.assertIn('editing_allowed = not bool(getattr(self, "karaoke_playing", False))', details)
        for name in (
            "_rename_selected_singer_history",
            "_add_song_to_selected_singer_history",
            "_edit_selected_singer_history_brand",
            "_edit_selected_singer_history_song",
            "_delete_selected_singer_history_song",
            "_delete_selected_singer_history",
            "_clear_all_singer_history",
            "_clear_small_singer_history",
            "_clear_inactive_singer_history",
        ):
            self.assertIn('getattr(self, "karaoke_playing", False)', function_source(name))

    def test_singer_chat_has_nightly_threads_unread_state_and_background_networking(self):
        build = function_source("_build_chat_page")
        poll = function_source("_schedule_chat_poll")
        send = function_source("_send_chat_message")
        clear = function_source("_clear_chat_history")
        self.assertIn("self.chat_singer_list", build)
        self.assertIn("self._chat_night_start", build)
        self.assertIn('host_chat.php', poll)
        self.assertIn('"since_time"', poll)
        self.assertIn("threading.Thread", poll)
        self.assertIn("_show_processing_notification", poll)
        self.assertIn("_net_send_direct_message", send)
        self.assertIn("Clear Chat History", build)
        self.assertIn('"action":"clear_history"', clear)
        self.assertIn("QMessageBox.StandardButton.Cancel", clear)
        self.assertIn("_chat_data_generation", poll)

    def test_hidden_singer_history_does_not_rebuild_models(self):
        schedule = function_source("_schedule_singer_history_refresh")
        self.assertIn("stack.currentWidget() is history_page", schedule)
        self.assertIn("_singer_history_refresh_hidden_deferred = True", schedule)
        self.assertIn("timer.stop()", schedule)
        refresh = function_source("_refresh_singer_history_view")
        self.assertIn(
            "self.left_workspace_stack.currentWidget() is not self.singer_history_page",
            refresh,
        )
        switch = function_source("_set_left_workspace_view")
        self.assertLess(
            switch.index("self.left_workspace_stack.setCurrentWidget(self.singer_history_page)"),
            switch.index('self._schedule_singer_history_refresh(reason="tab_switch")'),
        )

    def test_remote_reconcile_collapses_duplicate_singer_rows(self):
        reconcile = function_source("_reconcile_remote_requests")
        self.assertIn(
            '_merge_duplicate_rotation_singers(reason="remote_reconcile_start")',
            reconcile,
        )
        self.assertIn(
            '_merge_duplicate_rotation_singers(reason="remote_reconcile_finish")',
            reconcile,
        )

    def test_queue_insert_reuses_same_display_name_across_server_sessions(self):
        match = function_source("_queue_singer_match_index")
        self.assertIn("display-name identity collision reused existing row", match)
        self.assertNotIn("existing_server_id != singer_id:\n                    continue", match)
        add = function_source("_add_song_to_queue")
        self.assertIn("preserved existing row identity while attaching request", add)

    def test_queue_uses_model_backed_view_with_identity_roles(self):
        self.assertIn("class QueueListModel(QAbstractListModel)", MAIN_SOURCE)
        self.assertIn("class QueueListView(QListView)", MAIN_SOURCE)
        self.assertIn("_queue_row_kind_role", MAIN_SOURCE)
        self.assertIn("def _set_queue_row_identity", MAIN_SOURCE)
        page = MAIN_SOURCE[MAIN_SOURCE.index('self.queue_label = QLabel("Singers:0 (0:00)")'):]
        page = page[:page.index("# --- 1st Row: Move Up / Move Down / Remove ---")]
        self.assertIn("self.queue_display = QueueListView()", page)
        self.assertIn("self.queue_display_model = QueueListModel(self)", page)
        self.assertIn("self.queue_display.setModel(self.queue_display_model)", page)
        refresh = function_source("update_queue_display")
        self.assertIn("model_rows = []", refresh)
        self.assertIn("model.syncRows(model_rows)", refresh)
        self.assertNotIn("model.setRows(model_rows)", refresh)
        self.assertIn('for song_idx, entry in enumerate(singer.get("songs", [])):', refresh)
        self.assertIn('self._set_queue_row_identity(item, "singer", singer_idx, -1)', refresh)
        self.assertIn('self._set_queue_row_identity(item, "song", singer_idx, song_idx)', refresh)
        context = function_source("show_queue_context_menu")
        self.assertIn("kind = self._queue_item_kind(item)", context)
        self.assertIn("self._queue_item_singer_index(item, row)", context)
        self.assertIn("self._queue_item_song_indices(item, row)", context)
        move_up = function_source("move_up")
        move_down = function_source("move_down")
        remove = function_source("remove_selected")
        self.assertIn("current_kind = self._queue_item_kind(current_item)", move_up)
        self.assertIn("current_kind = self._queue_item_kind(current_item)", move_down)
        self.assertIn("item_kind = self._queue_item_kind(item)", remove)
        self.assertIn("_refresh_queue_song_row_identities(singer_idx)", move_up)
        self.assertIn("_refresh_queue_song_row_identities(singer_idx)", move_down)
        self.assertNotIn("takeItem", move_up)
        self.assertNotIn("insertItem", move_up)
        self.assertNotIn("takeItem", move_down)
        self.assertNotIn("insertItem", move_down)

    def test_async_remote_intake_ignores_uninitialized_qobject_stubs(self):
        helper = function_source("_qt_object_is_initialized")
        self.assertIn("self.thread()", helper)
        self.assertIn("except RuntimeError", helper)
        source = function_source("_should_async_remote_request_intake")
        self.assertIn("_qt_object_is_initialized()", source)
        accepting = function_source("_ensure_server_accepting_matches_host_intent_async")
        self.assertIn('getattr(self, "_disable_accepting_watchdog"', accepting)
        self.assertIn("except RuntimeError", accepting)
        waitlist = function_source("_sync_waitlist_state_from_server_async")
        self.assertIn('getattr(self, "_disable_waitlist_state_pull"', waitlist)
        self.assertIn("except RuntimeError", waitlist)


if __name__ == "__main__":
    unittest.main()
