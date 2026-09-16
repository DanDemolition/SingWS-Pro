#!/usr/bin/env python3
"""Fail a macOS build when required Mach-O architecture slices are missing."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import subprocess
import sys
import sysconfig


# Python 3.11+ puts THIS script's directory on sys.path, not the caller's cwd,
# so the repo-root native extensions below are not importable when the build
# script runs `python tools/verify_macos_arch.py` from the repo root. Add the
# repo root explicitly rather than relying on PYTHONPATH.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


RUNTIME_NATIVE_MODULES = (
    "_struct",
    "PyQt6.QtCore",
    "numpy._core._multiarray_umath",
    "scipy._lib._ccallback_c",
    "signalsmith_audio_native",
)


def _macho_arches(path: Path) -> set[str] | None:
    file_result = subprocess.run(
        ["file", "-b", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if "Mach-O" not in file_result.stdout:
        return None
    lipo_result = subprocess.run(
        ["lipo", "-archs", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if lipo_result.returncode != 0:
        raise RuntimeError(lipo_result.stderr.strip() or f"lipo failed for {path}")
    return set(lipo_result.stdout.split())


def _runtime_paths() -> list[Path]:
    paths = [Path(sys.executable).resolve()]
    python_library = sysconfig.get_config_var("LDLIBRARY")
    library_dir = sysconfig.get_config_var("LIBDIR")
    if python_library and library_dir:
        candidate = Path(library_dir) / python_library
        if candidate.exists():
            paths.append(candidate.resolve())
    for module_name in RUNTIME_NATIVE_MODULES:
        module = importlib.import_module(module_name)
        module_path = Path(module.__file__).resolve()
        paths.append(module_path)
    return paths


def _bundle_paths(bundle: Path) -> list[Path]:
    if not bundle.is_dir():
        raise FileNotFoundError(f"app bundle not found: {bundle}")
    cocoa_plugin = (
        bundle
        / "Contents"
        / "Frameworks"
        / "PyQt6"
        / "Qt6"
        / "plugins"
        / "platforms"
        / "libqcocoa.dylib"
    )
    if not cocoa_plugin.is_file():
        raise FileNotFoundError(
            f"required Qt Cocoa platform plugin not found: {cocoa_plugin}"
        )
    return [path for path in bundle.rglob("*") if path.is_file() and not path.is_symlink()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require", action="append", required=True, choices=("arm64", "x86_64"))
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--path", type=Path, action="append", default=[])
    args = parser.parse_args()
    if not args.runtime and args.bundle is None and not args.path:
        parser.error("one of --runtime, --bundle, or --path is required")

    candidates: list[Path] = []
    if args.runtime:
        try:
            candidates.extend(_runtime_paths())
        except (ImportError, OSError) as exc:
            print(f"architecture verification failed: runtime dependency unavailable: {exc}", file=sys.stderr)
            return 1
    if args.bundle is not None:
        try:
            candidates.extend(_bundle_paths(args.bundle.resolve()))
        except FileNotFoundError as exc:
            print(f"architecture verification failed: {exc}", file=sys.stderr)
            return 1
    candidates.extend(path.resolve() for path in args.path)

    required = set(args.require)
    checked = 0
    failures: list[tuple[Path, set[str]]] = []
    for path in dict.fromkeys(candidates):
        arches = _macho_arches(path)
        if arches is None:
            continue
        checked += 1
        if not required.issubset(arches):
            failures.append((path, arches))

    if checked == 0:
        print("architecture verification failed: no Mach-O files found", file=sys.stderr)
        return 1
    if failures:
        print(f"architecture verification failed: required {sorted(required)}", file=sys.stderr)
        for path, arches in failures:
            print(f"  {path}: found {sorted(arches)}", file=sys.stderr)
        return 1
    print(f"architecture verification passed: {checked} Mach-O files include {sorted(required)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
