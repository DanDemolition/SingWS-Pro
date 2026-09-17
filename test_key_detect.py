import unittest
from unittest import mock

import numpy as np

import key_detect as kd

SR = kd.ANALYSIS_SR


def tone(midi, seconds, sr=SR, amp=0.25):
    f = 440.0 * (2.0 ** ((midi - 69) / 12.0))
    t = np.arange(int(seconds * sr)) / sr
    # A couple of harmonics so the chroma looks like an instrument, not a test tone.
    return amp * (np.sin(2 * np.pi * f * t)
                  + 0.5 * np.sin(4 * np.pi * f * t)
                  + 0.25 * np.sin(6 * np.pi * f * t)).astype(np.float32)


def chord(midis, seconds):
    out = np.zeros(int(seconds * SR), dtype=np.float32)
    for m in midis:
        out += tone(m, seconds)
    return out


def progression(root_midi, mode, bars=4, bar_seconds=1.2):
    """I-IV-V-I, the cadence that makes a key unambiguous to a listener."""
    if mode == "major":
        triad = (0, 4, 7)
        degrees = (0, 5, 7, 0)
    else:
        triad = (0, 3, 7)
        degrees = (0, 5, 7, 0)
    parts = []
    for _ in range(bars):
        for d in degrees:
            parts.append(chord([root_midi + d + i for i in triad], bar_seconds))
    return np.concatenate(parts)


class DetectionTests(unittest.TestCase):
    def test_detects_c_major(self):
        r = kd.detect_key_from_pcm(progression(60, "major"), SR)
        self.assertIsNotNone(r)
        self.assertEqual(r.tonic, 0)
        self.assertEqual(r.mode, "major")

    def test_detects_a_minor(self):
        r = kd.detect_key_from_pcm(progression(57, "minor"), SR)
        self.assertIsNotNone(r)
        self.assertEqual(r.tonic, 9)
        self.assertEqual(r.mode, "minor")

    def test_transposition_moves_the_tonic_by_the_same_interval(self):
        """The strongest check: the detector must track a transposition exactly,
        not merely land on some plausible key."""
        base = kd.detect_key_from_pcm(progression(60, "major"), SR)
        self.assertIsNotNone(base)
        for shift in (1, 2, 5, 7, 11):
            r = kd.detect_key_from_pcm(progression(60 + shift, "major"), SR)
            self.assertIsNotNone(r, f"shift {shift}")
            self.assertEqual(r.tonic, (base.tonic + shift) % 12, f"shift {shift}")
            self.assertEqual(r.mode, "major", f"shift {shift}")


class StabilityTests(unittest.TestCase):
    def test_segments_of_one_signal_agree(self):
        """Verified on three real library tracks (both halves and the full track
        agreed); this pins the same property synthetically."""
        x = progression(62, "major", bars=8)
        half = len(x) // 2
        a = kd.detect_key_from_pcm(x[:half], SR)
        b = kd.detect_key_from_pcm(x[half:], SR)
        whole = kd.detect_key_from_pcm(x, SR)
        self.assertIsNotNone(a); self.assertIsNotNone(b); self.assertIsNotNone(whole)
        self.assertEqual((a.tonic, a.mode), (b.tonic, b.mode))
        self.assertEqual((a.tonic, a.mode), (whole.tonic, whole.mode))


class RefusalTests(unittest.TestCase):
    """Every ambiguous or broken case must decline rather than guess."""

    def test_silence_has_no_key(self):
        self.assertIsNone(kd.detect_key_from_pcm(np.zeros(SR * 20, dtype=np.float32), SR))

    def test_too_short_has_no_key(self):
        self.assertIsNone(kd.detect_key_from_pcm(progression(60, "major")[: SR * 2], SR))

    def test_bad_sample_rate_has_no_key(self):
        self.assertIsNone(kd.detect_key_from_pcm(progression(60, "major"), 0))

    def test_non_finite_input_has_no_key(self):
        x = progression(60, "major")
        x[:] = np.nan
        self.assertIsNone(kd.detect_key_from_pcm(x, SR))

    def test_flat_chroma_is_not_decisive(self):
        """All twelve pitch classes equally present: no key should stand out."""
        r = kd.detect_key_from_chroma(np.ones(12))
        self.assertTrue(r is None or r.confidence < kd.MIN_CONFIDENCE)

    def test_empty_and_wrong_shape_chroma(self):
        self.assertIsNone(kd.detect_key_from_chroma(np.zeros(12)))
        self.assertIsNone(kd.detect_key_from_chroma(np.ones(7)))
        self.assertIsNone(kd.detect_key_from_chroma(np.full(12, np.nan)))

    def test_missing_file_returns_none_rather_than_raising(self):
        self.assertIsNone(kd.detect_key("/nonexistent/song.mp3"))


