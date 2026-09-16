# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os
import platform
import subprocess
import sys

project_root = Path(SPECPATH)
machine = platform.machine().lower()
brew_root = Path("/opt/homebrew") if machine in {"arm64", "aarch64"} else Path("/usr/local")

# GStreamer has been removed from SingWS. This Apple Silicon build no longer
# copies GStreamer (framework plugins, typelibs, scanner, core dylibs) —
# that framework was the single largest thing in the bundle (~315 MiB). `gi`
# is in `excludes` below so PyInstaller cannot pull GStreamer back in.

extra_datas = [(str(project_root / "assets" / "rotation-stage-purple.png"), "assets")]
binaries = []
# Prompt 6 sound helper (optional): bundled only if it has been built.
_sound_helper = project_root / "native" / "sound_helper" / "SingWSSoundHelper"
if _sound_helper.is_file():
    binaries.append((str(_sound_helper), "."))
_mic_meter = project_root / "native" / "sound_helper" / "SingWSMicMeter"
if _mic_meter.is_file():
    binaries.append((str(_mic_meter), "."))

for helper in (
    "media_helpers.py",
    "libmpv_media_jobs.py",
    "mpv_playback_iina.py",
    "mpv_karaoke_transport.py",
    "bass_background_engine.py",
    "libmpv_background_engine.py",
    "bass_soundboard_engine.py",
    "song_index.py",
    "singws_eq.py",
    "singws_master_audio.py",
    "mac_keep_awake.py",
    "legacy_import.py",
    "transition_events.py",
    "transition_cues.py",
    "transition_observer.py",
    "sound_monitor.py",
    "sound_classes.py",
    "mic_config.py",
    "mic_activity.py",
    "mic_monitor.py",
    "mic_diagnostics_dialog.py",
    "transition_analysis.py",
):
    helper_path = project_root / helper
    if helper_path.exists():
        extra_datas.append((str(helper_path), "."))

moltenvk_icd = project_root / "MoltenVK_icd.json"
iina_frameworks = Path(os.environ.get("SINGWS_MPV_FRAMEWORKS", "") or
                       (project_root / "native_dual_view" / "Frameworks"))
bridge_dylib = project_root / "native" / "mpv_bridge" / "libsingws_mpv_bridge.dylib"
if not iina_frameworks.is_dir() or not bridge_dylib.is_file():
    raise SystemExit("Required bundled native mpv bridge/runtime is missing")
iina_dylibs = sorted(iina_frameworks.glob("*.dylib"))
if not iina_dylibs:
    raise SystemExit(f"No dylibs found in {iina_frameworks}")
binaries.extend((str(path), ".") for path in iina_dylibs)
binaries.append((str(bridge_dylib), "."))
print(f"[spec] native mpv stack: {len(iina_dylibs)} dylibs + bridge")
extra_datas.append((str(moltenvk_icd), "vulkan/icd.d"))

for bass_lib in (Path("vendor/bass") / name for name in (
    "libbass.dylib",
    "libbassmix.dylib",
    "libbassflac.dylib",
)):
    if bass_lib.exists():
        binaries.append((str(bass_lib), "vendor/bass"))

# libssl and libcrypto MUST come from one OpenSSL build. libmpv links
# Homebrew's, Python links the framework's, and the two share sonames, so
# whichever pair the bundle ends up with has to satisfy both. Taking the
# Homebrew pair does: it is the newer build, and an older consumer resolves
# against it. Mixing them does not — a Homebrew libssl beside the framework's
# libcrypto dies on a missing _CRYPTO_calloc, which is exactly what the
# post-build libmpv load test caught.
_already_bundled = {os.path.basename(source) for source, _ in binaries}
# The native libmpv stack links GnuTLS, so use the Python runtime's matching
# OpenSSL pair and avoid pulling a newer Homebrew deployment target into the
# macOS 12 bundle.
for openssl_lib in ("libssl.3.dylib", "libcrypto.3.dylib"):
    if openssl_lib in _already_bundled:
        continue  # libmpv's closure already supplied the matching pair
    # The native mpv runtime links GnuTLS. Use the Python framework's macOS-12
    # OpenSSL pair and prevent dependency analysis from pulling Homebrew's
    # newer deployment target into the bundle.
    candidates = (
        Path(sys.base_prefix) / "lib" / openssl_lib,
        Path(sys.prefix) / "lib" / openssl_lib,
    )
    for candidate in candidates:
        if candidate.exists():
            binaries.append((str(candidate), "."))
            break

