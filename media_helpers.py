"""Small media utilities shared by playback and analysis jobs."""

from __future__ import annotations

import logging

NS_PER_SECOND = 1_000_000_000

# A missing dependency and an unreadable file are different failures and must not
# look the same. On 2026-09-16 mutagen was absent from a test environment, this
# function returned 0.0 for *every* file, and detect_trailing_silence() therefore
# reported "no trailing silence" for the whole library -- silently, because 0.0
# is its deliberate fail-safe. It read as an unfixable test failure for months.
# The dependency is reported once, loudly; per-file failures stay quiet.
_MUTAGEN = None          # None = not yet probed, False = unavailable, else callable
_MUTAGEN_WARNED = False


def _mutagen_file():
    global _MUTAGEN, _MUTAGEN_WARNED
    if _MUTAGEN is None:
        try:
            from mutagen import File as MutagenFile
        except Exception as exc:
            _MUTAGEN = False
            if not _MUTAGEN_WARNED:
                _MUTAGEN_WARNED = True
                logging.error(
                    "[DEPENDENCY] mutagen is unavailable (%s). Track durations will "
                    "read as 0.0, which silently disables trailing-silence detection "
                    "and end-of-song trimming. Install it: "
                    "pip install -c constraints-macos15.txt mutagen", exc)
        else:
            _MUTAGEN = MutagenFile
    return _MUTAGEN


def dependency_status() -> dict:
    """Which optional dependencies resolved. For diagnostics and tests."""
    return {"mutagen": bool(_mutagen_file())}


def _normalized_audio_device_name(value: str) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def match_qt_audio_device(devices, wanted_name: str):
    wanted = _normalized_audio_device_name(wanted_name)
    if not wanted:
        return None
    candidates = []
    for device in list(devices or []):
        try:
            name = str(device.description() or "")
        except Exception:
            continue
        key = _normalized_audio_device_name(name)
        if not key:
            continue
        if key == wanted:
            return device
        if wanted in key or key in wanted:
            candidates.append(device)
    return candidates[0] if len(candidates) == 1 else None


def probe_duration_seconds(path: str) -> float:
    """Duration from metadata, or 0.0 when it cannot be determined.

    0.0 is a legitimate answer for an unreadable or tagless file. It is NOT a
    legitimate answer for "mutagen is missing" -- that case is logged once by
    _mutagen_file() so a deployment fault cannot hide behind a normal-looking
    zero. Callers still treat 0.0 as "unknown" either way.
    """
    reader = _mutagen_file()
    if not reader:
        return 0.0
    try:
        media = reader(str(path))
        return max(0.0, float(getattr(getattr(media, "info", None), "length", 0.0) or 0.0))
    except Exception:
        return 0.0
