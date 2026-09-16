"""Map Apple SoundAnalysis labels to SingWS sound classes (pure, no macOS APIs).

SingWS classes: active_vocal, speech, music, applause_crowd, silence, uncertain.

Apple's built-in classifier (version1) reports several hundred labels. Exact
names must be confirmed with ``SingWSSoundProbe --list-labels`` on a Mac; the
keyword sets below match by exact label first, then by whole-word keyword, and
anything unrecognized contributes nothing (it can never create confidence).

A single window is never a decision. Later phases apply hysteresis and
minimum hold times (Prompt 8); this module only scores one window.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

CLASSES = ("active_vocal", "speech", "music", "applause_crowd", "silence")
UNCERTAIN = "uncertain"

LABEL_MAP: Mapping[str, str] = {
    "singing": "active_vocal", "choir": "active_vocal", "yodeling": "active_vocal",
    "humming": "active_vocal", "rapping": "active_vocal", "vocal_music": "active_vocal",
    "speech": "speech", "conversation": "speech", "narration": "speech",
    "shout": "speech", "yell": "speech", "whispering": "speech", "laughter": "speech",
    "music": "music", "musical_instrument": "music", "guitar": "music", "piano": "music",
    "drum": "music", "drum_kit": "music", "bass_guitar": "music", "synthesizer": "music",
    "applause": "applause_crowd", "clapping": "applause_crowd", "cheering": "applause_crowd",
    "crowd": "applause_crowd", "chatter": "applause_crowd",
    "silence": "silence",
    # Confirmed against the real macOS 27 label set (303 labels) on 2026-09-16.
    # singing_bowl is a struck instrument: without this exact entry the keyword
    # fallback sees "sing" and scores it active_vocal, which would read as a
    # singer holding a note.
    "singing_bowl": "music",
    "keyboard_musical": "music", "vibraphone": "music",
    "booing": "applause_crowd", "whistling": "applause_crowd",
    "children_shouting": "applause_crowd",
    "belly_laugh": "speech", "baby_laughter": "speech",
    # Not present in the macOS 27 set, kept for other OS versions: choir,
    # vocal_music, conversation, narration, musical_instrument.
}

KEYWORDS: Mapping[str, str] = {
    "sing": "active_vocal", "singing": "active_vocal", "choir": "active_vocal", "vocal": "active_vocal",
    "speech": "speech", "talk": "speech", "conversation": "speech",
    "music": "music", "instrument": "music", "guitar": "music", "drum": "music", "piano": "music",
    "applause": "applause_crowd", "clap": "applause_crowd", "cheer": "applause_crowd", "crowd": "applause_crowd",
}

MIN_CLASS_SCORE = 0.5
MIN_MARGIN = 0.15
SILENCE_IF_TOP_BELOW = 0.1


def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(label).lower()).strip("_")


def class_for_label(label: str) -> str | None:
    key = _norm(label)
    if key in LABEL_MAP:
        return LABEL_MAP[key]
    for word in key.split("_"):
        if word in KEYWORDS:
            return KEYWORDS[word]
    return None


@dataclass(frozen=True)
class WindowScore:
    scores: Mapping[str, float]
    result: str
    confidence: float
    reason: str


def score_window(top: Iterable[Sequence]) -> WindowScore:
    """Score one analysis window from ``[(label, confidence), ...]``."""
    scores = {name: 0.0 for name in CLASSES}
    best_raw = 0.0
    for item in top or ():
        try:
            label, conf = str(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not 0.0 <= conf <= 1.0:
            continue
        best_raw = max(best_raw, conf)
        target = class_for_label(label)
        if target is not None:
            scores[target] = max(scores[target], conf)
    if best_raw < SILENCE_IF_TOP_BELOW and scores["silence"] < MIN_CLASS_SCORE:
        scores["silence"] = max(scores["silence"], 1.0 - best_raw)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (first, s1), (_second, s2) = ranked[0], ranked[1]
    if s1 < MIN_CLASS_SCORE:
        return WindowScore(scores, UNCERTAIN, s1, "below_min_score")
    # Singing over a backing track legitimately scores high for music too.
    if first in ("active_vocal", "music") and {first, _second} == {"active_vocal", "music"}:
        if scores["active_vocal"] >= MIN_CLASS_SCORE:
            return WindowScore(scores, "active_vocal", scores["active_vocal"], "vocal_over_music")
    if s1 - s2 < MIN_MARGIN:
        return WindowScore(scores, UNCERTAIN, s1, "ambiguous_margin")
    return WindowScore(scores, first, s1, "clear_top_class")
