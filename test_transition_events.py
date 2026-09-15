import ast
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import transition_events as te


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        self.t += 0.5
        return self.t


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.rec = te.EventRecorder(self.dir, capacity=8, flush_interval_s=60,
                                    clock=FakeClock(), wall=lambda: 1789500000.0)

    def tearDown(self):
        self.rec.close()
        self._tmp.cleanup()

    def lines(self):
        files = list(self.dir.glob("transition_events_*.jsonl"))
        return [json.loads(l) for f in files for l in f.read_text().splitlines()]

    def test_disabled_by_default_records_nothing_and_starts_no_thread(self):
        self.assertFalse(self.rec.enabled)
        self.assertFalse(self.rec.record("karaoke_start"))
        self.assertIsNone(self.rec._thread)
        self.assertEqual(self.rec.flush(), 0)
        self.assertEqual(self.lines(), [])

    def test_record_does_no_io_on_caller_thread(self):
        self.rec.configure(enabled=True)
        with mock.patch("builtins.open", side_effect=AssertionError("I/O on hot path")), \
             mock.patch("json.dumps", side_effect=AssertionError("formatting on hot path")):
            for _ in range(20):
                self.assertTrue(self.rec.record("karaoke_eos", playhead_s=1.0))

    def test_flush_writes_ordered_versioned_jsonl_and_filters_data(self):
        self.rec.configure(enabled=True)
        self.rec.record("karaoke_start", generation=3, track=te.track_id("/music/a.cdg"),
                        media="cdg", playhead_s=0.0, mode="cdg", bad=object(), nested={"x": 1})
        self.rec.record("media_end", generation=3, trigger="eos")
        self.assertEqual(self.rec.flush(), 2)
        rows = self.lines()
        self.assertEqual([r["kind"] for r in rows], ["karaoke_start", "media_end"])
        self.assertTrue(all(r["v"] == te.SCHEMA_VERSION for r in rows))
        self.assertLess(rows[0]["t_mono"], rows[1]["t_mono"])
        self.assertEqual(rows[0]["data"], {"mode": "cdg"})
        self.assertNotIn("/music", json.dumps(rows))

    def test_unknown_kind_rejected(self):
        self.rec.configure(enabled=True)
        self.assertFalse(self.rec.record("start_bgm_now"))

    def test_overflow_keeps_newest_and_reports_drops(self):
        self.rec.configure(enabled=True)
        for i in range(12):
            self.rec.record("manual_seek", playhead_s=float(i))
        self.rec.flush()
        rows = self.lines()
        seeks = [r["playhead_s"] for r in rows if r["kind"] == "manual_seek"]
        self.assertEqual(seeks, [float(i) for i in range(4, 12)])
        self.assertEqual(rows[-1]["kind"], "recorder_dropped")
        self.assertEqual(rows[-1]["data"]["count"], 4)

    def test_close_flushes_and_stops_writer(self):
        rec = te.EventRecorder(self.dir, capacity=8, flush_interval_s=60)
        rec.configure(enabled=True)
        thread = rec._thread
        rec.record("karaoke_eos")
        rec.close()
        self.assertFalse(thread.is_alive())
        self.assertFalse(rec.enabled)
        self.assertEqual([r["kind"] for r in self.lines()], ["karaoke_eos"])
        self.assertFalse(rec.record("karaoke_eos"))

    def test_write_failure_never_raises(self):
        self.rec.configure(enabled=True, log_dir=self.dir / "file")
        (self.dir / "file").write_text("not a dir")
        self.rec.record("karaoke_eos")
        self.assertEqual(self.rec.flush(), 1)

    def test_concurrent_producers_do_not_lose_events_within_capacity(self):
        rec = te.EventRecorder(self.dir, capacity=4000, flush_interval_s=60)
        rec.configure(enabled=True)
        def burst():
            for _ in range(500):
                rec.record("gui_stall", ms=1.0)
        threads = [threading.Thread(target=burst) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        rec.close()
        self.assertEqual(len([r for r in self.lines() if r["kind"] == "gui_stall"]), 2000)


class AppIntegrationContractTests(unittest.TestCase):
    """Instrumentation must stay passive: observe, never decide."""

    @classmethod
    def setUpClass(cls):
        cls.source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _method(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        self.fail(name)

    def test_default_off(self):
        self.assertIn('"ia_instrumentation_enabled": False', self.source)

    def test_helper_only_talks_to_recorder_and_swallows_errors(self):
        helper = self._method("_ia_record")
        calls = {ast.unparse(c.func) for c in ast.walk(helper) if isinstance(c, ast.Call)}
        self.assertIsInstance(helper.body[1], ast.Try)  # everything after the docstring is guarded
        allowed = {"transition_events.recorder", "rec.record", "transition_events.track_id",
                   "int", "getattr"}
        self.assertTrue(calls <= allowed, calls - allowed)
        self.assertTrue(any(isinstance(n, ast.Try) for n in ast.walk(helper)))

    def test_instrumentation_calls_are_bare_statements(self):
        # A recorder result must never feed a condition or assignment.
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in (
                    "_ia_record", "transition_events.record"):
                parent_ok = False
                for stmt in ast.walk(self.tree):
                    if isinstance(stmt, ast.Expr) and stmt.value is node:
                        parent_ok = True
                        break
                self.assertTrue(parent_ok, ast.unparse(node))

    def test_expected_call_sites_present(self):
        for kind in ("karaoke_start", "karaoke_eos", "media_end", "early_end_trim",
                     "eos_fallback", "stall_fallback", "bgm_prestart",
                     "bgm_prefire_verified", "bgm_fade_in", "manual_stop",
                     "manual_seek", "gui_stall"):
            self.assertIn(f'"{kind}"', self.source, kind)
            self.assertIn(kind, te.EVENT_KINDS)

    def test_helper_is_module_level_so_any_host_object_works(self):
        top = {n.name for n in self.tree.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("_ia_record", top)
        self.assertNotIn("self._ia_record(", self.source)

    def test_shutdown_closes_recorder(self):
        body = ast.unparse(self._method("_on_app_about_to_quit"))
        self.assertIn("transition_events.recorder().close()", body)


if __name__ == "__main__":
    unittest.main()