# PyInstaller 6.21's Qt dependency validator does not resolve the framework-
# qualified @rpath names used by Qt 6.11 plugins, so it silently excludes even
# the mandatory Cocoa platform plugin. Bundle the runtime plugin groups
# explicitly at the path used by PyInstaller's PyQt6 runtime hook.
qt_plugins_root = (
    Path(sys.prefix)
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
    / "PyQt6"
    / "Qt6"
    / "plugins"
)
qt_plugin_groups = (
    "iconengines",
    "imageformats",
    "multimedia",
    "networkinformation",
    "platforms",
    "styles",
    "tls",
)
qt_plugin_allowlists = {
    "platforms": {"libqcocoa.dylib"},
    "multimedia": {"libdarwinmediaplugin.dylib"},
    "tls": {"libqsecuretransportbackend.dylib"},
}
qt_plugin_excludes = {
    "imageformats": {"libqpdf.dylib", "libqtga.dylib", "libqwbmp.dylib"},
}
for plugin_group in qt_plugin_groups:
    plugin_dir = qt_plugins_root / plugin_group
    plugin_files = sorted(plugin_dir.glob("*.dylib"))
    if not plugin_files:
        raise SystemExit(f"Required Qt plugin group is missing: {plugin_dir}")
    allowed = qt_plugin_allowlists.get(plugin_group)
    if allowed is not None:
        plugin_files = [path for path in plugin_files if path.name in allowed]
    excluded = qt_plugin_excludes.get(plugin_group, set())
    plugin_files = [path for path in plugin_files if path.name not in excluded]
    if not plugin_files:
        raise SystemExit(f"Qt plugin allowlist removed every file in: {plugin_dir}")
    for plugin_file in plugin_files:
        binaries.append(
            (str(plugin_file), f"PyQt6/Qt6/plugins/{plugin_group}")
        )

