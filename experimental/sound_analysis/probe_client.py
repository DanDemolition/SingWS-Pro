"""Run SingWSSoundProbe as a child process safely (experimental, not shipped).

- hard timeout; the child is killed on timeout
- bounded output: at most ``max_lines`` lines, each at most 8 KiB
- a crash or malformed line never raises into the caller; it is reported
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from sound_classes import score_window  # noqa: E402

PROBE = HERE / "SingWSSoundProbe"


def analyze_file(path: str, *, timeout_s: float = 120.0, max_lines: int = 20000,
                 probe: Path = PROBE) -> dict:
    report = {"windows": [], "summary": None, "errors": [], "malformed": 0, "truncated": False}
    try:
        proc = subprocess.run([str(probe), "--file", str(path)], capture_output=True,
                              text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        report["errors"].append("timeout")
        return report
    except OSError as exc:
        report["errors"].append(f"probe_unavailable: {exc}")
        return report
    lines = proc.stdout.splitlines()
    if len(lines) > max_lines:
        lines, report["truncated"] = lines[:max_lines], True
    for line in lines:
        if len(line) > 8192:
            report["malformed"] += 1
            continue
        try:
            row = json.loads(line)
        except ValueError:
            report["malformed"] += 1
            continue
        kind = row.get("type")
        if kind == "window":
            scored = score_window(row.get("top", []))
            report["windows"].append({"t": row.get("t"), "result": scored.result,
                                      "confidence": round(scored.confidence, 3),
                                      "reason": scored.reason, "latency_ms": row.get("latency_ms")})
        elif kind == "summary":
            report["summary"] = row
        elif kind == "error":
            report["errors"].append(str(row.get("message")))
    if proc.returncode != 0:
        report["errors"].append(f"exit_{proc.returncode}")
    return report
