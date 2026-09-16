"""Phase 2 Intelligent Audio: Observer mode (propose, never act).

The observer watches the same facts the existing transition system uses and
records what an assisted transition WOULD do, next to what actually happened.

Hard boundary (tested): this module has no access to playback. It imports no
Qt or engine code, receives only plain values, and its only output is the
``sink`` callable (normally the Phase 0 event recorder). There is no method
that starts, stops, seeks, fades or schedules anything.

Timing uses the monotonic clock passed with each call plus the transport
playhead; wall-clock and tick counts are never used for decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from transition_cues import TrackCues

MODE_OFF = "off"
MODE_OBSERVER = "observer"
MODES = (MODE_OFF, MODE_OBSERVER)

MIN_PROPOSAL_CONFIDENCE = 0.8
# Sound evidence (Prompt 6): windows needed to switch "program vocal active".
VOCAL_ON_WINDOWS = 3
VOCAL_OFF_WINDOWS = 3
MIN_SOUND_CONFIDENCE = 0.6
# Listen only near the ending region (seconds before the earliest cue).
LISTEN_LEAD_S = 15.0

# Live mic evidence (Prompt 8). All modes remain advisory.
MIC_OFF = "off"
MIC_OBSERVE = "observe"
MIC_SINGER = "singer_protection"
MIC_SINGER_HOST = "singer_protection_host_ducking"
MIC_MODES = (MIC_OFF, MIC_OBSERVE, MIC_SINGER, MIC_SINGER_HOST)
MIC_ON_S = 0.5        # raw activity must persist this long (monotonic) to count
MIC_OFF_S = 1.2       # and be absent this long to clear (minimum hold)
SINGER_ROLES = ("singer1", "singer2", "singers")
HOST_ROLES = ("host",)
_ACTIVE_STATES = ("active", "sustained", "clipping")


class _Presence:
    """Debounced on/off for one group of mic roles (monotonic timestamps)."""

    def __init__(self, name: str):
        self.name = name
        self.active = False
        self._raw_since: Optional[float] = None
        self._quiet_since: Optional[float] = None

    def update(self, raw: bool, t: float) -> Optional[str]:
        if raw:
            self._quiet_since = None
            if self._raw_since is None:
                self._raw_since = t
            if not self.active and t - self._raw_since >= MIC_ON_S:
                self.active = True
                return "on"
        else:
            self._raw_since = None
            if self.active:
                if self._quiet_since is None:
                    self._quiet_since = t
                if t - self._quiet_since >= MIC_OFF_S:
                    self.active = False
                    return "off"
        return None

    def reset(self) -> bool:
        was = self.active
        self.active, self._raw_since, self._quiet_since = False, None, None
        return was
# A position report must be at most this old (monotonic seconds) to count.
MAX_POSITION_AGE_S = 2.0


@dataclass
class _Song:
    generation: int
    track: Optional[str]
    cues: Optional[TrackCues]
    started_mono: float
    last_playhead: float = 0.0
    last_mono: float = 0.0
    proposed_bgm_at: Optional[float] = None       # playhead when proposed
    proposed_bgm_mono: Optional[float] = None
    proposed_end_at: Optional[float] = None
    proposed_end_mono: Optional[float] = None
    actual_bgm_mono: Optional[float] = None
    seeked: bool = False
    vocal_active: bool = False
    vocal_streak: int = 0
    quiet_streak: int = 0
    held_end: bool = False
    held_end_reasons: tuple = ()
    held_bgm: bool = False
    sound_model: str = ""


class TransitionObserver:
    def __init__(self, sink: Callable[..., object], *, mode: str = MODE_OBSERVER):
        self._sink = sink
        self._mode = mode if mode in MODES else MODE_OFF
        self._song: Optional[_Song] = None
        self.stats = {"proposals": 0, "stale_ignored": 0, "sink_errors": 0}
        self._mic_mode = MIC_OFF
        self._singers = _Presence("singers")
        self._host = _Presence("host")
        self._mic_last_t: Optional[float] = None
        self._untrusted: set = set()
        self._last_generation = 0
        self._last_track: Optional[str] = None

    # ---- configuration ---------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        self._mode = mode if mode in MODES else MODE_OFF
        if self._mode == MODE_OFF:
            self._song = None

    # ---- output ------------------------------------------------------------
    def _emit(self, kind: str, song: _Song, playhead: Optional[float], **data) -> None:
        try:
            self._sink(kind, generation=song.generation, track=song.track,
                       playhead_s=playhead, **data)
        except Exception:
            self.stats["sink_errors"] += 1

    def _emit_ctx(self, kind: str, playhead: Optional[float], **data) -> None:
        song = self._song
        try:
            self._sink(kind, generation=song.generation if song else self._last_generation,
                       track=song.track if song else self._last_track, playhead_s=playhead, **data)
        except Exception:
            self.stats["sink_errors"] += 1

    # ---- live mic evidence (Prompt 8) --------------------------------------------
    @property
    def mic_mode(self) -> str:
        return self._mic_mode

    def set_mic_mode(self, mode: str) -> None:
        self._mic_mode = mode if mode in MIC_MODES else MIC_OFF
        if self._mic_mode == MIC_OFF:
            self._drop_mic("mic_off")

    def _singer_protection(self) -> bool:
        return self._mic_mode in (MIC_SINGER, MIC_SINGER_HOST)

    def _host_ducking(self) -> bool:
        return self._mic_mode == MIC_SINGER_HOST

    def _drop_mic(self, reason: str) -> None:
        changed = self._singers.reset() | self._host.reset()
        self._mic_last_t = None
        if changed and self._mode == MODE_OBSERVER:
            self._emit_ctx("observer_evidence", None, evidence="mic", state="unavailable", reason=str(reason))

    def mic(self, *, roles: dict, t_mono: float) -> None:
        """One live-mic snapshot: {role: {"state": ...}}. Debounced; never a decision alone."""
        if self._mode != MODE_OBSERVER or self._mic_mode == MIC_OFF:
            return
        t = float(t_mono)
        if self._mic_last_t is not None and t < self._mic_last_t:
            self.stats["stale_ignored"] += 1
            return
        self._mic_last_t = t
        states = {r: str((v or {}).get("state", "")) for r, v in (roles or {}).items()}
        if states and all(s == "stale" for s in states.values()):
            self._drop_mic("mic_stale")
            return
        for role, state in states.items():
            if state == "suspect_noise" and role not in self._untrusted:
                self._untrusted.add(role)
                self._emit_ctx("observer_evidence", None, evidence="mic", role=role, state="untrusted",
                               reason="suspect_noise_or_feedback")
            elif state in ("silent",) and role in self._untrusted:
                self._untrusted.discard(role)
                self._emit_ctx("observer_evidence", None, evidence="mic", role=role, state="trusted")
        playhead = self._song.last_playhead if self._song else None

        def raw(group):
            return any(states.get(r) in _ACTIVE_STATES and r not in self._untrusted for r in group)

        change = self._singers.update(raw(SINGER_ROLES), t)
        if change:
            self._emit_ctx("observer_evidence", playhead, evidence="singer_mic", state=change)
        change = self._host.update(raw(HOST_ROLES), t)
        if change:
            self._emit_ctx("observer_evidence", playhead, evidence="host_mic", state=change)
            song = self._song
            between_songs = song is None or song.proposed_bgm_at is not None
            if self._host_ducking() and between_songs:
                self.stats["proposals"] += 1
                self._emit_ctx("observer_proposal", playhead,
                               action="bgm_duck" if change == "on" else "bgm_raise",
                               confidence=0.8, reason="host_speaking" if change == "on" else "host_finished",
                               context="between_songs" if song is None else "song_tail")

    def mic_unavailable(self, *, reason: str) -> None:
        """Mixer unplugged, helper bypassed or config invalid: drop mic evidence now."""
        if self._mode == MODE_OBSERVER:
            self._drop_mic(reason)

    def _current(self, generation: int) -> Optional[_Song]:
        song = self._song
        if self._mode != MODE_OBSERVER or song is None:
            return None
        if int(generation) != song.generation:
            self.stats["stale_ignored"] += 1
            return None
        return song

    # ---- inputs (plain values only) ------------------------------------------
    def song_started(self, *, generation: int, track: Optional[str],
                     cues: Optional[TrackCues], t_mono: float, playhead: float = 0.0) -> None:
        if self._mode != MODE_OBSERVER:
            return
        self._song = _Song(int(generation), track, cues, float(t_mono),
                           last_playhead=float(playhead or 0.0), last_mono=float(t_mono))
        if cues is None:
            self._emit("observer_proposal", self._song, playhead, action="none",
                       confidence=0.0, reason="no_cached_cues")

    def position(self, *, generation: int, playhead: float, t_mono: float) -> None:
        song = self._current(generation)
        if song is None:
            return
        if float(t_mono) < song.last_mono:
            self.stats["stale_ignored"] += 1
            return
        song.last_playhead, song.last_mono = float(playhead), float(t_mono)
        cues = song.cues
        if cues is None or song.seeked:
            return
        if (song.proposed_bgm_at is None and cues.safe_bgm_entry is not None
                and cues.safe_bgm_entry_confidence >= MIN_PROPOSAL_CONFIDENCE
                and playhead >= cues.safe_bgm_entry):
            if self._singer_protection() and self._singers.active:
                if not song.held_bgm:
                    song.held_bgm = True
                    self._emit("observer_proposal", song, playhead, action="hold_bgm",
                               cue_s=cues.safe_bgm_entry, confidence=cues.safe_bgm_entry_confidence,
                               reason="singer_mic_active")
            else:
                song.proposed_bgm_at, song.proposed_bgm_mono = float(playhead), float(t_mono)
                self.stats["proposals"] += 1
                low = self._host_ducking() and self._host.active
                self._emit("observer_proposal", song, playhead,
                           action="bgm_fade_in_low" if low else "bgm_fade_in",
                           cue_s=cues.safe_bgm_entry, confidence=cues.safe_bgm_entry_confidence,
                           reason="host_speaking" if low else _first(cues.reasons, "bgm_entry"),
                           after_hold=song.held_bgm)
        if (song.proposed_end_at is None and cues.safe_early_end is not None
                and cues.safe_early_end_confidence >= MIN_PROPOSAL_CONFIDENCE
                and playhead >= cues.safe_early_end
                and (song.vocal_active or (self._singer_protection() and self._singers.active))):
            reason = "singer_mic_active" if (self._singer_protection() and self._singers.active) else "program_vocal_active"
            if reason not in song.held_end_reasons:
                song.held_end = True
                song.held_end_reasons = song.held_end_reasons + (reason,)
                self._emit("observer_proposal", song, playhead, action="hold",
                           cue_s=cues.safe_early_end, confidence=cues.safe_early_end_confidence,
                           reason=reason, model=song.sound_model if reason == "program_vocal_active" else "")
            return
        if (song.proposed_end_at is None and cues.safe_early_end is not None
                and cues.safe_early_end_confidence >= MIN_PROPOSAL_CONFIDENCE
                and playhead >= cues.safe_early_end):
            song.proposed_end_at, song.proposed_end_mono = float(playhead), float(t_mono)
            self.stats["proposals"] += 1
            self._emit("observer_proposal", song, playhead, action="end_karaoke",
                       cue_s=cues.safe_early_end, confidence=cues.safe_early_end_confidence,
                       remain_s=max(0.0, cues.duration - playhead),
                       reason=_first(cues.reasons, "early_end"))

    def listening_window(self, *, generation: int, playhead: float) -> bool:
        """True when sound evidence could change a proposal soon (ending region)."""
        song = self._song
        if self._mode != MODE_OBSERVER or song is None or song.generation != int(generation):
            return False
        cues = song.cues
        if cues is None or song.seeked:
            return False
        points = [p for p in (cues.safe_bgm_entry, cues.safe_early_end) if p is not None]
        if not points:
            return False
        return float(playhead) >= min(points) - LISTEN_LEAD_S and song.proposed_end_at is None


    def sound(self, *, generation: int, result: str, confidence: float, t_mono: float,
              model: str = "") -> None:
        """One classified program-audio window (advisory). Never a decision alone."""
        song = self._current(generation)
        if song is None:
            return
        song.sound_model = str(model or song.sound_model)
        vocal = str(result) == "active_vocal" and float(confidence) >= MIN_SOUND_CONFIDENCE
        if vocal:
            song.vocal_streak, song.quiet_streak = song.vocal_streak + 1, 0
        elif str(result) != "uncertain":
            song.quiet_streak, song.vocal_streak = song.quiet_streak + 1, 0
        # "uncertain" changes nothing: it neither starts nor clears vocal activity.
        if not song.vocal_active and song.vocal_streak >= VOCAL_ON_WINDOWS:
            song.vocal_active = True
            self._emit("observer_evidence", song, song.last_playhead, evidence="program_vocal",
                       state="on", confidence=float(confidence), model=song.sound_model)
        elif song.vocal_active and song.quiet_streak >= VOCAL_OFF_WINDOWS:
            song.vocal_active = False
            self._emit("observer_evidence", song, song.last_playhead, evidence="program_vocal",
                       state="off", result=str(result), model=song.sound_model)

    def sound_unavailable(self, *, reason: str) -> None:
        """Classifier missing/unhealthy: drop sound evidence, keep deterministic reasoning."""
        song = self._song
        if song is None or self._mode != MODE_OBSERVER:
            return
        if song.vocal_active or song.vocal_streak:
            song.vocal_active, song.vocal_streak, song.quiet_streak = False, 0, 0
            self._emit("observer_evidence", song, song.last_playhead, evidence="program_vocal",
                       state="unavailable", reason=str(reason))

    def seeked(self, *, generation: int, playhead: float, t_mono: float) -> None:
        song = self._current(generation)
        if song is None:
            return
        # After a manual seek the cue timeline no longer matches the performance.
        song.seeked = True
        song.last_playhead, song.last_mono = float(playhead), float(t_mono)
        self._emit("observer_compare", song, playhead, event="seek", note="proposals_suspended")

    def bgm_started(self, *, t_mono: float, generation: Optional[int] = None) -> None:
        # BGM code does not know the karaoke generation; None means "current song".
        if generation is None:
            if self._mode != MODE_OBSERVER or self._song is None:
                return
            generation = self._song.generation
        song = self._current(generation)
        if song is None or song.actual_bgm_mono is not None:
            return
        song.actual_bgm_mono = float(t_mono)
        delta = (float(t_mono) - song.proposed_bgm_mono) if song.proposed_bgm_mono is not None else None
        self._emit("observer_compare", song, song.last_playhead, event="bgm_fade_in",
                   proposed=song.proposed_bgm_mono is not None, delta_s=delta)

    def song_ended(self, *, generation: int, trigger: str, t_mono: float,
                   playhead: Optional[float] = None) -> None:
        song = self._current(generation)
        if song is None:
            return
        at = song.last_playhead if playhead is None else float(playhead)
        delta = (float(t_mono) - song.proposed_end_mono) if song.proposed_end_mono is not None else None
        manual = str(trigger) in ("manual_stop", "skip", "stop")
        self._emit("observer_compare", song, at, event="song_end", trigger=str(trigger),
                   proposed=song.proposed_end_mono is not None, delta_s=delta,
                   manual=manual, seeked=song.seeked,
                   saved_s=(max(0.0, at - song.proposed_end_at) if song.proposed_end_at is not None else None),
                   singer_mic_at_end=self._singers.active, host_mic_at_end=self._host.active)
        self._last_generation, self._last_track = song.generation, song.track
        self._song = None


def _first(reasons, prefix: str) -> str:
    for reason in reasons or ():
        if str(reason).startswith(prefix):
            return str(reason)
    return prefix
