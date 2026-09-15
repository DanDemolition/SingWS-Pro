import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import transition_cues as tc
from transition_analysis import TRANSITION_ANALYSIS_VERSION, TransitionAnalysis, TransitionAnalysisCache


def rec(**kw):
    base = dict(path="/lib/song.cdg", mtime=1, size=2, media_kind="karaoke", duration=200.0)
    base.update(kw)
    return TransitionAnalysis(**base)


class DeriveCuesTests(unittest.TestCase):
    def test_normal_ending_with_verified_lyrics_and_dead_tail(self):
        c = tc.derive_cues(rec(audio_start=4.0, audio_end=180.0, visual_end=178.0,
                               visual_confidence=0.95), now=1.0)
        self.assertEqual(c.safe_early_end, 180.5)
        self.assertGreater(c.safe_early_end_confidence, 0.8)
        self.assertEqual(c.safe_bgm_entry, 180.0)
        self.assertEqual(c.outro, "dead_tail")
        self.assertIn("early_end_verified_audio_and_lyrics", c.reasons)
        self.assertEqual(c.cue_version, tc.CUE_ANALYZER_VERSION)
        self.assertEqual(c.computed_at, 1.0)

    def test_lyrics_after_instrumental_outlast_audio(self):
        c = tc.derive_cues(rec(audio_start=4.0, audio_end=150.0, visual_end=175.0,
                               visual_confidence=0.9))
        self.assertEqual(c.safe_early_end, 175.5)
        self.assertIn("lyrics_outlast_audio", c.reasons)
        self.assertGreaterEqual(c.safe_early_end, c.final_lyric)

    def test_silence_alone_never_proposes_early_end(self):
        c = tc.derive_cues(rec(audio_start=4.0, audio_end=120.0))
        self.assertIsNone(c.safe_early_end)
        self.assertIn("early_end_silence_only_not_enough", c.reasons)
        # Audio-only authority may still propose a BGM entry under the dead tail.
        self.assertEqual(c.safe_bgm_entry, 120.0)

    def test_low_visual_confidence_blocks_early_end(self):
        c = tc.derive_cues(rec(audio_start=4.0, audio_end=180.0, visual_end=170.0,
                               visual_confidence=0.5))
        self.assertIsNone(c.safe_early_end)
        self.assertIn("early_end_visual_unverified", c.reasons)
        self.assertIn("final_lyric_low_confidence", c.reasons)

    def test_no_meaningful_gain_near_container_end(self):
        c = tc.derive_cues(rec(audio_start=4.0, audio_end=199.4, visual_end=199.0,
                               visual_confidence=0.95))
        self.assertIsNone(c.safe_early_end)
        self.assertIsNone(c.safe_bgm_entry)
        self.assertIn("early_end_no_meaningful_gain", c.reasons)

    def test_very_short_track(self):
        c = tc.derive_cues(rec(duration=15.0, audio_start=0.5, audio_end=10.0,
                               visual_end=9.0, visual_confidence=0.99))
        self.assertIsNone(c.safe_early_end)
        self.assertIn("early_end_track_too_short", c.reasons)

    def test_absent_audio_edges(self):
        c = tc.derive_cues(rec(visual_end=170.0, visual_confidence=0.99))
        self.assertIsNone(c.safe_early_end)
        self.assertIsNone(c.safe_bgm_entry)
        self.assertEqual(c.outro, "unknown")
        self.assertIn("audio_edges_unknown", c.reasons)

    def test_corrupt_or_old_metadata_yields_nothing(self):
        self.assertIsNone(tc.derive_cues(None))
        self.assertIsNone(tc.derive_cues(rec(audio_start=10.0, audio_end=5.0)))
        self.assertIsNone(tc.derive_cues(rec(audio_end=500.0)))
        self.assertIsNone(tc.derive_cues(rec(analysis_version=TRANSITION_ANALYSIS_VERSION + 1)))
        self.assertIsNone(tc.derive_cues({"path": "x"}))

    def test_bgm_track_entry_and_no_karaoke_cues(self):
        c = tc.derive_cues(rec(media_kind="bgm", audio_start=1.2, audio_end=190.0,
                               fade_start=182.0, fade_confidence=0.9, duration=191.0))
        self.assertEqual(c.safe_bgm_entry, 1.2)
        self.assertIsNone(c.safe_early_end)
        self.assertIsNone(c.final_lyric)
        self.assertEqual(c.outro, "dead_tail" if 191.0 - 190.0 >= 1.0 else "natural_fade")

    def test_derivation_is_pure(self):
        r = rec(audio_start=4.0, audio_end=180.0, visual_end=178.0, visual_confidence=0.95)
        before = r.to_dict()
        tc.derive_cues(r)
        self.assertEqual(r.to_dict(), before)
        self.assertEqual(tc.derive_cues(r, now=5).to_dict(), tc.derive_cues(r, now=5).to_dict())

    def test_module_has_no_playback_or_qt_imports(self):
        source = Path("transition_cues.py").read_text(encoding="utf-8")
        for banned in ("PyQt6", "mpv", "bass", "open(", "subprocess"):
            self.assertNotIn(banned, source)


class InspectToolTests(unittest.TestCase):
    def test_cli_reads_cache_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "a.cdg"
            media.write_bytes(b"x" * 10)
            cache = TransitionAnalysisCache(Path(tmp) / "transition-analysis.json")
            from transition_analysis import file_signature
            m, s = file_signature(str(media))
            cache.put(TransitionAnalysis(path=str(media), mtime=m, size=s, media_kind="karaoke",
                                         duration=200.0, audio_start=4.0, audio_end=180.0,
                                         visual_end=178.0, visual_confidence=0.95))
            cache.save()
            before = cache.path.read_bytes()
            out = subprocess.run([sys.executable, "tools/inspect_transition_cues.py",
                                  "--cache", str(cache.path), "--json"],
                                 capture_output=True, text=True, check=True).stdout
            rows = json.loads(out)
            self.assertEqual(rows[0]["safe_early_end"], 180.5)
            self.assertEqual(cache.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
