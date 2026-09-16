import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import mic_activity as ma
import mic_config as mc
import mic_monitor as mm
import sound_monitor as sm

FAKE = Path("test_fixtures/sound_helper/fake_mic_meter.py").resolve()


def wait_for(pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


class MicConfigTests(unittest.TestCase):
    def test_presets(self):
        ui = mc.MicInputConfig.from_preset("ui24r")
        self.assertEqual(ui.roles, {"singer1": 1, "singer2": 2, "host": 3})
        self.assertTrue(ui.separate_singers)
        sig = mc.MicInputConfig.from_preset("signature10")
        self.assertEqual(sig.roles, {"singers": 1, "host": 2})
        self.assertFalse(sig.separate_singers)

    def test_problems(self):
        cfg = mc.MicInputConfig.from_preset("ui24r")
        self.assertIn("no_device_selected", cfg.problems())
        cfg.device_uid = "X"
        self.assertEqual(cfg.problems(input_channels=32), [])
        self.assertIn("host_channel_3_missing_on_device", cfg.problems(input_channels=2))
        cfg.roles["host"] = 1
        self.assertTrue(any("channel_1_used_by" in p for p in cfg.problems()))
        cfg.roles["singers"] = 4
        self.assertIn("combined_and_separate_singers", cfg.problems())

    def test_settings_round_trip_rejects_garbage(self):
        cfg = mc.MicInputConfig.from_settings({"preset": "custom", "device_uid": "U",
                                               "roles": {"host": "3", "bogus": 1, "singer1": -2, "singer2": "x"},
                                               "noise_floor_db": {"host": -55, "singer1": 40}})
        self.assertEqual(cfg.roles, {"host": 3})
        self.assertEqual(cfg.noise_floor_db, {"host": -55.0})
        self.assertEqual(mc.MicInputConfig.from_settings(cfg.to_settings()).to_settings(), cfg.to_settings())
        self.assertEqual(mc.MicInputConfig.from_settings(None).preset, "ui24r")


class ActivityTests(unittest.TestCase):
    def feed(self, ch, values, start=0.0, step=0.1, peak=-20.0):
        t = start
        states = []
        for v in values:
            states.append(ch.update(v, peak, t))
            t += step
        return states, t

    def test_single_loud_report_does_not_open(self):
        ch = ma.ChannelActivity(floor_db=-60)
        states, _ = self.feed(ch, [-20, -70, -70])
        self.assertTrue(all(s == "silent" for s in states))

    def test_open_sustain_and_close_with_hysteresis(self):
        ch = ma.ChannelActivity(floor_db=-60)
        states, t = self.feed(ch, [-30] * 5)
        self.assertEqual(states[-1], "active")
        states, t = self.feed(ch, [-30, -45, -30, -45] * 5, start=t)   # varied level
        self.assertEqual(states[-1], "sustained")
        # short dip below close threshold keeps it open
        states, t = self.feed(ch, [-55, -55, -30], start=t)
        self.assertNotEqual(states[-1], "silent")
        states, t = self.feed(ch, [-58] * 8, start=t)
        self.assertEqual(states[-1], "silent")

    def test_constant_hum_is_suspect_noise(self):
        ch = ma.ChannelActivity(floor_db=-60)
        states, _ = self.feed(ch, [-30.0] * 70)
        self.assertEqual(states[-1], "suspect_noise")

    def test_clipping_and_stale(self):
        ch = ma.ChannelActivity(floor_db=-60)
        self.assertEqual(ch.update(-10, -0.5, 0.0), "clipping")
        self.assertEqual(ch.check_stale(5.0), "stale")

    def test_calibrate_floor(self):
        self.assertEqual(ma.calibrate_floor([-65, -64, -66, -40, -66]), -65.0)
        self.assertEqual(ma.calibrate_floor([]), ma.DEFAULT_FLOOR_DB)
        self.assertEqual(ma.calibrate_floor([-5, -5]), -20.0)

    def test_mic_activity_roles(self):
        act = ma.MicActivity({"singer1": 1, "host": 3})
        for i in range(10):
            snap = act.feed({"ch": {"1": {"rms_db": -25, "peak_db": -10}, "3": {"rms_db": -70, "peak_db": -60}}}, i * 0.1)
        self.assertEqual(snap["singer1"]["state"], "active")
        self.assertEqual(snap["host"]["state"], "silent")
        self.assertEqual(act.snapshot(10.0)["host"]["state"], "stale")


class MicMonitorTests(unittest.TestCase):
    def make(self, mode, **kw):
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        os.unlink(tmp.name)
        env = dict(os.environ, FAKE_MODE=mode, FAKE_STATE=tmp.name)
        launches = []

        def popen(args, **k):
            launches.append(args)
            return subprocess.Popen([sys.executable, str(FAKE), *args[1:]], env=env, **k)

        cfg = mc.MicInputConfig.from_preset("ui24r")
        cfg.device_uid = "UI24R-UID"
        mon = mm.MicMonitor(FAKE, cfg, popen=popen, **kw)
        mon._retry_delay_s = 0.2
        self.addCleanup(mon.shutdown)
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.unlink(tmp.name))
        return mon, launches

    def test_levels_become_role_activity(self):
        mon, launches = self.make("normal")
        mon.arm()
        self.assertTrue(wait_for(lambda: (mon.latest() or {}).get("roles", {}).get("singer1", {}).get("state") in ("active", "sustained")))
        snap = mon.latest()
        self.assertEqual(snap["roles"]["host"]["state"], "silent")
        self.assertEqual(snap["device"], "Soundcraft Ui24R")
        self.assertEqual(launches[0][1:], ["--device", "UI24R-UID", "--channels", "1,2,3"])

    def test_unplug_is_transient_and_resumes(self):
        mon, launches = self.make("lost_once", max_failures=1)
        mon.arm()
        self.assertTrue(wait_for(lambda: len(launches) >= 2 and mon.latest() is not None))
        self.assertNotEqual(mon.state, sm.STATE_BYPASSED)

    def test_evidence_dropped_while_waiting(self):
        mon, launches = self.make("format_change", max_failures=1)
        mon.arm()
        self.assertTrue(wait_for(lambda: len(launches) >= 2))
        self.assertIsNone(mon.latest())
        self.assertNotEqual(mon.state, sm.STATE_BYPASSED)

    def test_bad_config_never_launches(self):
        cfg = mc.MicInputConfig.from_preset("ui24r")   # no device
        launches = []
        mon = mm.MicMonitor(FAKE, cfg, popen=lambda a, **k: launches.append(a))
        mon.arm()
        self.assertEqual(launches, [])
        self.assertTrue(mon.reason.startswith("config:"))

    def test_list_devices(self):
        devices = mm.list_input_devices(FAKE, run=lambda a, **k: subprocess.run([sys.executable, str(FAKE), *a[1:]], **k))
        self.assertEqual(devices[0]["name"], "Soundcraft Ui24R")
        self.assertEqual(mm.list_input_devices("/nope"), [])


