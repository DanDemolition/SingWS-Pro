#!/usr/bin/env python3
"""Regenerate docs/release.json (the SingWS Pro auto-update manifest) from built DMGs.

SingWS Pro has its own repo (DanDemolition/SingWS-Pro), separate from SingWS 1.x.
Download URLs point at the exact release tag.

The desktop updater reads this file from GitHub Pages, compares ``version``
against APP_VERSION, and verifies the download against the per-arch ``sha256``.
So the manifest MUST reflect the actual built DMGs — this computes real sizes
and hashes rather than hand-editing.

Usage:
    python tools/write_manifest.py <version> [dmg_dir]
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "DanDemolition/SingWS-Pro"
CHANNEL = "pro"
MANIFEST_NAME = "release.json"

ARCHES = [
    ("mac_arm64", "Apple Silicon Mac (macOS 15+)", "arm64"),
]


def _required_arch_keys() -> set[str]:
    """Arches whose absence must fail the release rather than be skipped.

    SingWS 2.0 ships Apple Silicon only, so a missing arm64 DMG means the build
    silently failed. Override with SINGWS_REQUIRED_ARCHES (a
    comma-separated list of manifest keys) when releasing from a different host.
    """
    raw = os.environ.get("SINGWS_REQUIRED_ARCHES", "mac_arm64")
    return {part.strip() for part in raw.split(",") if part.strip()}


def human_size(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.0f} MB"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(version: str, dmg_dir: Path = ROOT, *, repo: str = REPO,
                   release_date: str | None = None) -> dict:
    version = version.lstrip("vV").strip()
    downloads = {}
    # Only required DMGs are fatal when missing. Advertising a file that is not there is still forbidden --
    # clients would be offered a 404 -- so entries are only written for DMGs that
    # exist, and a manifest with nothing in it is an error.
    required = _required_arch_keys()
    for key, label, arch in ARCHES:
        filename = f"SingWS-Pro-{version}-{arch}-installer.dmg"
        path = dmg_dir / filename
        if not path.exists():
            if key in required:
                raise SystemExit(f"missing DMG for manifest: {path}")
            print(f"note: {filename} not present — omitting {key} from the manifest")
            continue
        downloads[key] = {
            "label": label,
            "filename": filename,
            "url": f"https://github.com/{repo}/releases/download/v{version}/{filename}",
            "size": human_size(path.stat().st_size),
            "sha256": sha256(path),
        }
    if not downloads:
        raise SystemExit(f"no DMGs found for {version} in {dmg_dir}")
    return {
        "name": "SingWS Pro",
        "channel": CHANNEL,
        "version": version,
        "release_date": release_date or datetime.date.today().isoformat(),
        "repository": repo,
        "release_url": f"https://github.com/{repo}/releases/tag/v{version}",
        "downloads": downloads,
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: write_manifest.py <version> [dmg_dir]")
    version = sys.argv[1]
    dmg_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT
    manifest = build_manifest(version, dmg_dir)
    out = ROOT / "docs" / MANIFEST_NAME
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {out} (version {manifest['version']})")


if __name__ == "__main__":
    main()
