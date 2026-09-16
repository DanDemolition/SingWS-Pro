"""SingWS Pro side of the sound helper (Prompt 6): advisory evidence only.

Runs ``SingWSSoundHelper --tap-pid <SingWS pid>`` in a separate, low-priority
process ONLY while armed (the ending region of a song), and exposes the latest
classified window to the transition observer.

Safety contract:
- No audio passes through this process; the helper taps the mixed output itself.
- Every public method is O(1) and never raises; launching and reading happen on
  a daemon thread, never the GUI thread.
- Bounded: keeps only the newest ``history`` windows; stale windows (older than
  ``window_ttl_s``) are never returned.
- Health: heartbeat timeout, error lines, crashes, or CPU/RSS over budget kill
  the helper. After ``max_failures`` the monitor BYPASSES for the rest of the
  session and the observer falls back to deterministic-only reasoning.
- Nothing here can control playback.
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from sound_classes import score_window

STATE_OFF = "off"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_BYPASSED = "bypassed"


class SoundMonitor:
    def __init__(self, helper_path: str | os.PathLike, *, target_pid: Optional[int] = None,
                 clock: Callable[[], float] = time.monotonic,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 heartbeat_timeout_s: float = 3.0, window_ttl_s: float = 2.5,
                 max_failures: int = 3, history: int = 8,
                 cpu_budget_pct: float = 15.0, rss_budget_mb: float = 200.0,
                 on_health: Optional[Callable[[str, str], None]] = None,
                 extra_args: tuple[str, ...] = ()):
        self._helper = Path(helper_path)
        self._pid = int(target_pid if target_pid is not None else os.getpid())
        self._clock = clock
        self._popen = popen
        self._hb_timeout = float(heartbeat_timeout_s)
        self._ttl = float(window_ttl_s)
        self._max_failures = int(max_failures)
        self._cpu_budget = float(cpu_budget_pct)
        self._rss_budget = float(rss_budget_mb)
        self._on_health = on_health
        self._extra = tuple(extra_args)
        self._lock = threading.Lock()
        self._windows: collections.deque = collections.deque(maxlen=max(1, int(history)))
        self._state = STATE_OFF
        self._reason = ""
        self._failures = 0
        self._proc: Optional[subprocess.Popen] = None
        self._armed = False
        self._closed = False
        self._last_heartbeat = 0.0
        self._model = ""
        self._thread: Optional[threading.Thread] = None
        self._seq = 0   # monotonically increasing window id across restarts
        self.hello: dict = {}
        self.stats = {"windows": 0, "malformed": 0, "restarts": 0, "helper_dropped": 0}

    # ---- cheap, GUI-safe API -----------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def model(self) -> str:
        return self._model

    def arm(self) -> None:
        try:
            with self._lock:
                if self._closed or self._state == STATE_BYPASSED or self._armed:
                    return
                if not self._helper.is_file():
                    self._set_state(STATE_BYPASSED, "helper_missing")
                    return
                self._armed = True
                self._set_state(STATE_STARTING, "armed")
                self._thread = threading.Thread(target=self._run, name="singws-sound-monitor", daemon=True)
                self._thread.start()
        except Exception:
            pass

    def disarm(self) -> None:
        try:
            with self._lock:
                self._armed = False
                proc = self._proc
                self._windows.clear()
                if self._state in (STATE_STARTING, STATE_RUNNING):
                    self._set_state(STATE_OFF, "disarmed")
            self._stop_proc(proc)
        except Exception:
            pass

    def latest(self) -> Optional[dict]:
        try:
            with self._lock:
                if self._state != STATE_RUNNING or not self._windows:
                    return None
                row = self._windows[-1]
            if self._clock() - row["t_mono"] > self._ttl:
                return None
            return dict(row)
        except Exception:
            return None

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self.disarm()

    # ---- internals ---------------------------------------------------------------
    def _set_state(self, state: str, reason: str) -> None:
        if state == self._state and reason == self._reason:
            return
        self._state, self._reason = state, reason
        if self._on_health is not None:
            try:
                self._on_health(state, reason)
            except Exception:
                pass

    def _stop_proc(self, proc: Optional[subprocess.Popen]) -> None:
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()   # helper exits on stdin EOF
        except Exception:
            pass
        try:
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass

    def _fail(self, reason: str) -> None:
        with self._lock:
            self._failures += 1
            proc, self._proc = self._proc, None
            if self._failures >= self._max_failures:
                self._armed = False
                self._set_state(STATE_BYPASSED, f"unhealthy:{reason}")
            else:
                self.stats["restarts"] += 1
                self._set_state(STATE_STARTING, f"restart:{reason}")
            self._windows.clear()
        self._stop_proc(proc)

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._armed or self._closed or self._state == STATE_BYPASSED:
                    return
            try:
                proc = self._popen(
                    self._command(),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1,
                )
                try:
                    os.setpriority(os.PRIO_PROCESS, proc.pid, 10)
                except Exception:
                    pass
            except Exception:
                self._fail("launch_failed")
                continue
            with self._lock:
                abandoned = not self._armed
                if not abandoned:
                    self._proc = proc
                    self._last_heartbeat = self._clock()
            if abandoned:
                self._stop_proc(proc)
                return
            watchdog = threading.Thread(target=self._watch, args=(proc,), daemon=True)
            watchdog.start()
            outcome = self._read(proc)
            with self._lock:
                still_armed = self._armed and self._proc is proc
            if not still_armed:
                return
            if self._is_transient(outcome):
                with self._lock:
                    old, self._proc = self._proc, None
                    self._windows.clear()
                    self._set_state(STATE_STARTING, f"waiting:{outcome}")
                self._stop_proc(old)
                time.sleep(self._retry_delay_s)
                continue
            self._fail(outcome)

    _retry_delay_s = 2.0

    def _is_transient(self, outcome: str) -> bool:
        """Outcomes that should retry quietly without counting as a failure."""
        return False

    def _command(self) -> list[str]:
        return [str(self._helper), "--tap-pid", str(self._pid), *self._extra]

    def _on_row(self, kind, row: dict, now: float) -> Optional[str]:
        """Handle helper-specific rows. Return a failure reason to stop, else None."""
        if kind == "window":
            scored = score_window(row.get("top", []))
            with self._lock:
                self._seq += 1
                self._windows.append({"seq": self._seq, "t_mono": now, "result": scored.result,
                                      "confidence": round(float(scored.confidence), 3),
                                      "reason": scored.reason, "model": self._model})
                self.stats["windows"] += 1
                self._last_heartbeat = now
        return None

    def _read(self, proc: subprocess.Popen) -> str:
        try:
            for line in proc.stdout:
                if len(line) > 8192:
                    self.stats["malformed"] += 1
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    self.stats["malformed"] += 1
                    if self.stats["malformed"] > 50:
                        return "malformed_output"
                    continue
                kind = row.get("type") if isinstance(row, dict) else None
                now = self._clock()
                if kind == "hello":
                    with self._lock:
                        self._model = str(row.get("model", "") or row.get("name", ""))
                        self.hello = dict(row)
                        self._last_heartbeat = now
                        self._set_state(STATE_RUNNING, "hello")
                elif kind == "heartbeat":
                    with self._lock:
                        self._last_heartbeat = now
                        self.stats["helper_dropped"] = int(row.get("dropped", 0) or 0)
                    rss = float(row.get("rss_mb", 0) or 0)
                    if rss > self._rss_budget:
                        return "rss_over_budget"
                elif kind == "error":
                    return "helper_error"
                else:
                    outcome = self._on_row(kind, row, now)
                    if outcome:
                        return outcome
        except Exception:
            return "read_failed"
        return "helper_exited"

    def _watch(self, proc: subprocess.Popen) -> None:
        over_cpu = 0
        last_cpu = None
        try:
            import psutil  # already a SingWS dependency
            ps = psutil.Process(proc.pid)
            ps.cpu_percent(None)
        except Exception:
            ps = None
        while proc.poll() is None:
            time.sleep(0.5)
            with self._lock:
                if self._proc is not proc:
                    return
                age = self._clock() - self._last_heartbeat
            if age > self._hb_timeout:
                self._kill(proc)
                return
            if ps is not None:
                try:
                    last_cpu = ps.cpu_percent(None)
                except Exception:
                    ps = None
                    continue
                over_cpu = over_cpu + 1 if last_cpu > self._cpu_budget else 0
                if over_cpu >= 6:   # ~3 s sustained
                    self._kill(proc)
                    return

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            proc.kill()
        except Exception:
            pass