if __name__ == "__main__":
    unittest.main()


class MicSafetyContractTests(unittest.TestCase):
    def test_defaults_off_and_permissions_declared(self):
        app = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('"ia_mic_awareness_enabled": False', app)
        spec = Path("SingWS-arm64.spec").read_text(encoding="utf-8")
        self.assertIn("NSMicrophoneUsageDescription", spec)
        self.assertIn("com.apple.security.device.audio-input", Path("SingWS.entitlements").read_text())

    def test_mic_code_has_no_playback_or_recording_path(self):
        import ast
        allowed = {"__future__", "dataclasses", "typing", "statistics", "collections", "json",
                   "subprocess", "threading", "pathlib", "mic_activity", "mic_config", "mic_monitor",
                   "sound_monitor", "PyQt6"}
        for name in ("mic_config.py", "mic_activity.py", "mic_monitor.py", "mic_diagnostics_dialog.py"):
            tree = ast.parse(Path(name).read_text(encoding="utf-8"))
            mods = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    mods.add((node.module or "").split(".")[0])
            self.assertTrue(mods <= allowed, (name, mods - allowed))
        swift = Path("native/sound_helper/SingWSMicMeter.swift").read_text(encoding="utf-8")
        for banned in ("AVAudioFile", "ExtAudioFile", "write(to", "AudioFileCreate"):
            self.assertNotIn(banned, swift)

    def test_dialog_does_not_start_meters_on_open(self):
        import ast
        tree = ast.parse(Path("mic_diagnostics_dialog.py").read_text(encoding="utf-8"))
        init = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        self.assertNotIn(".arm(", ast.unparse(init))
        self.assertNotIn("_toggle_meters()", ast.unparse(init))

    def test_mic_feeds_observer_only(self):
        import ast
        app = Path("0.2.18.1.py").read_text(encoding="utf-8")
        tree = ast.parse(app)
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name in ("_ia_mic_tick", "_ia_mic_configure"):
            calls = {ast.unparse(c.func) for c in ast.walk(funcs[name]) if isinstance(c, ast.Call)}
            for call in calls:
                self.assertFalse(any(w in call for w in ("transport", "bg_music", "fade", "seek", "stop_playback",
                                                          "_handle_media_end")), (name, call))
        self.assertIn('"ia_mic_observer_mode": "singer_protection_host_ducking"', app)
