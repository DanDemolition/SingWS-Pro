"""Live-mic level monitor (Prompt 7): runs SingWSMicMeter, tracks activity.

Diagnostic in this phase: nothing consumes it for transitions yet (Prompt 8).
Off by default. Built on ``SoundMonitor``'s process supervision (daemon thread,
heartbeat, crash/garbage/RSS budgets, bypass after repeated failures).

Unplugging the mixer or changing its sample rate is NOT a failure: the monitor
drops all mic evidence immediately, shows "waiting", and retries every 2 s, so
plugging the mixer back in resumes metering without touching playback.
"""
from __future__ import annotations

from typing import Optional

from mic_activity import MicActivity
from mic_config import MicInputConfig
from sound_monitor import STATE_RUNNING, SoundMonitor

TRANSIENT = ("device_lost", "format_changed")


class MicMonitor(SoundMonitor):
    def __init__(self, helper_path, config: MicInputConfig, **kwargs):
        kwargs.setdefault("heartbeat_timeout_s", 3.0)
        kwargs.setdefault("rss_budget_mb", 80.0)
        kwargs.setdefault("cpu_budget_pct", 10.0)
        super().__init__(helper_path, **kwargs)
        self.config = config
        self.activity = MicActivity(config.roles, config.noise_floor_db)
        self._last_levels_t: Optional[float] = None

    def _command(self) -> list[str]:
        return [str(self._helper), "--device", self.config.device_uid,
                "--channels", ",".join(str(c) for c in self.config.channels())]

    def _is_transient(self, outcome: str) -> bool:
        return outcome in TRANSIENT

    def arm(self) -> None:
        if self.config.problems():
            with self._lock:
                self._set_state("off", "config:" + ",".join(self.config.problems()))
            return
        super().arm()

    def _on_row(self, kind, row: dict, now: float) -> Optional[str]:
        if kind == "levels":
            with self._lock:
                self.activity.feed(row, now)
                self._last_levels_t = now
                self._last_heartbeat = now
                self._seq += 1
            return None
        if kind in TRANSIENT:
            with self._lock:
                self.activity = MicActivity(self.config.roles, self.config.noise_floor_db)
            return kind
        return None

    def latest(self) -> Optional[dict]:
        """Per-role snapshot, or None when not running or reports are stale."""
        try:
            with self._lock:
                if self._state != STATE_RUNNING or self._last_levels_t is None:
                    return None
                now = self._clock()
                if now - self._last_levels_t > self._ttl:
                    return None
                snap = self.activity.snapshot(now)
                return {"seq": self._seq, "t_mono": self._last_levels_t, "roles": snap,
                        "device": self.hello.get("name", self.config.device_name)}
        except Exception:
            return None


def list_input_devices(helper_path, *, timeout_s: float = 5.0, run=None) -> list[dict]:
    """Input-capable Core Audio devices via ``SingWSMicMeter --list-devices``.

    Call from a worker thread (it launches a process). Never raises; [] on failure.
    """
    import json
    import subprocess
    run = run or subprocess.run
    try:
        proc = run([str(helper_path), "--list-devices"], capture_output=True, text=True, timeout=timeout_s)
        for line in (proc.stdout or "").splitlines():
            row = json.loads(line)
            if isinstance(row, dict) and row.get("type") == "devices":
                return [d for d in row.get("devices", []) if isinstance(d, dict) and d.get("uid")]
    except Exception:
        pass
    return []