a = Analysis(
    [str(project_root / '0.2.18.1.py')],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=extra_datas,
    hiddenimports=[
        'mutagen',
        'media_helpers',
        'libmpv_media_jobs',
        'libmpv_background_engine',
        'bass_soundboard_engine',
        'mpv_karaoke_transport',
        'bass_background_engine',
        # The Homebrew backend and the python-mpv module are added below only
        # for the homebrew stack: PyInstaller's ctypes hook resolves
        # ctypes.util.find_library('mpv') for the `mpv` module and bundles
        # Homebrew's libmpv plus its whole macOS-14 closure, which is exactly
        # what the IINA swap exists to remove.
        'mpv_playback_iina',
        'song_index',
        # 10-band graphic EQ added this session — pulls in numpy + scipy.
        'singws_eq',
        'singws_master_audio',
        'mac_keep_awake',
        'numpy',
        'scipy',
        'scipy.signal',
        'scipy.signal._sosfilt',
        'scipy.signal._signaltools',
        'ssl',
        '_ssl',
        '_hashlib',
        'certifi',
        'urllib3.util.ssl_',
        # WebSocket request relay (wss://wskar.com/relay)
        'PyQt6.QtWebSockets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Keep GStreamer and the retired python-mpv/follower stack out of the
    # permanent native-bridge package.
    excludes=['gi', 'gi.repository', 'mpv', 'pytest', '_pytest', 'pygments',
              'setuptools', 'wheel',
              'signalsmith_audio_native'],
    noarchive=False,
    optimize=0,
)


def _keep_runtime_binary(item):
    destination = str(item[0]).replace("\\", "/")
    name = Path(destination).name
    qt_ffmpeg_names = (
        "libavcodec.", "libavformat.", "libavutil.",
        "libswresample.", "libswscale.",
    )
    # PyInstaller also adds top-level symlink TOC entries for these libraries.
    # Remove those with their targets so staging has no dangling links.
    if name.startswith(qt_ffmpeg_names):
        return False
    if "/plugins/generic/" in destination:
        return False
    allowlisted_groups = {
        "/plugins/platforms/": {"libqcocoa.dylib"},
        "/plugins/multimedia/": {"libdarwinmediaplugin.dylib"},
        "/plugins/tls/": {"libqsecuretransportbackend.dylib"},
    }
    for marker, allowed in allowlisted_groups.items():
        if marker in destination and name not in allowed:
            return False
    if "/plugins/imageformats/" in destination and name in qt_plugin_excludes["imageformats"]:
        return False
    if "PyQt6/Qt6/lib/" in destination and name.startswith(qt_ffmpeg_names):
        return False
    return True


a.binaries = [item for item in a.binaries if _keep_runtime_binary(item)]

# Fail loudly if GStreamer ever sneaks back into the frozen graph.
_gst_binaries = [item for item in a.binaries if 'gst' in str(item[0]).lower() or 'gstreamer' in str(item[0]).lower()]
if _gst_binaries:
    raise SystemExit(f"GStreamer artifacts unexpectedly present in build: {_gst_binaries[:5]}")


def _keep_target_binary(item):
    """Drop dependencies that cannot execute on the target architecture."""
    source = Path(str(item[1]))
    if not source.exists():
        return True
    result = subprocess.run(
        ["lipo", "-archs", str(source)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0 or "arm64" in result.stdout.split():
        return True
    print(f"[arm64-build] excluding dependency without arm64 slice: {source}")
    return False


a.binaries = [item for item in a.binaries if _keep_target_binary(item)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SingWSPro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    # This dedicated Apple Silicon package requires every native dependency to
    # carry an arm64 slice.
    target_arch='arm64',
    codesign_identity=None,
    entitlements_file=str(project_root / 'SingWS.entitlements'),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SingWSPro',
)
# SingWS Pro (2.0) installs beside SingWS 1.x: separate bundle name and id,
# so macOS permissions, preferences and /Applications entries never collide.
# Version metadata is preserved in Info.plist via info_plist below.
app = BUNDLE(
    coll,
    name='SingWS Pro.app',
    icon=str(project_root / 'SingWS.icns'),
    bundle_identifier='com.singws.pro',
    info_plist={
        'CFBundleName': 'SingWS Pro',
        'CFBundleDisplayName': 'SingWS Pro',
        'CFBundleShortVersionString': '2.0.0.0',
        'CFBundleVersion': '2.0.0.0',
        'LSMinimumSystemVersion': '15.0',
        'NSHighResolutionCapable': True,
        'NSMicrophoneUsageDescription': (
            "SingWS Pro can read live mic levels from your mixer's USB inputs to see when singers or the host "
            "are still using a mic. Only levels are measured; nothing is recorded. This is off unless you enable it."
        ),
        'NSAudioCaptureUsageDescription': (
            "SingWS Pro can listen to its own music output to tell when a song's vocals have really finished. "
            "Nothing is recorded or saved. This is off unless you enable it."
        ),
        'NSAppleEventsUsageDescription': (
            "SingWS uses System Events to find, queue, and control songs in the KaraFun application."
        ),
        'NSLocationWhenInUseUsageDescription': (
            "SingWS uses this Mac's location to set venue coordinates for request signups."
        ),
        'NSLocationUsageDescription': (
            "SingWS uses this Mac's location to set venue coordinates for request signups."
        ),
    },
)
