"""Audience layout and periodic queue-spotlight regressions."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from PyQt6.QtTest import QTest
import test_rotation_render_thread as base

mod = base.mod


def setUpModule():
    base.setUpModule()


class RotationTvDesignTests(unittest.TestCase):
    def make_view(self):
        owner = mod.QWidget()
        owner.settings = {}
        owner.karaoke_playing = True
        owner._current_karaoke_mode = 'cdg'
        view = mod.RotationView(owner)
        view._backdrop_animation_timer.stop()
        self.addCleanup(owner.close)
        self.addCleanup(view.hide)
        return owner, view

    def test_five_rows_fit_without_spurious_overflow(self):
        _, view = self.make_view()
        view.resize(1280, 720)
        view.show()
        queue = [{'name': name, 'songs': [{'song_info': '/song.cdg'}]} for name in ('Sam', 'Jordan', 'Taylor', 'Chris', 'Morgan')]
        view.update_rotation(queue, [], 'Alex')
        QTest.qWait(600)
        self.assertFalse(view.rotation_rail._root.property('overflow'))
        self.assertGreaterEqual(view.ROTATION_QR_SIZE, 220)
        self.assertEqual(view.clock_label.text().count('\n'), 0)
        self.assertGreater(view.sidebar.x(), view.rotation_rail.x())

    def test_decorative_rotation_effects_pause_during_karaoke(self):
        owner, view = self.make_view()
        view.show()
        QTest.qWait(20)
        view.set_effects_enabled(True)
        view._tick_animated_backdrop()
        # 0.4.7.7-rc1 slowed the karaoke-time state check to 1 s so the optional
        # backdrop never repaints while lyrics move.
        self.assertEqual(view._backdrop_animation_timer.interval(), 1000)
        self.assertFalse(view.rotation_rail._root.property('effectsEnabled'))
        self.assertFalse(view.now_singing_surface._root.property('effectsEnabled'))
        owner.karaoke_playing = False
        view._tick_animated_backdrop()
        self.assertEqual(view._backdrop_animation_timer.interval(), 125)
        self.assertTrue(view.rotation_rail._root.property('effectsEnabled'))
        self.assertTrue(view.now_singing_surface._root.property('effectsEnabled'))

    def test_current_singer_uses_identity_and_preserves_same_named_other_singer(self):
        owner, view = self.make_view()
        owner._current_karaoke_singer_id = 'active'
        queue = [{'singer_id': identity, 'name': 'Alex', 'songs': [{'song_info': '/song.cdg'}]} for identity in ('active', 'other')]
        view.update_rotation(queue, [], 'Alex')
        self.assertEqual(len(json.loads(view.rotation_rail._root.property('itemsJson'))), 1)
        self.assertEqual(len(queue), 2)

    def test_next_up_spotlight_runs_once_per_thirty_second_boundary(self):
        _, view = self.make_view()
        view.show()
        QTest.qWait(20)
        view.rotation_rail.set_effects_enabled(True)
        view.rotation_rail.set_items([{'number': '1', 'singer': 'Sam'}], force=True)
        view._next_up_spotlight_started = None
        view._next_up_spotlight_cycle = -1
        with patch.object(mod.time, 'monotonic', return_value=100):
            view._refresh_next_up_spotlight(True)
        first = int(view.rotation_rail._root.property('spotlightSerial'))
        with patch.object(mod.time, 'monotonic', return_value=109):
            view._refresh_next_up_spotlight(True)
        self.assertEqual(int(view.rotation_rail._root.property('spotlightSerial')), first)
        with patch.object(mod.time, 'monotonic', return_value=130):
            view._refresh_next_up_spotlight(True)
        self.assertEqual(int(view.rotation_rail._root.property('spotlightSerial')), first + 1)
        with patch.object(mod.time, 'monotonic', return_value=139):
            view._refresh_next_up_spotlight(True)
        self.assertEqual(int(view.rotation_rail._root.property('spotlightSerial')), first + 1)
        with patch.object(mod.time, 'monotonic', return_value=140):
            view._refresh_next_up_spotlight(False)
        self.assertIsNone(view._next_up_spotlight_started)

    def test_announcement_mode_is_opt_in_and_main_ticker_keeps_countdown(self):
        ticker = mod.RotationAnnouncementTicker()
        self.addCleanup(ticker.close)
        ticker.set_announcement(True, 'Specials')
        if hasattr(ticker._backend, '_root'):
            self.assertTrue(ticker._backend._root.property('announcementMode'))
        else:
            self.assertTrue(ticker._backend._view._announcement_mode)
        main = mod.RenderThreadTicker(lambda: ([], 'Main ticker'), get_time_left_callback=lambda: '03:20')
        self.addCleanup(main.close)
        self.assertFalse(main._root.property('announcementMode'))
        main.update_right_text()
        self.assertEqual(main._root.property('rightText'), '03:20')

    def test_rotation_ticker_reasserts_its_render_surface(self):
        ticker = mod.RotationAnnouncementTicker()
        self.addCleanup(ticker.close)
        ticker.set_announcement(True, 'Specials')
        ticker.show()
        QTest.qWait(20)
        with patch.object(ticker._backend, 'sync_surface_geometry') as sync, \
             patch.object(ticker._backend, 'force_refresh_now') as refresh:
            self.assertTrue(ticker.reassert_surface())
            sync.assert_called_once_with()
            refresh.assert_not_called()

    def test_spotlight_does_not_reflow_queue(self):
        _, view = self.make_view()
        view.resize(1280, 720); view.show()
        with patch.object(mod.time, 'monotonic', return_value=100):
            view._refresh_next_up_spotlight(True)
        QTest.qWait(30)
        before = view.rotation_rail.geometry()
        with patch.object(mod.time, 'monotonic', return_value=130):
            view._refresh_next_up_spotlight(True)
        QTest.qWait(30)
        self.assertEqual(view.rotation_rail.geometry(), before)

    def test_fallback_loop_duplicates_only_the_displayed_names(self):
        with patch.object(mod, '_rotation_quick_surfaces_supported', return_value=False):
            _, view = self.make_view()
        view.resize(1280, 720); view.show()
        rows = [{'name': f'Singer {i}', 'songs': [{'song_info': '/song.cdg', 'title': 'Hidden song metadata'}]} for i in range(20)]
        view.update_rotation(rows, [], 'Alex')
        QTest.qWait(30)
        view.check_autoscroll()
        count = len(rows)
        self.assertEqual(view.duplicate_count, count)
        for i in range(count):
            self.assertEqual(view.list_widget.item(i).text(), view.list_widget.item(i + count).text())
            self.assertNotIn('Hidden song metadata', view.list_widget.item(i).text())

    def test_resizing_preserves_custom_qr_caption(self):
        _, view = self.make_view()
        qr = mod.QPixmap(250, 250); qr.fill(mod.QColor('white'))
        view.set_request_qr(qr, 'SING WITH US')
        view.resize(1280, 720); view.show()
        QTest.qWait(30)
        view.resize(1920, 1080)
        QTest.qWait(30)
        self.assertEqual(view.qr_caption_label.text(), 'SING WITH US')

    def test_no_dedicated_lyrics_preview_is_created(self):
        _, view = self.make_view()
        self.assertFalse(hasattr(view, 'live_preview'))
        self.assertIn('function spotlightNext()', mod.QML_ROTATION_RAIL_SOURCE)
