"""Phase 1 Intelligent Audio: deterministic, versioned transition cue candidates.

Derived purely from an existing cached ``TransitionAnalysis`` record, so:
- no decoding, file reads, or rescans (lazy by construction: cues are
  recomputed from whatever record exists; improving this module never
  invalidates ``transition-analysis.json``);
- no Qt, no playback imports, and nothing here is ever executed by the player.
  Cues are **proposals** for diagnostics and the later observer (Phase 2).

Evidence ranking: CDG/MP4 visual end (lyrics) outranks audio silence. Silence
alone never produces an early-end candidate.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from transition_analysis import TRANSITION_ANALYSIS_VERSION, TransitionAnalysis

CUE_ANALYZER_VERSION = 1

# Conservative policy constants (documented in docs/intelligent_audio/HANDOFF.md).
MIN_TRACK_SECONDS = 20.0          # shorter tracks never get early-end candidates
MIN_VISUAL_CONFIDENCE = 0.85      # matches calculate_effective_karaoke_end
EARLY_END_MARGIN_S = 0.5          # after max(audio_end, visual_end)
MIN_EARLY_END_GAIN_S = 1.0        # candidate must save at least this much
BGM_ENTRY_MIN_TAIL_S = 1.0        # dead audio tail needed before proposing BGM entry
MIN_AUDIO_EDGE_S = 0.3            # matches envelope confirmation window


def _num(value) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


@dataclass(frozen=True)
class TrackCues:
    cue_version: int
    analysis_version: int
    computed_at: float
    media_kind: str
    duration: float
    audible_start: Optional[float]
    audible_end: Optional[float]
    final_lyric: Optional[float]           # last visible CDG/MP4 lyric change
    final_lyric_confidence: float
    safe_bgm_entry: Optional[float]         # BGM may begin fading in from here
    safe_bgm_entry_confidence: float
    safe_early_end: Optional[float]         # karaoke may end here instead of EOS
    safe_early_end_confidence: float
    outro: str                              # dead_tail | natural_fade | hard_ending | unknown
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def _outro(record: TransitionAnalysis, duration: float, audio_end: Optional[float]) -> str:
    if audio_end is None:
        return "unknown"
    if duration - audio_end >= BGM_ENTRY_MIN_TAIL_S:
        return "dead_tail"
    if _num(record.fade_start) is not None and (_num(record.fade_confidence) or 0.0) >= 0.8:
        return "natural_fade"
    if (_num(record.fade_confidence) or 0.0) <= 0.2 and str(record.media_kind).lower() == "bgm":
        return "hard_ending"
    return "unknown"


def derive_cues(record: Optional[TransitionAnalysis], *, now: Optional[float] = None) -> Optional[TrackCues]:
    """Return conservative cue candidates, or None when no usable record exists."""
    if record is None or not isinstance(record, TransitionAnalysis):
        return None
    if record.analysis_version != TRANSITION_ANALYSIS_VERSION or not record.is_valid():
        return None
    reasons: list[str] = []
    duration = _num(record.duration) or 0.0
    kind = str(record.media_kind or "").lower()
    audio_start = _num(record.audio_start)
    audio_end = _num(record.audio_end)
    visual_end = _num(record.visual_end)
    visual_conf = max(0.0, min(1.0, _num(record.visual_confidence) or 0.0))

    if audio_start is None or audio_end is None:
        reasons.append("audio_edges_unknown")
    elif audio_end - audio_start < MIN_AUDIO_EDGE_S:
        reasons.append("audio_edges_implausible")
        audio_start = audio_end = None

    final_lyric = None
    final_lyric_conf = 0.0
    if kind != "bgm":
        if visual_end is None:
            reasons.append("final_lyric_unknown")
        else:
            final_lyric, final_lyric_conf = visual_end, visual_conf
            if visual_conf < MIN_VISUAL_CONFIDENCE:
                reasons.append("final_lyric_low_confidence")

    outro = _outro(record, duration, audio_end)

    # --- safe BGM entry -------------------------------------------------------
    safe_bgm_entry = None
    bgm_conf = 0.0
    if kind == "bgm":
        if audio_start is not None:
            safe_bgm_entry, bgm_conf = audio_start, 0.9
            reasons.append("bgm_entry_at_audible_start")
    elif audio_end is not None and duration - audio_end >= BGM_ENTRY_MIN_TAIL_S:
        # Audio-only authority: BGM may rise under a verified dead audio tail
        # while visuals continue (mirrors _prefire_bgm_at_verified_audio_end).
        safe_bgm_entry = audio_end
        bgm_conf = 0.8
        reasons.append("bgm_entry_after_verified_audio_end")
    elif audio_end is not None:
        reasons.append("bgm_entry_no_dead_tail")

    # --- safe early end (karaoke only; lyrics outrank silence) ----------------
    safe_early_end = None
    early_conf = 0.0
    if kind != "bgm":
        if duration < MIN_TRACK_SECONDS:
            reasons.append("early_end_track_too_short")
        elif audio_end is None:
            reasons.append("early_end_audio_unverified")
        elif final_lyric is None:
            reasons.append("early_end_silence_only_not_enough")
        elif final_lyric_conf < MIN_VISUAL_CONFIDENCE:
            reasons.append("early_end_visual_unverified")
        else:
            candidate = max(audio_end, final_lyric) + EARLY_END_MARGIN_S
            if duration - candidate < MIN_EARLY_END_GAIN_S:
                reasons.append("early_end_no_meaningful_gain")
            else:
                safe_early_end = candidate
                early_conf = round(min(final_lyric_conf, 0.95), 3)
                reasons.append("early_end_verified_audio_and_lyrics")
                if final_lyric > audio_end:
                    reasons.append("lyrics_outlast_audio")

    return TrackCues(
        cue_version=CUE_ANALYZER_VERSION,
        analysis_version=int(record.analysis_version),
        computed_at=float(time.time() if now is None else now),
        media_kind=kind,
        duration=duration,
        audible_start=audio_start,
        audible_end=audio_end,
        final_lyric=final_lyric,
        final_lyric_confidence=final_lyric_conf,
        safe_bgm_entry=safe_bgm_entry,
        safe_bgm_entry_confidence=bgm_conf,
        safe_early_end=safe_early_end,
        safe_early_end_confidence=early_conf,
        outro=outro,
        reasons=tuple(reasons),
    )
