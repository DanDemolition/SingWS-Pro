#!/usr/bin/env python3
"""Benchmark SingWSSoundProbe over labelled clips (experimental).

Clip folder layout (the folder name is the expected class):
    clips/silence/*.wav  clips/music/*.mp3  clips/active_vocal/*.m4a
    clips/speech/*  clips/applause_crowd/*  clips/noisy_room/*   (noisy_room = report only)

    python benchmark.py --clips ~/SingWS-test-clips --out report.json
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from collections import Counter
from pathlib import Path

from probe_client import PROBE, analyze_file

AUDIO = {".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac", ".caf"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("report.json"))
    args = ap.parse_args()
    rows, confusion = [], Counter()
    for clip in sorted(p for p in args.clips.rglob("*") if p.suffix.lower() in AUDIO):
        expected = clip.parent.name
        t0 = time.perf_counter()
        rep = analyze_file(str(clip))
        wall = (time.perf_counter() - t0) * 1000.0
        results = Counter(w["result"] for w in rep["windows"])
        majority = results.most_common(1)[0][0] if results else "none"
        confusion[(expected, majority)] += 1
        summ = rep["summary"] or {}
        rows.append({"clip": str(clip.relative_to(args.clips)), "expected": expected,
                     "majority": majority, "window_results": dict(results),
                     "wall_ms": round(wall, 1), "cold_start_ms": summ.get("cold_start_ms"),
                     "avg_latency_ms": summ.get("avg_latency_ms"),
                     "max_latency_ms": summ.get("max_latency_ms"),
                     "peak_rss_mb": summ.get("peak_rss_mb"), "errors": rep["errors"]})
        print(f"{expected:>15} -> {majority:<15} {wall:8.0f} ms  {clip.name}")
    lat = [r["avg_latency_ms"] for r in rows if r["avg_latency_ms"] is not None]
    report = {
        "machine": platform.machine(), "macos": platform.mac_ver()[0], "probe": str(PROBE),
        "clips": rows,
        "confusion": {f"{e}->{m}": n for (e, m), n in sorted(confusion.items())},
        "median_avg_latency_ms": statistics.median(lat) if lat else None,
        "max_peak_rss_mb": max((r["peak_rss_mb"] or 0) for r in rows) if rows else None,
    }
    args.out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