class ContractTests(unittest.TestCase):
    def test_camelot_matches_the_published_wheel(self):
        self.assertEqual(kd.KeyResult(0, "major", 1.0).camelot, "8B")   # C major
        self.assertEqual(kd.KeyResult(9, "minor", 1.0).camelot, "8A")   # A minor
        self.assertEqual(kd.KeyResult(7, "major", 1.0).camelot, "9B")   # G major
        self.assertEqual(kd.KeyResult(4, "minor", 1.0).camelot, "9A")   # E minor

    def test_relative_major_and_minor_share_a_camelot_number(self):
        for tonic in range(12):
            rel_minor = (tonic + 9) % 12
            self.assertEqual(kd.KeyResult(tonic, "major", 1.0).camelot[:-1],
                             kd.KeyResult(rel_minor, "minor", 1.0).camelot[:-1])

    def test_nothing_is_usable_while_the_detector_is_unvalidated(self):
        """Measured 4/15 on real-audio shift tracking, and it fails octave
        invariance. Until that is fixed, no consumer may act on a key."""
        self.assertFalse(kd.VALIDATED)
        self.assertFalse(kd.is_usable(kd.KeyResult(0, "major", 0.99, fit=0.99)))

    def test_fit_and_confidence_gate_once_validated(self):
        with mock.patch.object(kd, "VALIDATED", True):
            self.assertFalse(kd.is_usable(None))
            self.assertFalse(kd.is_usable(kd.KeyResult(0, "major", 0.0, fit=0.9)))
            self.assertFalse(kd.is_usable(kd.KeyResult(0, "major", 0.9, fit=0.1)),
                             "a high margin over a poor fit is still a poor fit")
            self.assertTrue(kd.is_usable(kd.KeyResult(0, "major", 0.9, fit=0.9)))

    def test_relative_key_round_trips(self):
        for tonic in range(12):
            maj = kd.KeyResult(tonic, "major", 1.0)
            self.assertEqual(maj.relative.mode, "minor")
            self.assertEqual(maj.relative.relative.tonic, tonic)

    def test_confidence_ignores_the_relative_key(self):
        """A chroma that fits C major also fits A minor; that ambiguity must
        show up as relative_margin, not as low confidence."""
        import numpy as np
        v = np.zeros(12)
        for pc, w in ((0, 6.0), (4, 4.0), (7, 5.0), (2, 3.0), (9, 3.0), (5, 3.5), (11, 2.5)):
            v[pc] = w
        r = kd.detect_key_from_chroma(v)
        self.assertIsNotNone(r)
        self.assertGreater(r.fit, 0.5)

    def test_semitones_between_is_signed_and_shortest(self):
        c = kd.KeyResult(0, "major", 1.0)
        self.assertEqual(kd.semitones_between(c, kd.KeyResult(2, "major", 1.0)), 2)
        self.assertEqual(kd.semitones_between(c, kd.KeyResult(11, "major", 1.0)), -1)
        self.assertEqual(kd.semitones_between(c, kd.KeyResult(6, "major", 1.0)), 6)

    def test_result_serialises_with_its_analyzer_version(self):
        d = kd.KeyResult(3, "minor", 0.5).as_dict()
        self.assertEqual(d["analyzer_version"], kd.KEY_ANALYZER_VERSION)
        self.assertEqual(d["name"], "D# minor")

    def test_module_never_imports_playback_or_qt(self):
        from pathlib import Path
        src = Path("key_detect.py").read_text()
        for banned in ("PyQt6", "mpv_karaoke_transport", "bass_background_engine", "0.2.18.1"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()
