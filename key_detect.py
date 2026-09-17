"""Musical key detection (MS1). Pure numpy/scipy, no Qt, no playback.

Reuses the chroma primitives already in ``phrase_detect`` rather than growing a
second spectral stack: ``_stft_mag`` and ``_chroma`` are the same code the
transition analyser uses. No new dependency.

Method: average the chroma over the track, then correlate against the twelve
rotations of the Krumhansl-Kessler major and minor profiles. The best of the 24
candidates wins; confidence comes from how far ahead it is of the runner-up, so
a track that fits two keys equally well reports low confidence rather than a
coin-flip answer.

Failure is quiet and explicit: anything ambiguous, silent, too short, or
undecodable returns ``None``. Callers must treat "no key" as normal -- a large
fraction of a karaoke library is modal, key-changing, or simply noisy, and a
confidently wrong key is worse than no key for every consumer (harmonic
transitions, pitch suggestions, mixer presets).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

KEY_ANALYZER_VERSION = 1

# MEASURED FAILURE, 2026-09-16. Do not let anything act on a detected key.
#
# Validated with tools/validate_key_detect.py, which pitch-shifts real library
# tracks by a known amount and requires the detected tonic to move by exactly
# that much (the shift is the ground truth, so no known-key song list is needed).
# Results on real audio:
#
#   shift tracking      4/15
#   octave invariance   FAILS -- +/-12 semitones preserves every pitch class,
#                       yet G major was detected as E minor and C major
#
# The synthetic tests in test_key_detect.py pass, including transposition
# invariance, so the method works on clean tones and falls apart on real mixes.
# The cause is that the chroma sums raw magnitude across the spectrum, so
# spectral tilt -- not pitch content -- moves the result. A band-limited,
# log-compressed, whitened chroma was tried: octave invariance improved to 5/6
# but shift tracking dropped to 1/15, so it is not a quick fix.
#
# This module is kept as a foundation and a harness, not as a working detector.
# MS2 (harmonic transitions), MS4 (pitch suggestions) and MS5 (mixer presets)
# must not consume it until VALIDATED is True.
VALIDATED = False

PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Krumhansl-Kessler probe-tone profiles.
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                   2.52, 5.19, 2.39, 3.66, 2.29, 2.88], dtype=np.float64)
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                   2.54, 4.75, 3.98, 2.69, 3.34, 3.17], dtype=np.float64)

# Analysis is done on a decoded mono signal; 11025 Hz carries every pitch class
# we care about (the chroma folds harmonics anyway) at a quarter of the cost.
ANALYSIS_SR = 11025
N_FFT = 4096
HOP = 2048
MIN_SECONDS = 8.0          # shorter than this is not a song

# PROVISIONAL AND UNCALIBRATED. Measured on three real library tracks on
# 2026-09-16: fit 0.58-0.80, confidence 0.12-0.15. Three tracks is a sample, not
# a calibration, and no ground-truth key was available (none of the archives
# carried a TKEY tag), so these thresholds say "plausible", not "correct".
# `fit` is the better-behaved signal -- it measures whether any key fits at all
# -- so usability requires both. Calibrate against known-key tracks before any
# consumer acts on a key. See docs/2.0/plan.md MS1.
MIN_CONFIDENCE = 0.10
MIN_FIT = 0.45


@dataclass(frozen=True)
class KeyResult:
    tonic: int             # 0 = C .. 11 = B
    mode: str              # "major" | "minor"
    confidence: float      # 0..1, margin over the best NON-relative competitor
    fit: float = 0.0       # raw correlation of the winner: how tonal the track is
    relative_margin: float = 0.0  # how far ahead of its own relative major/minor
    analyzer_version: int = KEY_ANALYZER_VERSION

    @property
    def name(self) -> str:
        return f"{PITCH_NAMES[self.tonic]} {self.mode}"

    @property
    def camelot(self) -> str:
        """Camelot wheel position, the notation DJs use for harmonic mixing.
        Neighbouring numbers are compatible keys."""
        major_order = (8, 3, 10, 5, 12, 7, 2, 9, 4, 11, 6, 1)   # C, C#, D, ...
        minor_order = (5, 12, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10)
        if self.mode == "major":
            return f"{major_order[self.tonic]}B"
        return f"{minor_order[self.tonic]}A"

    @property
    def relative(self) -> "KeyResult":
        """The relative major/minor: same pitch classes, different tonic."""
        if self.mode == "major":
            return KeyResult((self.tonic + 9) % 12, "minor", self.confidence)
        return KeyResult((self.tonic + 3) % 12, "major", self.confidence)

    def as_dict(self) -> dict:
        return {"tonic": self.tonic, "mode": self.mode,
                "confidence": round(float(self.confidence), 4),
                "fit": round(float(self.fit), 4),
                "relative_margin": round(float(self.relative_margin), 4),
                "name": self.name, "camelot": self.camelot,
                "analyzer_version": self.analyzer_version}


def _correlate(chroma: np.ndarray, profile: np.ndarray) -> np.ndarray:
    """Pearson correlation of the chroma against all 12 rotations."""
    c = chroma - chroma.mean()
    cn = float(np.sqrt((c * c).sum()))
    if cn <= 0.0:
        return np.full(12, -1.0)
    out = np.empty(12, dtype=np.float64)
    for k in range(12):
        p = np.roll(profile, k)
        p = p - p.mean()
        pn = float(np.sqrt((p * p).sum()))
        out[k] = float((c * p).sum() / (cn * pn)) if pn > 0.0 else -1.0
    return out


def detect_key_from_chroma(chroma_mean: np.ndarray) -> Optional[KeyResult]:
    """Score one averaged 12-bin chroma vector. None when it is not decisive."""
    v = np.asarray(chroma_mean, dtype=np.float64).ravel()
    if v.size != 12 or not np.all(np.isfinite(v)) or v.sum() <= 0.0:
        return None

    major = _correlate(v, _MAJOR)
    minor = _correlate(v, _MINOR)
    scores = np.concatenate([major, minor])
    if not np.all(np.isfinite(scores)):
        return None

    best = int(np.argmax(scores))
    top = float(scores[best])
    if top <= 0.0:
        return None

    tonic = best % 12
    mode = "major" if best < 12 else "minor"

    # The runner-up is almost always the RELATIVE key, which shares all seven
    # pitch classes -- chroma cannot separate those, and measured on real tracks
    # it dominated the margin and made every confidence useless (G major 0.801
    # vs E minor 0.701; B minor 0.749 vs D major 0.739). Score the winner
    # against the best genuinely different key instead, and report the relative
    # margin separately so callers can see that specific ambiguity.
    rel_index = ((tonic + 9) % 12) + 12 if mode == "major" else (tonic + 3) % 12
    mask = np.ones(24, dtype=bool)
    mask[best] = False
    mask[rel_index] = False
    rival = float(np.max(scores[mask]))
    relative_margin = max(0.0, min(1.0, (top - float(scores[rel_index])) / max(top, 1e-9)))

    confidence = max(0.0, min(1.0, (top - rival) / max(top, 1e-9)))
    return KeyResult(tonic=tonic, mode=mode, confidence=confidence,
                     fit=top, relative_margin=relative_margin)


def detect_key_from_pcm(pcm: np.ndarray, sr: int) -> Optional[KeyResult]:
    """Detect from decoded mono PCM. None for silence or anything too short."""
    from phrase_detect import _chroma, _stft_mag

    x = np.asarray(pcm, dtype=np.float32).ravel()
    if sr <= 0 or x.size < int(MIN_SECONDS * sr):
        return None
    if not np.any(np.isfinite(x)) or float(np.max(np.abs(x))) <= 1e-6:
        return None

    mag = _stft_mag(x, N_FFT, HOP)
    if mag.shape[0] == 0:
        return None
    chroma = _chroma(mag, int(sr), N_FFT)
    if chroma.shape[0] == 0:
        return None

    # Per-frame normalisation keeps a loud chorus from outvoting a quiet verse.
    norms = np.linalg.norm(chroma, axis=1, keepdims=True)
    norms[norms <= 0.0] = 1.0
    return detect_key_from_chroma((chroma / norms).mean(axis=0))


def detect_key(path: str, *, max_seconds: float = 240.0) -> Optional[KeyResult]:
    """Decode and detect. Returns None on any decode failure, by design: a
    missing key must never stop a scan or a show."""
    try:
        from phrase_detect import decode_pcm_mono
        pcm = decode_pcm_mono(path, sr=ANALYSIS_SR, max_seconds=float(max_seconds))
    except Exception:
        return None
    if pcm is None:
        return None
    return detect_key_from_pcm(pcm, ANALYSIS_SR)


def is_usable(result: Optional[KeyResult], min_confidence: float = MIN_CONFIDENCE,
              min_fit: float = MIN_FIT) -> bool:
    """Whether a result is worth acting on: a key has to fit the track at all
    (`fit`) *and* beat the nearest genuinely different key (`confidence`).
    Consumers should gate on this, never on `result is not None`."""
    if not VALIDATED:
        # The detector does not track pitch shifts on real audio (see the note
        # by KEY_ANALYZER_VERSION). Refusing here means a consumer written
        # before the fix lands cannot silently act on a wrong key.
        return False
    return (result is not None
            and result.fit >= float(min_fit)
            and result.confidence >= float(min_confidence))


def semitones_between(a: KeyResult, b: KeyResult) -> int:
    """Signed semitone distance from a's tonic to b's, in -5..6."""
    d = (b.tonic - a.tonic) % 12
    return d - 12 if d > 6 else d
