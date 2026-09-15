#!/bin/bash
# One-command release for SingWS.
#
#   ./release.sh          # auto-increment patch (0.2.18.1 -> 0.2.18.2)
#   ./release.sh 0.3.0    # release a specific version
#
# Steps: run tests -> bump version (APP_VERSION + spec CFBundle) -> build the
# the native arm64 DMG -> regenerate
# docs/release.json with real
# size+sha256 -> commit + tag, push tag -> draft release, upload + verify
# the DMGs, publish -> push main. SingWS Pro clients read docs/release.json
# from main of DanDemolition/SingWSPro, so main is pushed LAST: the
# manifest must never go live before the installers it points at are downloadable.
#
# Plain ./build_all.sh remains a non-publishing test build.
set -euo pipefail
cd "$(dirname "$0")"

# SingWS Pro releases from its own repo (DanDemolition/SingWSPro), never the
# 1.x repo, so 1.x update checks can never see a Pro installer.
REPO="DanDemolition/SingWSPro"
ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
if [[ "$ORIGIN_URL" != *"DanDemolition/SingWSPro"* ]]; then
  echo "!! origin must be $REPO (found: ${ORIGIN_URL:-none}). Refusing to publish."
  exit 1
fi
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$BRANCH" != "main" ]]; then
  echo "!! SingWS Pro releases run from main (on: $BRANCH)."
  exit 1
fi
# See build_all.sh: fall back to ~/.singws-python314 when the system Python 3.14
# framework is absent. An explicit SINGWS_DYLD_FRAMEWORK_PATH always wins.
if [[ -z "${SINGWS_DYLD_FRAMEWORK_PATH:-}" \
      && ! -f /Library/Frameworks/Python.framework/Versions/3.14/Python \
      && -f "$HOME/.singws-python314/Python.framework/Versions/3.14/Python" ]]; then
  SINGWS_DYLD_FRAMEWORK_PATH="$HOME/.singws-python314"
  # PyInstaller finds the Python shared library by basename via DYLD_LIBRARY_PATH.
  : "${SINGWS_DYLD_LIBRARY_PATH:=$HOME/.singws-python314/Python.framework/Versions/3.14}"
fi
if [[ -n "${SINGWS_DYLD_FRAMEWORK_PATH:-}" ]]; then
  export DYLD_FRAMEWORK_PATH="$SINGWS_DYLD_FRAMEWORK_PATH"
fi
if [[ -n "${SINGWS_DYLD_LIBRARY_PATH:-}" ]]; then
  export DYLD_LIBRARY_PATH="$SINGWS_DYLD_LIBRARY_PATH"
fi

PY=".venv/bin/python"
EXPLICIT_VERSION="${1:-}"

echo "========================================"
echo " SingWS RELEASE  $(date)"
echo "========================================"

# Fail early if the release tools aren't where we expect.
[ -x "$PY" ] || { echo "!! $PY not found"; exit 1; }
command -v gh >/dev/null || { echo "!! gh (GitHub CLI) not found"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "!! gh not authenticated (run: gh auth login)"; exit 1; }

CUR_VER="$($PY tools/release_version.py --current)"
echo ">>> current version: $CUR_VER"

# 1) Tests first — never ship a version that fails the suite. (Runs on current
#    code; version-independent, so do it before the bump.)
echo ">>> [1/7] running test suite"
# Via tools/run_tests.sh, not a bare pytest: the runner selects an interpreter
# backed by a real Python install, forces QT_QPA_PLATFORM=offscreen, and skips
# the one non-pytest script. Calling pytest directly aborts on every GUI test.
SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS=1 ./tools/run_tests.sh

# 2) Bump (or set) the version. This writes APP_VERSION + the spec CFBundle
#    strings so the built app and DMG names use the new version.
if [ -n "$EXPLICIT_VERSION" ]; then
  NEW_VER="$($PY tools/release_version.py --set "$EXPLICIT_VERSION")"
else
  NEW_VER="$($PY tools/release_version.py --bump)"
fi
TAG="v$NEW_VER"
# 2.0 versions must start at 2.x so a tag can never collide with, or look
# newer than, a 1.x (0.4.7.x) release on the other channel.
if [[ "$NEW_VER" != 2.* ]]; then
  echo "!! SingWS 2.0 release version must be 2.x (got $NEW_VER). Pass one, e.g. ./release.sh 2.0.0.1"
  git checkout -- 0.2.18.1.py SingWS-arm64.spec 2>/dev/null || true
  exit 1
fi
echo ">>> [2/7] version: $CUR_VER -> $NEW_VER (tag $TAG)"

if git rev-parse "$TAG" >/dev/null 2>&1 || gh release view "$TAG" >/dev/null 2>&1; then
  echo "!! $TAG already exists — bump to a new version or delete the old release/tag first."
  echo "   (reverting the version bump)"
  git checkout -- 0.2.18.1.py SingWS-arm64.spec 2>/dev/null || true
  exit 1
