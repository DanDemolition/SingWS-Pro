import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import sound_monitor as sm

FAKE = Path("test_fixtures/sound_helper/fake_helper.py").resolve()


def popen_for(mode, launches=None):
    def _popen(args, **kw):
        if launches is not None:
            launches.append(args)
        env = dict(os.environ, FAKE_MODE=mode)
        return subprocess.Popen([sys.executable, str(FAKE)], env=env, **kw)
    return _popen


def wait_for(pred, timeout=6.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


class SoundMonitorTests(unittest.TestCase):
    def make(self, mode, **kw):
        health = []
        launches = []
        mon = sm.SoundMonitor(FAKE, popen=popen_for(mode, launches), target_pid=4242,
                              on_health=lambda s, r: health.append((s, r)),
                              heartbeat_timeout_s=kw.pop("hb", 1.0), **kw)
        self.addCleanup(mon.shutdown)
        return mon, health, launches

    def test_normal_run_delivers_fresh_scored_windows_with_increasing_seq(self):
        mon, health, launches = self.make("normal")
        mon.arm()
        self.assertTrue(wait_for(lambda: mon.latest() is not None))
        first = mon.latest()
        self.assertEqual(mon.state, sm.STATE_RUNNING)
        self.assertEqual(first["result"], "active_vocal")
        self.assertEqual(first["model"], "fake.v1")
        self.assertTrue(wait_for(lambda: (mon.latest() or {}).get("seq", 0) > first["seq"]))
        self.assertEqual(launches[0][1:], ["--tap-pid", "4242"])
        mon.arm()   # idempotent while armed
        self.assertEqual(len(launches), 1)

    def test_disarm_stops_helper_and_clears_evidence(self):
        mon, _h, _l = self.make("normal")
        mon.arm()
        self.assertTrue(wait_for(lambda: mon.latest() is not None))
        proc = mon._proc
        t0 = time.monotonic()
        mon.disarm()
        self.assertLess(time.monotonic() - t0, 2.5)
        self.assertIsNotNone(proc.poll())
        self.assertIsNone(mon.latest())
        self.assertEqual(mon.state, sm.STATE_OFF)

    def test_stale_window_not_returned(self):
        clock = [1000.0]
        mon = sm.SoundMonitor(FAKE, popen=popen_for("normal"), clock=lambda: clock[0], window_ttl_s=2.0,
                              heartbeat_timeout_s=1e9)
        self.addCleanup(mon.shutdown)
        mon.arm()
        self.assertTrue(wait_for(lambda: mon.latest() is not None))
        clock[0] += 10.0
        self.assertIsNone(mon.latest())

    def test_repeated_crashes_bypass(self):
        mon, health, launches = self.make("crash", max_failures=3)
        mon.arm()
        self.assertTrue(wait_for(lambda: mon.state == sm.STATE_BYPASSED))
        self.assertEqual(len(launches), 3)
        self.assertTrue(mon.reason.startswith("unhealthy:"))
        mon.arm()
        time.sleep(0.2)
        self.assertEqual(len(launches), 3)   # stays bypassed
        self.assertIsNone(mon.latest())

    def test_error_line_counts_as_failure(self):
        mon, health, _l = self.make("error", max_failures=1)
        mon.arm()
        self.assertTrue(wait_for(lambda: mon.state == sm.STATE_BYPASSED))
        self.assertIn("helper_error", mon.reason)

    def test_missing_heartbeat_is_killed(self):
        mon, health, launches = self.make("silent", max_failures=1, hb=0.8)
        mon.arm()
        self.assertTrue(wait_for(lambda: mon.state == sm.STATE_BYPASSED, timeout=8))

    def test_malformed_flood_fails(self):
        mon, _h, _l = self.make("malformed", max_failures=1, hb=30)
        mon.arm()
        self.assertTrue(wait_for(lambda: mon.state == sm.STATE_BYPASSED))
        self.assertIn("malformed_output", mon.reason)

    def test_rss_over_budget_fails(self):
        mon, _h, _l = self.make("rss", max_failures=1, hb=30, rss_budget_mb=200)
        mon.arm()
        self.assertTrue(wait_for(lambda: mon.state == sm.STATE_BYPASSED))
        self.assertIn("rss_over_budget", mon.reason)

    def test_missing_helper_bypasses_without_launch(self):
        launches = []
        mon = sm.SoundMonitor("/nonexistent/SingWSSoundHelper", popen=popen_for("normal", launches))
        mon.arm()
        self.assertEqual(mon.state, sm.STATE_BYPASSED)
        self.assertEqual(mon.reason, "helper_missing")
        self.assertEqual(launches, [])

    def test_shutdown_prevents_rearm(self):
        mon, _h, launches = self.make("normal")
        mon.shutdown()
        mon.arm()
        self.assertEqual(launches, [])

    def test_launch_failure_never_raises(self):
        def boom(*a, **k):
            raise OSError("no exec")
        mon = sm.SoundMonitor(FAKE, popen=boom, max_failures=2)
        mon.arm()
        self.assertTrue(wait_for(lambda: mon.state == sm.STATE_BYPASSED))

    def test_gui_methods_are_nonblocking(self):
        mon, _h, _l = self.make("silent", hb=30)
        t0 = time.perf_counter()
        for _ in range(1000):
            mon.arm(); mon.latest()
        self.assertLess(time.perf_counter() - t0, 0.5)


class AppWiringContractTests(unittest.TestCase):
    def setUp(self):
        self.source = Path("0.2.18.1.py").read_text(encoding="utf-8")

    def test_default_off_and_bundle_permission_text(self):
        self.assertIn('"ia_sound_classifier_enabled": False', self.source)
        spec = Path("SingWS-arm64.spec").read_text(encoding="utf-8")
        self.assertIn("NSAudioCaptureUsageDescription", spec)
        self.assertIn('"sound_monitor.py"', spec)

    def test_sound_monitor_has_no_playback_access(self):
        import ast
        tree = ast.parse(Path("sound_monitor.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names |= {(getattr(node, "module", None) or a.name).split(".")[0] for a in node.names}
        self.assertTrue(names <= {"__future__", "collections", "json", "os", "subprocess", "threading",
                                  "time", "pathlib", "typing", "sound_classes", "psutil"}, names)


if __name__ == "__main__":
    unittest.main()
