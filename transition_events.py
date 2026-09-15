"""Phase 0 Intelligent Audio instrumentation: passive transition event log.

Records what the EXISTING transition system does (karaoke start/end, early-end
decisions, BGM fades, manual stop/seek) as compact JSONL so later phases can be
compared against real shows. It never changes playback.

Safety contract (see docs/intelligent_audio/ARCHITECTURE.md):
- ``record()`` is O(1): a monotonic timestamp and a bounded deque append under a
  short lock. No I/O, no formatting, no allocation beyond the small event.
- One daemon writer thread serializes and appends to disk.
- Overflow policy: the ring keeps the newest ``capacity`` events; the oldest are
  dropped and counted, and the drop count is written with the next batch.
- Disabled by default (setting ``ia_instrumentation_enabled``); when disabled
  ``record()`` returns immediately.
- No raw audio and no singer names are ever recorded. Tracks are identified by a
  short hash of the path.
"""
from __future__ import annotations

import collections
import datetime
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1

EVENT_KINDS = frozenset({
    "karaoke_start",         # transport started a song
    "karaoke_eos",           # decoder reported end of stream
    "media_end",             # _handle_media_end_safe entered (data.trigger)
    "early_end_trim",        # verified silent tail ended the song early
    "eos_fallback",          # near-end/level fallback forced end
    "stall_fallback",        # stalled-position fallback forced end
    "bgm_prestart",          # duration-only BGM pre-start during karaoke tail
    "bgm_prefire_verified",  # BGM faded under a verified dead audio tail
    "bgm_fade_in",           # BackgroundMusicPlayer.fade_in requested
    "manual_stop",
    "manual_seek",
    "gui_stall",
    "recorder_dropped",      # synthetic: overflow count
})

_ALLOWED_VALUE_TYPES = (str, int, float, bool, type(None))


def track_id(path: str | None) -> str | None:
    """Short, non-reversible identifier for a media path (no file I/O)."""
    if not path:
        return None
    return hashlib.sha1(str(path).encode("utf-8", "surrogatepass")).hexdigest()[:12]


@dataclass(frozen=True)
class PlaybackEvent:
    t_mono: float
    t_wall: float
    kind: str
    generation: int | None = None
    track: str | None = None
    media: str | None = None
    playhead_s: float | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        payload = {
            "v": SCHEMA_VERSION,
            "t_mono": round(self.t_mono, 4),
            "t_wall": round(self.t_wall, 3),
            "kind": self.kind,
        }
        for key in ("generation", "track", "media", "playhead_s"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = round(value, 3) if isinstance(value, float) else value
        clean = {
            str(k): (round(v, 4) if isinstance(v, float) else v)
            for k, v in dict(self.data or {}).items()
            if isinstance(v, _ALLOWED_VALUE_TYPES)
        }
        if clean:
            payload["data"] = clean
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


class EventRecorder:
    def __init__(self, log_dir: Path | str | None = None, *, capacity: int = 2048,
                 flush_interval_s: float = 1.0, clock=time.monotonic, wall=time.time):
        self._log_dir = Path(log_dir) if log_dir else None
        self._capacity = max(8, int(capacity))
        self._ring: collections.deque[PlaybackEvent] = collections.deque(maxlen=self._capacity)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._flush_interval_s = float(flush_interval_s)
        self._clock = clock
        self._wall = wall
        self._enabled = False
        self._dropped = 0
        self._written = 0
        self._thread: threading.Thread | None = None

    # ---- configuration -------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    def configure(self, *, enabled: bool, log_dir: Path | str | None = None) -> None:
        if log_dir is not None:
            self._log_dir = Path(log_dir)
        self._enabled = bool(enabled) and self._log_dir is not None
        if self._enabled:
            self._ensure_thread()

    # ---- hot path --------------------------------------------------------
    def record(self, kind: str, *, generation=None, track=None, media=None,
               playhead_s=None, **data) -> bool:
        if not self._enabled:
            return False
        if kind not in EVENT_KINDS:
            return False
        event = PlaybackEvent(self._clock(), self._wall(), kind, generation, track,
                              media, playhead_s, data)
        with self._lock:
            if len(self._ring) == self._capacity:
                self._dropped += 1
            self._ring.append(event)
        return True

    # ---- writer ------------------------------------------------------------
    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ia-transition-events", daemon=True)
        self._thread.start()

    def _drain(self) -> tuple[list[PlaybackEvent], int]:
        with self._lock:
            batch = list(self._ring)
            self._ring.clear()
            dropped, self._dropped = self._dropped, 0
        return batch, dropped

    def _path(self) -> Path:
        day = datetime.date.fromtimestamp(self._wall()).strftime("%Y%m%d")
        return self._log_dir / f"transition_events_{day}.jsonl"

    def flush(self) -> int:
        batch, dropped = self._drain()
        if not batch and not dropped:
            return 0
        lines = [e.to_json() for e in batch]
        if dropped:
            lines.append(PlaybackEvent(self._clock(), self._wall(), "recorder_dropped",
                                       data={"count": dropped}).to_json())
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            with open(self._path(), "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            self._written += len(lines)
        except OSError:
            pass  # diagnostics must never break the app
        return len(lines)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._flush_interval_s)
            self._wake.clear()
            self.flush()
        self.flush()

    def close(self, timeout: float = 2.0) -> None:
        self._enabled = False
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None

    @property
    def stats(self) -> dict:
        with self._lock:
            return {"pending": len(self._ring), "dropped": self._dropped,
                    "written": self._written, "capacity": self._capacity}


_RECORDER = EventRecorder()


def recorder() -> EventRecorder:
    return _RECORDER


def record(kind: str, **kwargs) -> bool:
    """Module-level convenience; never raises."""
    try:
        return _RECORDER.record(kind, **kwargs)
    except Exception:
        return False
