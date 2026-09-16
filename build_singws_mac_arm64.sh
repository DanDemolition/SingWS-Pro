#!/bin/bash
# Build only the Apple Silicon SingWS app and styled installer DMG.
set -euo pipefail

cd "$(dirname "$0")"

# A Python.org framework extracted for a portable build cannot live at its
# installer path under /Library.  Carry the explicit shim through this shell;
# macOS strips DYLD_* variables when launching protected system shells.
if [[ -n "${SINGWS_DYLD_FRAMEWORK_PATH:-}" ]]; then
    export DYLD_FRAMEWORK_PATH="$SINGWS_DYLD_FRAMEWORK_PATH"
fi
if [[ -n "${SINGWS_DYLD_LIBRARY_PATH:-}" ]]; then
    export DYLD_LIBRARY_PATH="$SINGWS_DYLD_LIBRARY_PATH"
fi

APP_NAME="SingWS Pro"
EXE_NAME="SingWSPro"
ENTRY="0.2.18.1.py"
SPEC="SingWS-arm64.spec"
PYTHON=".venv/bin/python"

if [[ "$(uname -m)" != "arm64" ]]; then
    echo "This dedicated build must run natively on an Apple Silicon Mac."
    exit 1
fi

for required in \
    "$ENTRY" "$SPEC" "$PYTHON" \
    mpv_karaoke_transport.py MoltenVK_icd.json constraints-macos15.txt \
    SingWS.entitlements dmg_settings.py tools/verify_macos_arch.py \
    tools/verify_macos_min_version.py; do
    [[ -e "$required" ]] || { echo "Missing required file: $required"; exit 1; }
done

for command in hdiutil codesign file otool shasum; do
    command -v "$command" >/dev/null || { echo "Missing command: $command"; exit 1; }
done

: "${SINGWS_MPV_FRAMEWORKS:=$(pwd)/native_dual_view/Frameworks}"
export SINGWS_MPV_FRAMEWORKS
STACK_INPUTS=(
    mpv_playback_iina.py
    native/mpv_bridge/libsingws_mpv_bridge.dylib
    "$SINGWS_MPV_FRAMEWORKS/singws_libmpv.2.dylib"
)
for required in "${STACK_INPUTS[@]}"; do
    [[ -e "$required" ]] || { echo "Missing Apple Silicon playback dependency: $required"; exit 1; }
    if file "$required" | grep -q "Mach-O" && ! file "$required" | grep -q "arm64"; then
        echo "Playback dependency is not Apple Silicon arm64: $required"
        exit 1
    fi
done

"$PYTHON" tools/verify_macos_arch.py --runtime --require arm64
"$PYTHON" -c "import PyQt6"

# SingWS Pro 2.0 targets macOS 15 and above, arm64 only. Wheel tags cannot be
# trusted to report a real deployment target (PyQt6 has shipped a
# "macosx_10_14" tag over binaries requiring 13.0), so check the installed
# versions against the verified pin set instead, and the finished bundle
# against tools/verify_macos_min_version.py --maximum 15.0 below.
"$PYTHON" - <<'PYPINS'
import re, sys
from importlib.metadata import PackageNotFoundError, version
wanted = {}
for line in open("constraints-macos15.txt", encoding="utf-8"):
    line = line.split("#", 1)[0].strip()
    if not line:
        continue
    name, _, pin = line.partition("==")
    wanted[name.strip()] = pin.strip()
bad = []
for name, pin in wanted.items():
    try:
        found = version(name)
    except PackageNotFoundError:
        bad.append(f"{name}: not installed (need {pin})")
        continue
    if found != pin:
        bad.append(f"{name}: {found} installed, macOS 15 build needs {pin}")
if bad:
    print("Dependency versions break macOS 15 support:")
    for line in bad:
        print(f"  {line}")
    sys.exit("Install the pinned set: pip install -c constraints-macos15.txt "
             + " ".join(wanted))
print(f"macOS 15 dependency pins verified: "
      + ", ".join(f"{n}=={v}" for n, v in sorted(wanted.items())))
PYPINS

APP_VERSION="$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' "$ENTRY" | head -1)"
[[ -n "$APP_VERSION" ]] || { echo "APP_VERSION is missing from $ENTRY"; exit 1; }
DMG_NAME="SingWS-Pro-${APP_VERSION}-arm64-installer.dmg"

echo "Building $APP_NAME $APP_VERSION for Apple Silicon..."
.venv/bin/python tools/make_dmg_assets.py --style-only

