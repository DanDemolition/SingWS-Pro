#!/usr/bin/env python3
"""Print Phase 1 transition cue candidates from the cached analysis (read-only).

Usage:
    python tools/inspect_transition_cues.py [--cache PATH] [--match TEXT] [--limit N] [--json]

Reads ~/SingWSPro/transition-analysis.json (or $SINGWS_HOME). Never decodes media,
never writes the cache, and has no effect on playback.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from transition_analysis import TransitionAnalysisCache  # noqa: E402
from transition_cues import derive_cues  # noqa: E402


def default_cache() -> Path:
    home = os.environ.get("SINGWS_HOME") or str(Path.home() / "SingWSPro")
    return Path(home) / "transition-analysis.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, default=default_cache())
    ap.add_argument("--match", default="", help="only paths containing this text")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cache = TransitionAnalysisCache(args.cache)
    cache.load()
    records = [r for k, r in sorted(cache._records.items()) if args.match.lower() in k.lower()]
    cues = [(r.path, derive_cues(r)) for r in records]
    usable = [(p, c) for p, c in cues if c is not None]
    if args.json:
        print(json.dumps([dict(path=p, **c.to_dict()) for p, c in usable[: args.limit]], indent=2))
        return 0
    print(f"cache: {args.cache}  records: {len(records)}  usable: {len(usable)}")
    summary = Counter(reason for _p, c in usable for reason in c.reasons)
    for reason, count in summary.most_common():
        print(f"  {count:6d}  {reason}")
    def f(x):
        return "   -   " if x is None else f"{x:7.2f}"
    print("\n  start    end  lyric  bgm_in  early   dur  outro       file")
    for path, c in usable[: args.limit]:
        print(f"{f(c.audible_start)}{f(c.audible_end)}{f(c.final_lyric)}{f(c.safe_bgm_entry)}"
              f"{f(c.safe_early_end)}{c.duration:6.1f}  {c.outro:<11} {Path(path).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
