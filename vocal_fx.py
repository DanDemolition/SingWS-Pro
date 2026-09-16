"""Host side of the vocal-effects helper (Prompt 11, VFX0/VFX1).

Off by default. Wet-only: the dry voice stays in the mixer and never passes
through SingWS, so a helper failure costs the effect and never the vocal.

Deliberately its **own** ``SoundMonitor`` subclass instance, not a shared one.
The Prompt 10 gate says the effects callback must not share the transition
analysis worker; sharing a supervisor instance would reintroduce exactly that
coupling, letting a classifier stall bypass reverb mid-song. Inheritance reuses
the code; the failure counter stays separate.

Nothing here can reach karaoke or BGM playback. It has no transport reference.
"""
from __future__ import annotations

import io
import json
import threading
from typing import Optional

from sound_monitor import SoundMonitor

SETTING_ENABLED = "vfx_enabled"          # master switch, default off
SETTING_DEVICE = "vfx_device_uid"
SETTING_CHANNELS = "vfx_channels"        # 1-based mixer USB channels
SETTING_FRAMES = "vfx_frames"            # 64 or 128
SETTING_EFFECT = "vfx_effect"            # "none" (VFX0) | "reverb" (VFX1)
SETTING_WET = "vfx_wet"                  # 0.0 .. 4.0 linear

DEFAULT_FRAMES = 128
DEFAULT_EFFECT = "none"
EFFECTS = ("none", "reverb")

# Transient: drop to silence, report, retry; not a failure. Same policy as the
# mic path (USB_INPUT_DESIGN.md), because the cause is usually a cable.
_TRANSIENT = ("device_lost", "format_changed")


def is_enabled(settings: dict | None) -> bool:
    """Effects are opt-in. Absent or malformed settings mean off."""
    if not isinstance(settings, dict):
        return False
    return bool(settings.get(SETTING_ENABLED, False))


def channels_from_settings(settings: dict | None) -> list[int]:
    raw = (settings or {}).get(SETTING_CHANNELS) or []
    out = []
    for v in raw:
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 64:
            out.append(n)
    return out


class VocalFXMonitor(SoundMonitor):
    def __init__(self, helper_path, *, device_uid: str, channels: list[int],
                 frames: int = DEFAULT_FRAMES, effect: str = DEFAULT_EFFECT, **kw):
        super().__init__(helper_path, **kw)
        self._device_uid = str(device_uid or "")
        self._channels = [int(c) for c in (channels or [])]
        self._frames = int(frames or DEFAULT_FRAMES)
        self._effect = effect if effect in EFFECTS else DEFAULT_EFFECT
        self._send_lock = threading.Lock()
        self._helper_stats: Optional[dict] = None
        # Mirrors what we last asked for, so a restarted helper can be restored
        # to the operator's setting instead of silently coming back bypassed.
        self._wet = 1.0
        self._on = False

    # -- supervision hooks ---------------------------------------------------

    def _command(self) -> list[str]:
        cmd = [str(self._helper), "--device", self._device_uid,
               "--frames", str(self._frames), "--effect", self._effect]
        if self._channels:
            cmd += ["--channels", ",".join(str(c) for c in self._channels)]
        return cmd

    def _is_transient(self, outcome: str) -> bool:
        return outcome in _TRANSIENT

    def _set_state(self, state: str, reason: str) -> None:
        super()._set_state(state, reason)
        # The base consumes "hello" before _on_row sees it, and this is the one
        # hook that fires on it. A fresh helper always starts bypassed, so a
        # transient restart would otherwise silently leave effects off after the
        # operator had turned them on.
        if reason == "hello" and self._on:
            self._send({"type": "params", "wet": self._wet, "enabled": True})

    def _on_row(self, kind, row: dict, now: float) -> Optional[str]:
        if kind in _TRANSIENT:
            # Returned as the outcome so _is_transient() can classify it; the
            # helper exits straight after sending these.
            return kind
        if kind == "stats":
            with self._lock:
                self._helper_stats = dict(row)
                self._last_heartbeat = now
        return None

    # -- control -------------------------------------------------------------

    def _send(self, obj: dict) -> bool:
        """Write one control line. Never raises into the caller: a dead pipe is
        a supervision problem, not something that should break the UI."""
        with self._send_lock:
            proc = self._proc
            if proc is None or proc.stdin is None:
                return False
            try:
                line = json.dumps(obj) + "\n"
                # SoundMonitor opens the helper in text mode; tolerate either.
                proc.stdin.write(line if isinstance(proc.stdin, io.TextIOBase)
                                 or "b" not in getattr(proc.stdin, "mode", "")
                                 else line.encode("utf-8"))
                proc.stdin.flush()
                return True
            except (BrokenPipeError, ValueError, OSError):
                return False

    def set_params(self, wet: float, enabled: bool) -> bool:
        try:
            wet = float(wet)
        except (TypeError, ValueError):
            wet = 1.0
        wet = max(0.0, min(4.0, wet))
        self._wet, self._on = wet, bool(enabled)
        return self._send({"type": "params", "wet": wet, "enabled": bool(enabled)})

    def bypass(self) -> bool:
        """Immediate bypass. The helper fades to silence rather than hard-muting."""
        self._on = False
        return self._send({"type": "bypass"})

    # -- read-only status ----------------------------------------------------

    @property
    def helper_stats(self) -> Optional[dict]:
        """Latest stats row. Named to avoid shadowing SoundMonitor.stats, which
        is a plain dict the base class writes to."""
        with self._lock:
            return dict(self._helper_stats) if self._helper_stats else None

    @property
    def estimated_latency_ms(self) -> Optional[float]:
        h = self.hello
        if not h:
            return None
        try:
            return float(h.get("estimated_latency_ms"))
        except (TypeError, ValueError):
            return None
