import ast
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import transition_events
import transition_observer as to
from transition_analysis import TransitionAnalysis
from transition_cues import derive_cues
from transition_replay import check_scenario, load_scenarios

FIXTURES = Path("test_fixtures/transition_replay")


def cues(**kw):
    base = dict(path="/x", mtime=1, size=1, media_kind="karaoke", duration=200.0,
                audio_start=4.0, audio_end=180.0, visual_end=178.0, visual_confidence=0.95)
    base.update(kw)
    return derive_cues(TransitionAnalysis(**base), now=0)


class ReplayFixtureTests(unittest.TestCase):
    def test_all_replay_fixtures(self):
        scenarios = load_scenarios(FIXTURES)
        self.assertGreaterEqual(len(scenarios), 8)
        for path, scenario in scenarios:
            with self.subTest(fixture=path.name):
                ok, problems, emitted = check_scenario(scenario)
                self.assertTrue(ok, f"{scenario.get('name')}: {problems}\n{emitted[-5:]}")


class ObserverUnitTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.obs = to.TransitionObserver(lambda kind, **kw: self.events.append((kind, kw)))

    def test_each_proposal_emitted_once(self):
        self.obs.song_started(generation=1, track="t", cues=cues(), t_mono=0.0)
        for i in range(0, 400):
            self.obs.position(generation=1, playhead=i * 0.5, t_mono=i * 0.5)
        actions = [kw["action"] for k, kw in self.events if k == "observer_proposal"]
        self.assertEqual(actions, ["bgm_fade_in", "end_karaoke"])

    def test_off_mode_emits_nothing(self):
        self.obs.set_mode("off")
        self.obs.song_started(generation=1, track="t", cues=cues(), t_mono=0.0)
        self.obs.position(generation=1, playhead=199.0, t_mono=199.0)
        self.obs.song_ended(generation=1, trigger="eos", t_mono=200.0)
        self.assertEqual(self.events, [])
        self.obs.set_mode("nonsense")
        self.assertEqual(self.obs.mode, "off")

    def test_backwards_clock_ignored(self):
        self.obs.song_started(generation=1, track="t", cues=cues(), t_mono=100.0)
        self.obs.position(generation=1, playhead=199.0, t_mono=50.0)
        self.assertEqual([k for k, _ in self.events], [])
        self.assertEqual(self.obs.stats["stale_ignored"], 1)

    def test_sink_failure_never_raises(self):
        obs = to.TransitionObserver(mock.Mock(side_effect=RuntimeError("disk")))
        obs.song_started(generation=1, track="t", cues=None, t_mono=0.0)
        obs.song_ended(generation=1, trigger="eos", t_mono=1.0)
        self.assertEqual(obs.stats["sink_errors"], 2)

    def test_low_confidence_cues_never_proposed(self):
        low = cues(visual_confidence=0.5)
        self.obs.song_started(generation=1, track="t", cues=low, t_mono=0.0)
        for i in range(201):
            self.obs.position(generation=1, playhead=float(i), t_mono=float(i))
        actions = [kw["action"] for k, kw in self.events if k == "observer_proposal"]
        self.assertNotIn("end_karaoke", actions)


class SoundEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.obs = to.TransitionObserver(lambda kind, **kw: self.events.append((kind, kw)))
        self.obs.song_started(generation=1, track="t", cues=cues(), t_mono=0.0)

    def test_listening_window_only_near_ending(self):
        self.assertFalse(self.obs.listening_window(generation=1, playhead=100.0))
        self.assertTrue(self.obs.listening_window(generation=1, playhead=170.0))
        self.assertFalse(self.obs.listening_window(generation=2, playhead=170.0))
        self.obs.seeked(generation=1, playhead=170.0, t_mono=1.0)
        self.assertFalse(self.obs.listening_window(generation=1, playhead=175.0))

    def test_low_confidence_vocal_does_not_count(self):
        for i in range(5):
            self.obs.sound(generation=1, result="active_vocal", confidence=0.4, t_mono=float(i))
        self.assertEqual([k for k, _ in self.events], [])

    def test_stale_generation_sound_ignored(self):
        for i in range(5):
            self.obs.sound(generation=0, result="active_vocal", confidence=0.9, t_mono=float(i))
        self.assertEqual(self.events, [])


