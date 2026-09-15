import importlib.util
import json
import unittest
from pathlib import Path


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rv = _load("release_version", "tools/release_version.py")
wm = _load("write_manifest", "tools/write_manifest.py")


class BumpTests(unittest.TestCase):
    def test_patch_increment(self):
        self.assertEqual(rv.bump_patch("0.2.18.1"), "0.2.18.2")
        self.assertEqual(rv.bump_patch("0.2.18.9"), "0.2.18.10")
        self.assertEqual(rv.bump_patch("1.2.3"), "1.2.4")
        self.assertEqual(rv.bump_patch("0.3.0"), "0.3.1")

    def test_write_version_updates_entry_and_specs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            entry = Path(d) / "0.2.18.1.py"
            entry.write_text('APP_VERSION = "0.2.18.1"\nprint("hi")\n')
            spec = Path(d) / "SingWS-arm64.spec"
            spec.write_text(
                "info_plist={\n"
                "    'CFBundleShortVersionString': '0.2.18.1',\n"
                "    'CFBundleVersion': '0.2.18.1',\n"
                "}\n"
            )
            rv.write_version("0.3.0", entry=entry, specs=[spec])
            self.assertEqual(rv.read_version(entry), "0.3.0")
            txt = spec.read_text()
            self.assertIn("'CFBundleShortVersionString': '0.3.0'", txt)
            self.assertIn("'CFBundleVersion': '0.3.0'", txt)

    def test_write_version_rejects_garbage(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            entry = Path(d) / "0.2.18.1.py"
            entry.write_text('APP_VERSION = "0.2.18.1"\n')
            with self.assertRaises(SystemExit):
                rv.write_version("; rm -rf /", entry=entry, specs=[])


class ManifestTests(unittest.TestCase):
    def _fake_dmgs(self, d: Path, version: str):
        for arch, content in (("arm64", b"A" * 1000),):
            (d / f"SingWS-Pro-{version}-{arch}-installer.dmg").write_bytes(content)

    def test_build_manifest_structure_and_hashes(self):
        import hashlib
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._fake_dmgs(d, "0.3.0")
            man = wm.build_manifest("v0.3.0", d, release_date="2026-06-07")
            self.assertEqual(man["version"], "0.3.0")  # 'v' stripped
            self.assertEqual(man["release_date"], "2026-06-07")
            self.assertEqual(set(man["downloads"]), {"mac_arm64"})
            arm = man["downloads"]["mac_arm64"]
            self.assertEqual(arm["filename"], "SingWS-Pro-0.3.0-arm64-installer.dmg")
            self.assertIn("releases/download/v0.3.0/SingWS-Pro-0.3.0-arm64-installer.dmg", arm["url"])
            self.assertEqual(arm["sha256"], hashlib.sha256(b"A" * 1000).hexdigest())
            # JSON-serializable.
            json.dumps(man)

    def test_build_manifest_errors_on_missing_dmg(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                wm.build_manifest("0.9.9", Path(tmp))

    def test_build_manifest_requires_arm64(self):
        # SingWS 2.0 ships Apple Silicon only; a missing arm64 DMG is fatal.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "SingWS-2.0.0-x86_64-installer.dmg").write_bytes(b"B" * 2000)
            with self.assertRaises(SystemExit):
                wm.build_manifest("2.0.0", d)

    def test_manifest_never_advertises_intel(self):
        self.assertEqual([k for k, _l, _a in wm.ARCHES], ["mac_arm64"])


class UpdateManifestDefaultsTests(unittest.TestCase):
    CHANNEL_URL = "https://raw.githubusercontent.com/DanDemolition/SingWS-Pro/main/docs/release.json"
    LEGACY_URL = "https://raw.githubusercontent.com/DanDemolition/SingWS/main/docs/release.json"

    def _channel_ns(self):
        import ast
        tree = ast.parse(Path("0.2.18.1.py").read_text(encoding="utf-8"))
        names = {"LEGACY_1X_UPDATE_MANIFEST_URL", "DEFAULT_UPDATE_MANIFEST_URL",
                 "DEFAULT_UPDATE_REPO", "LEGACY_1X_UPDATE_REPO"}
        keep = [n for n in tree.body if (
            isinstance(n, ast.Assign) and any(getattr(t, "id", "") in names for t in n.targets)
        ) or (isinstance(n, ast.FunctionDef) and n.name in {
            "_effective_update_manifest_url", "_effective_update_repo"})]
        ns = {}
        exec(compile(ast.Module(body=keep, type_ignores=[]), "channel", "exec"), ns)
        return ns

    def test_pro_defaults_to_its_own_repo(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn(f'DEFAULT_UPDATE_MANIFEST_URL = "{self.CHANNEL_URL}"', source)
        self.assertIn('DEFAULT_UPDATE_REPO = "DanDemolition/SingWS-Pro"', source)
        self.assertIn('"auto_update_manifest_url": DEFAULT_UPDATE_MANIFEST_URL', source)
        self.assertIn('"auto_update_repo": DEFAULT_UPDATE_REPO', source)
        self.assertIn('manifest_url=_effective_update_manifest_url(self.settings.get("auto_update_manifest_url", ""))', source)
        self.assertEqual(source.count(self.LEGACY_URL), 1)
        self.assertEqual(source.count('"DanDemolition/SingWS"'), 1)

    def test_imported_1x_settings_migrate_to_pro(self):
        ns = self._channel_ns()
        url, repo = ns["_effective_update_manifest_url"], ns["_effective_update_repo"]
        self.assertEqual(url(""), self.CHANNEL_URL)
        self.assertEqual(url(self.LEGACY_URL), self.CHANNEL_URL)
        self.assertEqual(url("https://example.com/custom.json"), "https://example.com/custom.json")
        self.assertEqual(repo(""), "DanDemolition/SingWS-Pro")
        self.assertEqual(repo("DanDemolition/SingWS"), "DanDemolition/SingWS-Pro")
        self.assertEqual(repo("someone/fork"), "someone/fork")

    def test_manifest_uses_pro_repo_and_tag_urls(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "SingWS-Pro-2.0.0.1-arm64-installer.dmg").write_bytes(b"A" * 10)
            man = wm.build_manifest("2.0.0.1", d)
            self.assertEqual(man["channel"], "pro")
            self.assertEqual(man["repository"], "DanDemolition/SingWS-Pro")
            self.assertIn("github.com/DanDemolition/SingWS-Pro/releases/download/v2.0.0.1/", man["downloads"]["mac_arm64"]["url"])
            self.assertEqual(wm.MANIFEST_NAME, "release.json")

    def test_placeholder_manifest_offers_nothing(self):
        man = json.loads(Path("docs/release.json").read_text(encoding="utf-8"))
        self.assertEqual(man["repository"], "DanDemolition/SingWS-Pro")
        self.assertEqual(man["downloads"], {})

    def test_release_script_only_publishes_to_pro_repo(self):
        source = Path("release.sh").read_text(encoding="utf-8")
        self.assertLess(source.index('*"DanDemolition/SingWS-Pro"*'), source.index("gh auth status"))
        self.assertIn('"$NEW_VER" != 2.*', source)
        self.assertNotIn("DanDemolition/SingWS/", source)


class PackagingSpecTests(unittest.TestCase):
    def test_pro_installs_beside_1x(self):
        spec = Path("SingWS-arm64.spec").read_text(encoding="utf-8")
        self.assertIn("name='SingWS Pro.app'", spec)
        self.assertIn("bundle_identifier='com.singws.pro'", spec)
        self.assertNotIn("com.singws.app", spec)
        self.assertIn('"legacy_import.py"', spec)
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('APP_DIRNAME = "SingWSPro"', source)
        self.assertIn('Path.home() / APP_DIRNAME', source)
        self.assertNotIn('Path.home() / "SingWS")', source)
        self.assertIn('APP_DIRNAME = "SingWSPro"', Path("song_index.py").read_text(encoding="utf-8"))
        self.assertNotIn("/Applications/SingWS.app", Path("install_dev_singws.sh").read_text(encoding="utf-8"))

    def test_intel_packaging_removed_from_2_0(self):
        self.assertFalse(Path("SingWS-x86_64.spec").exists())
        self.assertFalse(Path("build_singws_mac_intel.sh").exists())
        self.assertNotIn("build_singws_mac_intel", Path("build_all.sh").read_text(encoding="utf-8"))

    def test_specs_bundle_no_gstreamer_and_exclude_gi(self):
        # GStreamer removal: specs must not set up a GST_REGISTRY, bundle the
        # plugin scanner/typelibs/framework, and must exclude gi so PyInstaller
        # cannot pull GStreamer back in transitively.
        for spec in ("SingWS-arm64.spec",):
            with self.subTest(spec=spec):
                source = Path(spec).read_text(encoding="utf-8")
                # Matched on the list contents, not the whole assignment: the
                # specs legitimately differ in how they build the rest of the
                # list (arm64 appends 'mpv' conditionally, x86_64 wraps the
                # expression in parens), and asserting the exact literal made
                # this fail on formatting rather than on gi being importable.
                self.assertIn("excludes=", source)
                self.assertIn("'gi', 'gi.repository'", source)
                self.assertNotIn("GST_REGISTRY", source)
                self.assertNotIn("gst-plugin-scanner", source)
                self.assertNotIn("gi_typelibs", source)
                self.assertNotIn('binaries.append((str(plug), "gst_plugins"))', source)

    def test_specs_do_not_bundle_legacy_media_executables(self):
        self.assertFalse(Path("singws_pyinstaller_runtime.py").exists())
        for spec in ("SingWS-arm64.spec",):
            source = Path(spec).read_text(encoding="utf-8")
            self.assertNotIn('for ff_binary in ("ffmpeg", "ffprobe")', source)
            self.assertIn("'libmpv_media_jobs'", source)
            self.assertIn('"libmpv_background_engine.py"', source)

    def test_apple_silicon_package_matches_permanent_native_stack(self):
        spec = Path("SingWS-arm64.spec").read_text(encoding="utf-8")
        build = Path("build_singws_mac_arm64.sh").read_text(encoding="utf-8")
        self.assertIn("target_arch='arm64'", spec)
        self.assertIn('"arm64" in result.stdout.split()', spec)
        self.assertIn("--runtime --require arm64", build)
        self.assertIn("--bundle \"$APP_PATH\" --require arm64", build)
        self.assertIn("'LSMinimumSystemVersion': '15.0'", spec)
        self.assertIn("--maximum 15.0", build)
        self.assertIn("libsingws_mpv_bridge.dylib", spec + build)
        self.assertIn("singws_libmpv.2.dylib", spec + build)
        self.assertNotIn("mpv_playback.py", spec + build)
        self.assertNotIn("python_karaoke_transport", spec + build)

    def test_release_specs_include_karafun_apple_events_authorization(self):
        entitlements = Path("SingWS.entitlements").read_text(encoding="utf-8")
        self.assertIn("com.apple.security.automation.apple-events", entitlements)
        for spec in ("SingWS-arm64.spec",):
            with self.subTest(spec=spec):
                source = Path(spec).read_text(encoding="utf-8")
                self.assertIn("NSAppleEventsUsageDescription", source)
                self.assertIn("entitlements_file=str(project_root / 'SingWS.entitlements')", source)

    def test_release_specs_bundle_requests_tls_support(self):
        for spec in ("SingWS-arm64.spec",):
            with self.subTest(spec=spec):
                source = Path(spec).read_text(encoding="utf-8")
                self.assertIn("project_root = Path(SPECPATH)", source)
                for module in ("'ssl'", "'_ssl'", "'_hashlib'", "'certifi'", "'urllib3.util.ssl_'"):
                    self.assertIn(module, source)
                for dylib in ('"libssl.3.dylib"', '"libcrypto.3.dylib"'):
                    self.assertIn(dylib, source)

    def test_release_specs_bundle_required_qt_plugins(self):
        required_groups = (
            '"platforms"',
            '"multimedia"',
            '"networkinformation"',
            '"tls"',
        )
        for spec in ("SingWS-arm64.spec",):
            with self.subTest(spec=spec):
                source = Path(spec).read_text(encoding="utf-8")
                self.assertIn('qt_plugins_root = (', source)
                self.assertIn('f"PyQt6/Qt6/plugins/{plugin_group}"', source)
                self.assertIn("Required Qt plugin group is missing", source)
                for group in required_groups:
                    self.assertIn(group, source)

    def test_release_verifier_requires_cocoa_platform_plugin(self):
        verifier = Path("tools/verify_macos_arch.py").read_text(encoding="utf-8")
        self.assertIn('"libqcocoa.dylib"', verifier)

if __name__ == "__main__":
    unittest.main()