fi

# 3) Build the native flavor for this Mac. mpv/MoltenVK cannot be safely
# cross-packaged from the opposite Homebrew architecture; build the other
# native DMG on its matching Mac and copy it into this directory.
echo ">>> [3/7] building native installer for $(uname -m)"
./build_all.sh
DMG_ARM="SingWS-Pro-$NEW_VER-arm64-installer.dmg"
[ -f "$DMG_ARM" ] || {
  echo "!! expected DMG not found: $DMG_ARM"
  echo "   The Apple Silicon build did not produce an installer; check the output above."
  exit 1
}
RELEASE_DMGS=("$DMG_ARM")

# 4) Regenerate the auto-update manifest from the freshly built DMGs.
echo ">>> [4/7] writing docs/release.json"
$PY tools/write_manifest.py "$NEW_VER"

# 5) Commit the version bump + manifest and tag, but push ONLY the tag.
#    docs/release.json must not reach main until the DMGs are
#    actually downloadable, or auto-update clients get offered 404s.
echo ">>> [5/7] commit + tag (pushing tag only; main is pushed last)"
git add \
  .gitignore .gitmodules \
  vendor/pybind11 vendor/signalsmith-linear vendor/signalsmith-stretch \
  0.2.18.1.py \
  mpv_playback_iina.py mpv_karaoke_transport.py libmpv_media_jobs.py \
  native/mpv_bridge/bridge.mm \
  media_helpers.py libmpv_background_engine.py bass_soundboard_engine.py \
  MoltenVK_icd.json \
  SingWS-arm64.spec \
  build_all.sh build_singws_mac_arm64.sh \
  test_mpv_karaoke_transport.py test_karaoke_engine_selection.py \
  test_performance_safety.py test_remote_request_tombstones.py \
  tools/verify_macos_min_version.py \
  tools/probe_cdg_render.py tools/run_tests.sh \
  tools/write_manifest.py tools/release_version.py \
  test_release_tools.py docs/release.json legacy_import.py test_legacy_import.py HANDOFF.md release.sh
git commit -m "Release $TAG"
git tag "$TAG"
git push origin "$TAG"

# 6) Create the release as a draft, upload the DMGs (retrying flaky uploads),
#    verify every asset landed at full size, then publish. Until this step
#    finishes, nothing is public and clients are unaffected.
echo ">>> [6/7] creating GitHub release $TAG (draft) + uploading DMGs"
gh release create "$TAG" \
  --draft \
  --title "SingWS Pro $NEW_VER" \
  --notes "Automated release $TAG."

for d in "${RELEASE_DMGS[@]}"; do
  uploaded=""
  for attempt in 1 2 3; do
    if gh release upload "$TAG" "$d" --clobber; then uploaded=1; break; fi
    echo "   upload of $d failed (attempt $attempt/3), retrying in 10s..."
    sleep 10
  done
  if [ -z "$uploaded" ]; then
    echo "!! could not upload $d after 3 attempts."
    echo "   The release is still an UNPUBLISHED DRAFT and main was not pushed, so"
    echo "   auto-update clients are unaffected. To finish by hand:"
    echo "     gh release upload $TAG ${RELEASE_DMGS[*]} --clobber"
    echo "     gh release edit $TAG --draft=false --latest"
    echo "     git push origin main"
    exit 1
  fi
done

for d in "${RELEASE_DMGS[@]}"; do
  local_size="$(stat -f%z "$d")"
  remote_size="$(gh release view "$TAG" --json assets --jq ".assets[] | select(.name == \"$d\") | .size")"
  if [ "$local_size" != "$remote_size" ]; then
    echo "!! uploaded asset $d is ${remote_size:-missing} bytes, expected $local_size."
    echo "   Re-upload it (gh release upload $TAG $d --clobber), then publish and"
    echo "   push main as printed above. The draft release is not public yet."
    exit 1
  fi
done

gh release edit "$TAG" --draft=false --latest

# 7) Only now go live: push main so SingWS Pro clients see the new manifest, and
#    confirm an advertised download URL actually resolves.
echo ">>> [7/7] pushing main (manifest goes live)"
git push origin main || {
  echo "!! push to main failed. The release IS published, but clients still see"
  echo "   the previous manifest. Fix and re-run: git push origin main"
  exit 1
}

http_code="$(curl -sIL -o /dev/null -w '%{http_code}' \
  "https://github.com/$REPO/releases/download/$TAG/$DMG_ARM")"
if [ "$http_code" != "200" ]; then
  echo "!! warning: download check for $DMG_ARM returned HTTP $http_code —"
  echo "   verify the release assets manually before trusting auto-update."
fi

echo "========================================"
echo " RELEASED SingWS Pro $NEW_VER"
echo "   https://github.com/$REPO/releases/tag/$TAG"
echo "   SingWS Pro clients see it via docs/release.json on main."
echo "========================================"
