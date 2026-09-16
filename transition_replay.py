"""Deterministic replay harness for the Phase 2 observer (no audio hardware).

A scenario is plain JSON:

    {"name": "...",
     "record": {...TransitionAnalysis fields...} | null,
     "events": [
        {"t": 0.0, "type": "start", "playhead": 0.0},
        {"t": 0.0, "type": "play", "from": 0.0, "to": 200.0, "step": 0.5},
        {"t": 181.0, "type": "bgm_started"},
        {"t": 186.0, "type": "seek", "playhead": 90.0},
        {"t": 200.2, "type": "end", "trigger": "eos"},
        {"t": 5.0, "type": "stale_position", "generation": 0, "playhead": 199.0}
     ],
     "expect": [{"kind": "observer_proposal", "action": "end_karaoke"}, ...],
     "forbid": [{"action": "end_karaoke"}]}

``play`` expands into position reports at wall offset t + (playhead - from).
``expect`` items must match emitted events in order (subset match on keys);
``forbid`` items must match none.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from transition_analysis import TransitionAnalysis
from transition_cues import derive_cues
from transition_observer import TransitionObserver


def _record(payload):
    if not payload:
        return None
    base = {"path": "/replay/track", "mtime": 1, "size": 1, "media_kind": "karaoke"}
    base.update(payload)
    return TransitionAnalysis(**base)


def _flatten(event: dict, out: dict) -> dict:
    row = {"kind": event["kind"]}
    for key in ("generation", "track", "playhead_s"):
        if event.get(key) is not None:
            row[key] = event[key]
    row.update(out)
    return row


def run_scenario(scenario: dict) -> list[dict]:
    emitted: list[dict] = []

    def sink(kind, **kwargs):
        row = {"kind": kind}
        row.update({k: v for k, v in kwargs.items() if v is not None})
        emitted.append(row)

    observer = TransitionObserver(sink)
    observer.set_mic_mode(scenario.get("mic_mode", "off"))
    cues = derive_cues(_record(scenario.get("record")), now=0.0)
    generation = 1
    for ev in scenario.get("events", []):
        kind = ev["type"]
        t = float(ev.get("t", 0.0))
        if kind == "start":
            observer.song_started(generation=generation, track="replay", cues=cues,
                                  t_mono=t, playhead=float(ev.get("playhead", 0.0)))
        elif kind == "play":
            start, stop, step = float(ev["from"]), float(ev["to"]), float(ev.get("step", 0.5))
            ph = start
            while ph <= stop + 1e-9:
                observer.position(generation=generation, playhead=round(ph, 3),
                                  t_mono=round(t + (ph - start), 3))
                ph += step
        elif kind == "position":
            observer.position(generation=generation, playhead=float(ev["playhead"]), t_mono=t)
        elif kind == "stale_position":
            observer.position(generation=int(ev.get("generation", generation - 1)),
                              playhead=float(ev["playhead"]), t_mono=t)
        elif kind == "seek":
            observer.seeked(generation=generation, playhead=float(ev["playhead"]), t_mono=t)
        elif kind == "sound":
            # {"t": .., "type": "sound", "result": "active_vocal", "confidence": 0.9, "repeat": 3, "step": 0.5}
            for i in range(int(ev.get("repeat", 1))):
                observer.sound(generation=generation, result=ev["result"],
                               confidence=float(ev.get("confidence", 0.9)),
                               t_mono=t + i * float(ev.get("step", 0.5)), model="replay")
        elif kind == "mic":
            # {"t": .., "type": "mic", "roles": {"singer1": "sustained", "host": "silent"}, "repeat": 10, "step": 0.1}
            roles = {r: {"state": s} for r, s in ev.get("roles", {}).items()}
            for i in range(int(ev.get("repeat", 1))):
                observer.mic(roles=roles, t_mono=round(t + i * float(ev.get("step", 0.1)), 3))
        elif kind == "mic_unavailable":
            observer.mic_unavailable(reason=ev.get("reason", "replay"))
        elif kind == "sound_unavailable":
            observer.sound_unavailable(reason=ev.get("reason", "replay"))
        elif kind == "bgm_started":
            observer.bgm_started(t_mono=t)
        elif kind == "end":
            observer.song_ended(generation=generation, trigger=ev.get("trigger", "eos"),
                                t_mono=t, playhead=ev.get("playhead"))
            generation += 1
        else:
            raise ValueError(f"unknown replay event type: {kind}")
    return emitted


def _matches(row: dict, pattern: dict) -> bool:
    for key, want in pattern.items():
        have = row.get(key)
        if isinstance(want, float) and isinstance(have, (int, float)):
            if abs(have - want) > 1e-6:
                return False
        elif have != want:
            return False
    return True


def check_scenario(scenario: dict) -> tuple[bool, list[str], list[dict]]:
    emitted = run_scenario(scenario)
    problems = []
    cursor = 0
    for pattern in scenario.get("expect", []):
        while cursor < len(emitted) and not _matches(emitted[cursor], pattern):
            cursor += 1
        if cursor >= len(emitted):
            problems.append(f"missing (in order): {pattern}")
            break
        cursor += 1
    for pattern in scenario.get("forbid", []):
        for row in emitted:
            if _matches(row, pattern):
                problems.append(f"forbidden: {pattern} matched {row}")
    return (not problems), problems, emitted


def load_scenarios(folder: Path | str) -> list[tuple[Path, dict]]:
    return [(p, json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(Path(folder).glob("*.json"))]
