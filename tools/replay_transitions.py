#!/usr/bin/env python3
"""Run observer replay scenarios and print what would have been proposed.

Usage:
    python tools/replay_transitions.py [FOLDER_OR_JSON ...] [--verbose]

Defaults to test_fixtures/transition_replay. No audio hardware, no app, no playback.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from transition_replay import check_scenario, load_scenarios  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", default=[str(ROOT / "test_fixtures" / "transition_replay")])
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    scenarios = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            scenarios += load_scenarios(p)
        else:
            scenarios.append((p, json.loads(p.read_text(encoding="utf-8"))))
    failed = 0
    for path, scenario in scenarios:
        ok, problems, emitted = check_scenario(scenario)
        failed += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {path.name}  {scenario.get('name', '')}")
        for problem in problems:
            print(f"      {problem}")
        if args.verbose or not ok:
            for row in emitted:
                print("      ", json.dumps(row, sort_keys=True))
    print(f"\n{len(scenarios) - failed}/{len(scenarios)} scenarios passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