rm -rf build dist
"$PYTHON" -m PyInstaller --noconfirm "$SPEC"

APP_PATH="dist/$APP_NAME.app"
[[ -d "$APP_PATH" ]] || { echo "Build failed: $APP_PATH was not created"; exit 1; }
"$PYTHON" tools/verify_macos_arch.py --bundle "$APP_PATH" --require arm64

REQUIRED_BUNDLED=(
    "$APP_PATH/Contents/Frameworks/singws_libmpv.2.dylib"
    "$APP_PATH/Contents/Frameworks/libsingws_mpv_bridge.dylib"
)
LIBMPV_NAMES=(libsingws_mpv_bridge.dylib singws_libmpv.2.dylib)
for bundled in "${REQUIRED_BUNDLED[@]}"; do
    [[ -e "$bundled" ]] || { echo "Bundled mpv dependency is missing: $bundled"; exit 1; }
done

# Prove the permanent bundled media core loads before this bundle goes any
# further; there is no alternate karaoke engine to mask a broken runtime.
"$PYTHON" - "$APP_PATH" "${LIBMPV_NAMES[@]}" <<'PYCHECK'
import ctypes, pathlib, sys
frameworks = pathlib.Path(sys.argv[1]) / "Contents" / "Frameworks"
for name in sys.argv[2:]:
    target = frameworks / name
    if not target.exists():
        sys.exit(f"Bundled {name} is missing")
    try:
        ctypes.CDLL(str(target))
    except OSError as exc:
        sys.exit(f"Bundled {name} cannot be loaded:\n{exc}")
print(f"Bundled media core loads cleanly: {', '.join(sys.argv[2:])}")
PYCHECK

# SingWS 2.0 supports Apple Silicon on macOS 15+ only. Nothing in the shipped
# bundle may require a newer macOS than 15.0.
# Checks the real Mach-O load commands, not wheel tags or filenames.
"$PYTHON" tools/verify_macos_min_version.py "$APP_PATH" --arch arm64 --maximum 15.0

# Stage the bundle BEFORE signing, and sign the staged copy.
#
# This project lives under ~/Documents, which is file-provider (iCloud)
# managed: every file carries com.apple.FinderInfo and com.apple.fileprovider
# xattrs, and codesign refuses them with "resource fork, Finder information, or
# similar detritus not allowed". They cannot be stripped in place -- xattr -cr
# runs, and the file provider puts them straight back. A
# `ditto --norsrc --noextattr` copy outside that tree has none of them.
# build_all.sh always signed such a copy, which is why it never hit this.
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/singws-arm64-dmg.XXXXXX")"
cleanup() {
    rm -rf "$STAGING"
}
trap cleanup EXIT
mkdir -p "$STAGING/dist"
ditto --norsrc --noextattr "$APP_PATH" "$STAGING/dist/$APP_NAME.app"

# PyInstaller recreates compatibility symlinks after Analysis, including links
# for Qt's FFmpeg libraries even though that unused plugin and its libraries
# were filtered out. Remove only those now-dangling aliases before codesign and
# dmgbuild traverse the staged bundle.
for link_dir in Contents/Frameworks Contents/Resources; do
    for library in libavcodec.61.dylib libavformat.61.dylib libavutil.59.dylib \
                   libswresample.5.dylib libswscale.8.dylib; do
        link="$STAGING/dist/$APP_NAME.app/$link_dir/$library"
        [[ ! -L "$link" ]] || rm "$link"
    done
done
if find -L "$STAGING/dist/$APP_NAME.app" -type l -print -quit | grep -q .; then
    echo "Staged app contains a dangling symlink"
    find -L "$STAGING/dist/$APP_NAME.app" -type l -print
    exit 1
fi

codesign --force --deep --sign - \
    --entitlements SingWS.entitlements "$STAGING/dist/$APP_NAME.app"
codesign --verify --deep --strict "$STAGING/dist/$APP_NAME.app"

rm -f "$DMG_NAME"
SINGWS_DMG_APP_ROOT="$STAGING" "$PYTHON" -m dmgbuild \
    -s dmg_settings.py "SingWS Pro ${APP_VERSION}" "$DMG_NAME"
hdiutil verify "$DMG_NAME"

echo "Apple Silicon build complete:"
echo "  App: $(pwd)/$APP_PATH"
echo "  DMG: $(pwd)/$DMG_NAME"
shasum -a 256 "$APP_PATH/Contents/MacOS/$EXE_NAME" "$DMG_NAME"
