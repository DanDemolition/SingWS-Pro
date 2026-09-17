#!/usr/bin/env python3
"""Validate key detection against real audio, with no musical knowledge needed.

The trick: we do not need to know a track's real key. We pitch-shift it by a
known number of semitones and require the detected tonic to move by exactly that
much. The shift is the ground truth, and it works on real, messy karaoke audio
rather than synthetic test tones.

Two properties are checked:

  octave invariance  -- shifting by +/-12 preserves every pitch class, so the
                        detected key must not change at all. Failing this means
                        the chroma is responding to spectral tilt, not to pitch.
  shift tracking     -- shifting by N moves the tonic by exactly N.

Usage:
    SINGWS_HOME=$(mktemp -d) PYTHONPATH=. ./qtvenv/bin/python \
        tools/validate_key_detect.py [--library DIR] [--tracks N]

Read-only: extracts to a temp dir and deletes as it goes. Never writes to the
library or to show data.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import tempfile
import zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import key_detect as kd  # noqa: E402
from phrase_detect import decode_pcm_mono  # noqa: E402

SHIFTS = (1, 2, 3, 5, 7)


def shift_semitones(pcm: np.ndarray, semis: float, sr: int) -> np.ndarray:
    import signalsmith_audio_native as sn
    engine = sn.StretchEngine(1, sr)
    engine.set_modifiers(tempo_ratio=1.0, semitones=float(semis))
    raw = engine.process_f32le(np.asarray(pcm, np.float32).tobytes()) + engine.flush_f32le()
    return np.frombuffer(raw, dtype=np.float32)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", default=os.path.expanduser("~/Music/Karaoke/Karaoke Library"))
    ap.add_argument("--tracks", type=int, default=12)
    ap.add_argument("--seconds", type=float, default=180.0)
    args = ap.parse_args()

    zips = sorted(glob.glob(os.path.join(args.library, "**", "*.zip"), recursive=True))[: args.tracks]
    if not zips:
        print(f"no archives under {args.library}", file=sys.stderr)
        return 2

    tmp = tempfile.mkdtemp()
    oct_ok = oct_total = shift_ok = shift_total = 0

    for path in zips:
        try:
            with zipfile.ZipFile(path) as zf:
                member = next((n for n in zf.namelist() if n.lower().endswith(".mp3")), None)
                if not member:
                    continue
                extracted = zf.extract(member, tmp)
        except Exception as exc:
            print(f"  skip {os.path.basename(path)[:40]}: {exc}")
            continue
        try:
            pcm = decode_pcm_mono(extracted, sr=kd.ANALYSIS_SR, max_seconds=args.seconds)
            base = kd.detect_key_from_pcm(pcm, kd.ANALYSIS_SR)
            if base is None:
                print(f"  {os.path.basename(path)[:36]:38} no key")
                continue
            cells = [f"{os.path.basename(path)[:28]:30} base={base.name:>9}"]
            for semis in (12, -12):
                got = kd.detect_key_from_pcm(shift_semitones(pcm, semis, kd.ANALYSIS_SR), kd.ANALYSIS_SR)
                good = got is not None and (got.tonic, got.mode) == (base.tonic, base.mode)
                oct_total += 1
                oct_ok += bool(good)
                cells.append(f"{semis:+d}:{'OK' if good else (got.name if got else '-')}")
            for semis in SHIFTS:
                got = kd.detect_key_from_pcm(shift_semitones(pcm, semis, kd.ANALYSIS_SR), kd.ANALYSIS_SR)
                good = (got is not None and got.tonic == (base.tonic + semis) % 12
                        and got.mode == base.mode)
                shift_total += 1
                shift_ok += bool(good)
                cells.append(f"+{semis}:{'OK' if good else (got.name if got else '-')}")
            print("  ".join(cells))
        finally:
            try:
                os.remove(extracted)
            except OSError:
                pass

    print(f"\noctave invariance {oct_ok}/{oct_total}   shift tracking {shift_ok}/{shift_total}")
    print("Both must be at or near 100% before any consumer acts on a detected key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
