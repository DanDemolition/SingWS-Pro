"""A test profile must not read or save the host's library/playlist state."""
import contextlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

import phrase_markers
import song_index


class ProfilePathTests(unittest.TestCase):
    def test_database_and_marker_paths_follow_test_profile(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"SINGWS_HOME": td}):
            self.assertEqual(song_index.db_path(), Path(td) / "singws.db")
            self.assertEqual(song_index.tracks_json_path(), Path(td) / "tracks.json")
            self.assertEqual(phrase_markers.db_path(), Path(td) / "phrase_markers.db")

    def test_regular_profile_keeps_original_location(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(Path, "home", return_value=Path(td)):
            with mock.patch.dict(os.environ, {"SINGWS_HOME": ""}):
                # SingWS Pro keeps its own data root beside 1.x (~/SingWS).
                self.assertEqual(song_index.db_path(), Path(td) / "SingWSPro" / "singws.db")


class BackgroundProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_recent_regressions import load_main_module
        cls.module = load_main_module()
        cls.qt = cls.module.QApplication.instance() or cls.module.QApplication([])

    def test_playlist_load_edit_save_and_analysis_stay_in_test_profile(self):
        module = self.module
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = root / "SingWS"
            live.mkdir()
            test = root / "test"
            test.mkdir()
            live_track, test_track = root / "live.mp3", root / "test.mp3"
            live_track.touch()
            test_track.touch()
            live_playlist = live / "bg_playlist.json"
            live_playlist.write_text(json.dumps([str(live_track)]))
            original = live_playlist.read_bytes()
            test_playlist = test / "bg_playlist.json"
            test_playlist.write_text(json.dumps([str(test_track)]))
            bg = SimpleNamespace(playlist=[], play=lambda: None)
            host = SimpleNamespace(bg_music=bg, settings={})
            with mock.patch.object(module, "APP_USER_DIR", test), \
                 mock.patch.object(Path, "home", return_value=root):
                module.KaraokeApp._bootstrap_bg_playlist_on_startup(host)
                self.assertEqual(bg.playlist, [str(test_track)])
                bg.playlist = []
                items = module.KaraokeApp._bgm_analysis_items(host)
                self.assertEqual([row[1] for row in items], [str(test_track)])
                # Construct the real manager, omitting unrelated UI wiring.
                with contextlib.ExitStack() as patches:
                    for method in ("setup_ui", "_apply_main_window_look", "_match_main_window_geometry",
                                   "load_data", "setup_connections", "_init_file_browser"):
                        patches.enter_context(mock.patch.object(module.BackgroundMusicManager, method))
                    manager = module.BackgroundMusicManager(bg)
                try:
                    manager.current_playlist = [{"path": str(test_track), "title": "Test"}]
                    manager.save_current_playlist()
                    self.assertEqual(json.loads(test_playlist.read_text()), manager.current_playlist)
                    self.assertEqual(live_playlist.read_bytes(), original)
                finally:
                    manager.close()


if __name__ == "__main__":
    unittest.main()
