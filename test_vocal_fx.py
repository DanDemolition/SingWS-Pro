import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

import sound_monitor as sm
import vocal_fx

FAKE = Path("test_fixtures/sound_helper/fake_vfx.py").resolve()


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


class EnablementTests(unittest.TestCase):
    """Effects are opt-in. Live-show rule 10."""

    def test_disabled_by_default_and_for_bad_settings(self):
        for settings in (None, {}, {"vfx_enabled": False}, "nonsense", []):
            self.assertFalse(vocal_fx.is_enabled(settings), settings)

    def test_enabled_only_when_explicitly_true(self):
        self.assertTrue(vocal_fx.is_enabled({"vfx_enabled": True}))

    def test_channels_reject_nonsense(self):
        got = vocal_fx.channels_from_settings({"vfx_channels": [1, "2", 0, 99999, "x", None, 3]})
        self.assertEqual(got, [1, 2, 3])


class CommandTests(unittest.TestCase):
    def make(self, mode="normal", **kw):
        launches = []
        mon = vocal_fx.VocalFXMonitor(
            FAKE, device_uid="UID-1", channels=[1, 2], frames=64, effect="reverb",
            popen=popen_for(mode, launches), **kw)
        self.addCleanup(mon.shutdown)
        return mon, launches

    def test_command_carries_device_channels_frames_and_effect(self):
        mon, _ = self.make()
        cmd = mon._command()
        self.assertIn("--device", cmd)
        self.assertEqual(cmd[cmd.index("--device") + 1], "UID-1")
        self.assertEqual(cmd[cmd.index("--channels") + 1], "1,2")
        self.assertEqual(cmd[cmd.index("--frames") + 1], "64")
        self.assertEqual(cmd[cmd.index("--effect") + 1], "reverb")

    def test_unknown_effect_falls_back_to_passthrough(self):
        mon = vocal_fx.VocalFXMonitor(FAKE, device_uid="U", channels=[1],
                                      effect="wormhole", popen=popen_for("normal"))
        self.addCleanup(mon.shutdown)
        self.assertEqual(mon._command()[mon._command().index("--effect") + 1], "none")


class ControlTests(unittest.TestCase):
    def make(self, mode="normal"):
        mon = vocal_fx.VocalFXMonitor(FAKE, device_uid="U", channels=[1, 2],
                                      popen=popen_for(mode))
        self.addCleanup(mon.shutdown)
        mon.arm()
        self.assertTrue(wait_for(lambda: mon.hello), "helper never said hello")
        return mon

    def test_hello_exposes_estimated_latency(self):
        mon = self.make()
        self.assertAlmostEqual(mon.estimated_latency_ms, 8.4, places=3)
        self.assertEqual(mon.hello["sample_rate"], 48000)

    def test_stats_rows_are_captured(self):
        mon = self.make()
        self.assertTrue(wait_for(lambda: mon.helper_stats is not None))
        self.assertIn("frames", mon.helper_stats)

    def test_set_params_clamps_and_sends(self):
        mon = self.make()
        self.assertTrue(mon.set_params(9.0, True))     # above the 4.0 ceiling
        self.assertEqual(mon._wet, 4.0)
        self.assertTrue(mon.set_params(-3.0, True))
        self.assertEqual(mon._wet, 0.0)
        self.assertTrue(mon.set_params("nonsense", True))
        self.assertEqual(mon._wet, 1.0)

    def test_bypass_clears_enabled(self):
        mon = self.make()
        mon.set_params(1.0, True)
        self.assertTrue(mon._on)
        self.assertTrue(mon.bypass())
        self.assertFalse(mon._on)

    def test_restarted_helper_is_restored_to_operator_intent(self):
        """A fresh helper starts bypassed; it must not silently stay that way
        after a transient restart when the operator had effects on."""
        mon = self.make()
        mon.set_params(0.75, True)
        mon._set_state(sm.STATE_RUNNING, "hello")
        self.assertTrue(mon._on)
        self.assertEqual(mon._wet, 0.75)

    def test_send_on_dead_pipe_reports_false_instead_of_raising(self):
        mon = vocal_fx.VocalFXMonitor(FAKE, device_uid="U", channels=[1],
                                      popen=popen_for("normal"))
        self.addCleanup(mon.shutdown)
        self.assertFalse(mon.set_params(1.0, True))   # never armed: no process
        self.assertFalse(mon.bypass())


class SupervisionTests(unittest.TestCase):
    def make(self, mode, **kw):
        health = []
        mon = vocal_fx.VocalFXMonitor(FAKE, device_uid="U", channels=[1],
                                      popen=popen_for(mode),
                                      on_health=lambda s, r: health.append((s, r)), **kw)
        self.addCleanup(mon.shutdown)
        return mon, health

    def test_device_loss_is_transient_and_never_bypasses(self):
        mon, _ = self.make("device_lost", max_failures=2)
        mon.arm()
        time.sleep(2.0)
        self.assertNotEqual(mon.state, sm.STATE_BYPASSED)
        self.assertEqual(mon._failures, 0, "a pulled cable must not count as a failure")

    def test_format_change_is_transient(self):
        mon, _ = self.make("format_changed", max_failures=2)
        mon.arm()
        time.sleep(2.0)
        self.assertEqual(mon._failures, 0)

    def test_repeated_crashes_bypass_for_the_session(self):
        mon, _ = self.make("crash", max_failures=2)
        mon.arm()
        self.assertTrue(wait_for(lambda: mon.state == sm.STATE_BYPASSED, timeout=10.0),
                        "crashing helper should bypass")


class IsolationTests(unittest.TestCase):
    """The effects path must not be able to touch playback, and must not share
    the transition-analysis supervisor instance (Prompt 10 gate)."""

    def test_module_never_imports_playback_or_transport(self):
        src = Path("vocal_fx.py").read_text()
        for banned in ("mpv_karaoke_transport", "bass_background_engine",
                       "playback_providers", "transition_observer", "0.2.18.1"):
            self.assertNotIn(banned, src, f"vocal_fx must not reach {banned}")

    def test_monitor_is_its_own_instance_not_a_shared_one(self):
        a = vocal_fx.VocalFXMonitor(FAKE, device_uid="U", channels=[1], popen=popen_for("normal"))
        b = vocal_fx.VocalFXMonitor(FAKE, device_uid="U", channels=[1], popen=popen_for("normal"))
        self.addCleanup(a.shutdown)
        self.addCleanup(b.shutdown)
        a._failures = 3
        self.assertEqual(b._failures, 0, "failure counters must not be shared")


if __name__ == "__main__":
    unittest.main()
