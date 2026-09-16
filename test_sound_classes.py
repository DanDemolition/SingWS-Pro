import unittest
from pathlib import Path

import sound_classes as sc


class SoundClassTests(unittest.TestCase):
    def test_label_mapping_exact_and_keyword(self):
        self.assertEqual(sc.class_for_label("singing"), "active_vocal")
        self.assertEqual(sc.class_for_label("Speech"), "speech")
        self.assertEqual(sc.class_for_label("electric_guitar"), "music")
        self.assertEqual(sc.class_for_label("crowd cheering"), "applause_crowd")
        self.assertIsNone(sc.class_for_label("dog_bark"))

    def test_clear_classes(self):
        self.assertEqual(sc.score_window([("speech", 0.9), ("music", 0.1)]).result, "speech")
        self.assertEqual(sc.score_window([("applause", 0.8), ("speech", 0.2)]).result, "applause_crowd")

    def test_singing_over_backing_is_vocal_not_ambiguous(self):
        w = sc.score_window([("music", 0.85), ("singing", 0.8)])
        self.assertEqual(w.result, "active_vocal")
        self.assertEqual(w.reason, "vocal_over_music")

    def test_instrumental_only(self):
        self.assertEqual(sc.score_window([("music", 0.9), ("singing", 0.1)]).result, "music")

    def test_quiet_window_is_silence(self):
        w = sc.score_window([("dog_bark", 0.02)])
        self.assertEqual(w.result, "silence")

    def test_ambiguous_and_low_scores_are_uncertain(self):
        self.assertEqual(sc.score_window([("speech", 0.6), ("applause", 0.55)]).result, "uncertain")
        self.assertEqual(sc.score_window([("speech", 0.3)]).result, "uncertain")

    def test_unknown_labels_cannot_create_confidence(self):
        w = sc.score_window([("engine", 0.99), ("dog_bark", 0.95)])
        self.assertEqual(w.result, "uncertain")

    def test_malformed_input_is_safe(self):
        for bad in (None, [], [("speech",)], [("speech", "x")], [("speech", 5.0)], [None]):
            self.assertIn(sc.score_window(bad).result, ("uncertain", "silence"))

    def test_experimental_probe_not_loaded_by_app_or_bundle(self):
        app = Path("0.2.18.1.py").read_text(encoding="utf-8")
        spec = Path("SingWS-arm64.spec").read_text(encoding="utf-8")
        for name in ("SingWSSoundProbe", "experimental/sound_analysis"):
            self.assertFalse(name in app, name)
            self.assertFalse(name in spec, name)


if __name__ == "__main__":
    unittest.main()
