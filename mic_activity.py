"""Per-channel live-mic activity from level reports (Prompt 7). Pure, no audio.

Input: RMS/peak dB every ~100 ms per role. Output per role: a state with
hysteresis so meters and later decisions don't chatter:

- ``silent``      below floor + open margin
- ``active``      above threshold for >= open_ms
- ``sustained``   continuously active >= sustained_ms (held note or steady speech)
- ``clipping``    peak >= -1 dBFS
- ``suspect_noise`` near-constant high level for >= noise_ms (hum, feedback, open mic in a loud room)
- ``stale``       no report for > stale_ms (device lost or helper stalled)

A single report never flips a state. Basic level features only: this does not
claim to recognise singing versus speech (ordinary speech VAD is unreliable on
singing).
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field

DEFAULT_FLOOR_DB = -60.0
OPEN_MARGIN_DB = 12.0     # open when rms > floor + 12
CLOSE_MARGIN_DB = 8.0     # close when rms < floor + 8 (hysteresis)
OPEN_MS = 250
CLOSE_MS = 600
SUSTAINED_MS = 1500
NOISE_MS = 5000
NOISE_MAX_STDEV_DB = 1.5
CLIP_PEAK_DB = -1.0
STALE_MS = 1000


@dataclass
class ChannelActivity:
    floor_db: float = DEFAULT_FLOOR_DB
    state: str = "silent"
    active_since: float | None = None
    _above_since: float | None = None
    _below_since: float | None = None
    last_t: float | None = None
    last_rms: float = -120.0
    last_peak: float = -120.0
    _recent: deque = field(default_factory=lambda: deque(maxlen=64))

    def update(self, rms_db: float, peak_db: float, t: float) -> str:
        t = float(t)
        rms_db, peak_db = float(rms_db), float(peak_db)
        self.last_t, self.last_rms, self.last_peak = t, rms_db, peak_db
        self._recent.append((t, rms_db))
        opened = self.active_since is not None
        if not opened:
            if rms_db > self.floor_db + OPEN_MARGIN_DB:
                self._above_since = self._above_since if self._above_since is not None else t
                if (t - self._above_since) * 1000.0 >= OPEN_MS:
                    self.active_since, self._below_since = self._above_since, None
            else:
                self._above_since = None
        else:
            if rms_db < self.floor_db + CLOSE_MARGIN_DB:
                self._below_since = self._below_since if self._below_since is not None else t
                if (t - self._below_since) * 1000.0 >= CLOSE_MS:
                    self.active_since, self._above_since = None, None
            else:
                self._below_since = None
        self.state = self._classify(t, peak_db)
        return self.state

    def _classify(self, t: float, peak_db: float) -> str:
        if peak_db >= CLIP_PEAK_DB:
            return "clipping"
        if self.active_since is None:
            return "silent"
        active_ms = (t - self.active_since) * 1000.0
        if active_ms >= NOISE_MS:
            window = [r for (tt, r) in self._recent if (t - tt) * 1000.0 <= NOISE_MS]
            if len(window) >= 20 and statistics.pstdev(window) <= NOISE_MAX_STDEV_DB:
                return "suspect_noise"
        if active_ms >= SUSTAINED_MS:
            return "sustained"
        return "active"

    def check_stale(self, now: float) -> str:
        if self.last_t is None or (float(now) - self.last_t) * 1000.0 > STALE_MS:
            self.state, self.active_since, self._above_since = "stale", None, None
        return self.state


def calibrate_floor(rms_samples_db, *, percentile: float = 0.5) -> float:
    """Noise floor from ~3-5 s of an idle channel (median by default), clamped."""
    values = sorted(float(v) for v in rms_samples_db if v is not None)
    if not values:
        return DEFAULT_FLOOR_DB
    index = min(len(values) - 1, max(0, int(round(percentile * (len(values) - 1)))))
    return max(-100.0, min(-20.0, values[index]))


class MicActivity:
    """Tracks all mapped roles from ``levels`` rows."""

    def __init__(self, roles: dict, floors: dict | None = None):
        self.roles = dict(roles)                       # role -> channel
        self.channels = {r: ChannelActivity(floor_db=(floors or {}).get(r, DEFAULT_FLOOR_DB)) for r in roles}

    def feed(self, levels_row: dict, t: float) -> dict:
        ch = levels_row.get("ch", {}) if isinstance(levels_row, dict) else {}
        for role, number in self.roles.items():
            data = ch.get(str(number))
            if not isinstance(data, dict):
                continue
            try:
                self.channels[role].update(float(data["rms_db"]), float(data["peak_db"]), t)
            except (KeyError, TypeError, ValueError):
                continue
        return self.snapshot(t)

    def snapshot(self, now: float) -> dict:
        return {role: {"state": act.check_stale(now) if act.last_t is None or (now - act.last_t) * 1000 > STALE_MS else act.state,
                       "rms_db": act.last_rms, "peak_db": act.last_peak, "floor_db": act.floor_db}
                for role, act in self.channels.items()}
