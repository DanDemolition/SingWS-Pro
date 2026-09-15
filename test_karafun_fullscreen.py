import unittest
from unittest.mock import patch

from karafun_fullscreen import ensure_renderer_fullscreen


class Host:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.scripts = []
        self.clicks = []

    def _karafun_run_window_script(self, lines, on_complete, **kwargs):
        self.scripts.append(lines)
        on_complete(next(self.replies))
        return True

    def _macos_native_double_click(self, *point):
        self.clicks.append(point)
        return True

    def _run_on_ui_thread(self, callback):
        callback()


class InlineThread:
    def __init__(self, *args, target=None, **kwargs):
        self.target = target

    def start(self):
        self.target()


class InlineTimer:
    """Fire the delayed re-verification immediately.

    Patching only threading.Thread broke threading.Timer, whose __init__ calls
    the (patched) module-level Thread.__init__ -> "Thread.__init__() not called".
    """

    def __init__(self, _interval, function, args=None, kwargs=None):
        self.function = function
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.daemon = False

    def start(self):
        self.function(*self.args, **self.kwargs)


class FullscreenTests(unittest.TestCase):
    def setUp(self):
        for name, fake in (("threading.Thread", InlineThread), ("threading.Timer", InlineTimer)):
            patcher = patch(name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_windowed_uses_current_renderer_bounds_once_then_only_verifies(self):
        host = Host(["WINDOWED", "CLICK|-500|400", "FULLSCREEN"])
        results = []
        ensure_renderer_fullscreen(host, results.append, lambda: True)
        self.assertEqual(host.clicks, [(-500, 400)])
        self.assertEqual(results, ["FULLSCREEN"])
        self.assertNotIn('set value of attribute "AXFullScreen"', "\n".join(host.scripts[-1]))

    def test_no_click_if_fullscreen_or_missing_or_attribute_unknown(self):
        for state in ("FULLSCREEN", "NO_DUAL_RENDERER", "AX_STATE_ERROR|-25205"):
            host = Host([state])
            results = []
            ensure_renderer_fullscreen(host, results.append, lambda: True)
            self.assertEqual(host.clicks, [])
            self.assertEqual(results, [state])

    def test_no_toggle_if_renderer_enters_fullscreen_before_fallback(self):
        host = Host(["WINDOWED", "FULLSCREEN"])
        results = []
        ensure_renderer_fullscreen(host, results.append, lambda: True)
        self.assertEqual(host.clicks, [])
        self.assertEqual(results, ["FULLSCREEN"])

    def test_stale_song_cannot_start_automation(self):
        host = Host([])
        ensure_renderer_fullscreen(host, self.fail, lambda: False)
        self.assertEqual(host.scripts, [])

    def test_failed_verification_does_not_claim_success_or_click_again(self):
        # A WINDOWED verification is re-checked once after a delay (slow Space
        # transitions); if it is still WINDOWED, report it without clicking again.
        host = Host(["WINDOWED", "CLICK|400|300", "WINDOWED", "WINDOWED"])
        results = []
        ensure_renderer_fullscreen(host, results.append, lambda: True)
        self.assertEqual(results, ["WINDOWED"])
        self.assertEqual(len(host.clicks), 1)
        self.assertEqual(len(host.scripts), 4)

    def test_slow_fullscreen_transition_succeeds_on_recheck(self):
        host = Host(["WINDOWED", "CLICK|400|300", "WINDOWED", "FULLSCREEN"])
        results = []
        ensure_renderer_fullscreen(host, results.append, lambda: True)
        self.assertEqual(results, ["FULLSCREEN"])
        self.assertEqual(len(host.clicks), 1)