class NoPlaybackAuthorityTests(unittest.TestCase):
    def test_observer_module_imports_nothing_that_can_play(self):
        tree = ast.parse(Path("transition_observer.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertTrue(imported <= {"__future__", "dataclasses", "typing", "transition_cues"}, imported)

    def test_observer_public_api_is_inputs_only(self):
        public = {n for n in dir(to.TransitionObserver) if not n.startswith("_")}
        self.assertEqual(public, {"mode", "set_mode", "song_started", "position", "seeked",
                                  "bgm_started", "song_ended", "listening_window", "sound",
                                  "sound_unavailable", "mic", "mic_unavailable", "mic_mode",
                                  "set_mic_mode"})
        banned = ("stop", "start(", "seek(", "fade", "play", "schedule", "singleShot", "QTimer")
        source = Path("transition_observer.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in source.splitlines() if not l.strip().startswith(("#", '"""')))
        for word in ("singleShot", "QTimer", ".stop(", ".seek(", "fade_in(", "fade_out("):
            self.assertNotIn(word, code)

    def test_app_feed_never_reaches_playback_objects(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        code = "\n\n".join(ast.unparse(funcs[n]) for n in ("_ia_observer_active", "_ia_observe"))
        fake_monitor = mock.Mock()
        fake_monitor.latest.side_effect = [{"seq": i, "result": "active_vocal", "confidence": 0.9,
                                            "t_mono": 1.0 + i, "model": "fake"} for i in range(1, 400)]
        recorded = []
        observer = to.TransitionObserver(lambda kind, **kw: recorded.append(kind))
        rec = mock.Mock(enabled=True)
        ns = {
            "time": __import__("time"),
            "transition_events": mock.Mock(recorder=lambda: rec, track_id=lambda p: "id"),
            "transition_observer": to,
            "transition_cues": __import__("transition_cues"),
            "transition_analysis_cached": lambda path: TransitionAnalysis(
                path=path, mtime=1, size=1, media_kind="karaoke", duration=200.0,
                audio_start=4.0, audio_end=180.0, visual_end=178.0, visual_confidence=0.95),
            "_IA_OBSERVER": observer,
            "_IA_SOUND": {"enabled": True, "monitor": fake_monitor, "last_seq": 0},
            "_ia_sound_monitor": lambda: fake_monitor,
        }
        exec(compile(code, "observe", "exec"), ns)
        owner = mock.Mock()   # every attribute/method is a spy
        owner._ia_karaoke_generation = 1
        owner._current_karaoke_audio_path = "/lib/song.cdg"
        feed = ns["_ia_observe"]
        feed(owner, "start", playhead=0.0, video_path=None)
        for p in range(0, 200):
            feed(owner, "position", playhead=float(p))
        feed(owner, "seek", playhead=10.0)
        feed(owner, "bgm")
        feed(owner, "end", trigger="eos")
        self.assertIn("observer_proposal", recorded)
        self.assertIn("observer_evidence", recorded)
        self.assertTrue(fake_monitor.arm.called)
        self.assertTrue(fake_monitor.disarm.called)
        self.assertEqual(owner.method_calls, [])   # no playback method was touched
        for name in ("karaoke_transport", "bg_music", "stop_playback", "_handle_media_end_safe"):
            self.assertFalse(getattr(owner, name).called, name)

    def test_app_observer_calls_are_bare_statements_and_default_mode(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('"transition_observer_mode": "observer"', source)
        tree = ast.parse(source)
        exprs = {id(s.value) for s in ast.walk(tree) if isinstance(s, ast.Expr)}
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == "_ia_observe"]
        self.assertGreaterEqual(len(calls), 6)
        for call in calls:
            if id(call) in exprs:
                continue
            self.fail(ast.unparse(call))

    def test_event_kinds_registered(self):
        self.assertIn("observer_proposal", transition_events.EVENT_KINDS)
        self.assertIn("observer_compare", transition_events.EVENT_KINDS)


if __name__ == "__main__":
    unittest.main()
