"""Round-trip real Python history through the PHP sync implementation in scratch SQLite."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class SingerHistoryCountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("singws_history_counts", "0.2.18.1.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        self.app = self.module.KaraokeApp.__new__(self.module.KaraokeApp)
        self.app.singer_history = {"singers": {}, "deletions": {}, "song_deletions": {}}
        self.app.queue = []
        self.app._karaoke_tempo_percent = 100
        self.app._schedule_singer_history_refresh = lambda **_: None
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        php = shutil.which("php")
        if not php:
            self.skipTest("PHP with SQLite3 is required for the app/server integration test")
        # The app and server now share one consolidated SingWS workspace.
        server = Path(__file__).resolve().parent / "SingWS-Server"
        if not (server / "api" / "v1" / "singer_history_sync.php").is_file():
            # SingWS-Server is a private repo; it is checked out locally (and
            # gitignored) only on machines that run the app/server tests.
            self.skipTest("SingWS-Server checkout not present at ./SingWS-Server")
        if not (server / "config.inc").is_file():
            self.skipTest("SingWS-Server/config.inc missing; for local tests: "
                          "cp SingWS-Server/config.inc.example SingWS-Server/config.inc")
        # No endpoint/auth/tenant setup: require helper definitions, and operate
        # exclusively on a new SQLite file under TemporaryDirectory.
        code = r'''
require $argv[1];
$db = new SQLite3($argv[2]);
_ensure_history_sync_schema($db);
while (($line = fgets(STDIN)) !== false) {
  $payload = json_decode($line, true);
  _apply_incoming_deletions($db, _normalize_incoming_deletions($payload));
  _apply_incoming_song_deletions($db, _normalize_incoming_song_deletions($payload));
  _apply_incoming_history($db, _normalize_incoming_history($payload));
  echo json_encode(_merge_server_history($db)), "\n";
  fflush(STDOUT);
}
$db->close();
'''
        self.process = subprocess.Popen(
            [php, "-r", code, str(server / "api/v1/singer_history_sync.php"), str(Path(self.tmp.name) / "history.db")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(self.close_server)

    def close_server(self):
        self.process.stdin.close()
        self.process.wait(timeout=5)
        errors = self.process.stderr.read()
        self.process.stdout.close()
        self.process.stderr.close()
        self.assertEqual(self.process.returncode, 0, errors)

    def sing(self, track_id="kf_591933", provider="karafun_streaming"):
        self.app._record_singer_history_play("Shawn", {
            "artist": "Temper City", "title": "Self Aware",
            "provider": provider, "provider_track_id": track_id,
            "provider_url": "https://www.karafun.com/",
        }, "", tempo_percent=100)

    def sync(self):
        self.process.stdin.write(json.dumps(self.app._export_singer_history_payload()) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        self.assertTrue(line, "PHP sync worker exited unexpectedly")
        result = json.loads(line)
        self.app._merge_remote_singer_history(result)
        return result

    def test_repeated_karafun_sync_does_not_invent_performances(self):
        self.sing()
        for _ in range(12):
            remote = self.sync()
            local = self.app._export_singer_history_payload()
            for store in (local, remote):
                singer = store["singers"]["shawn"]
                self.assertEqual(sum(s["play_count"] for s in singer["songs"].values()), 1)
                self.assertEqual(singer["total_performances"], 1)
                self.assertEqual(len(singer["songs"]), 1)
                song = next(iter(singer["songs"].values()))
                self.assertEqual(song["provider"], "karafun_streaming")
                self.assertEqual(song["provider_track_id"], "kf_591933")
            self.assertFalse(self.app._merge_remote_singer_history(remote), "identical sync must not rebuild history")

    def test_actual_repeat_and_different_provider_records_count_once_each(self):
        self.sing()
        self.sync()
        self.sing()
        self.sync()
        self.sing("kf_1349184")
        self.sync()
        self.sing("", "local")
        for _ in range(5):
            remote = self.sync()
            for store in (self.app._export_singer_history_payload(), remote):
                songs = store["singers"]["shawn"]["songs"]
                self.assertEqual(sum(s["play_count"] for s in songs.values()), 4)
                self.assertEqual(store["singers"]["shawn"]["total_performances"], 4)

    def test_alias_snapshots_merge_without_adding_counts(self):
        self.sing()
        record = self.app.singer_history["singers"]["shawn"]
        song = next(iter(record["songs"].values()))
        record["songs"]["legacy alias"] = copy.deepcopy(song)
        for _ in range(5):
            remote = self.sync()
            self.assertEqual(sum(s["play_count"] for s in remote["singers"]["shawn"]["songs"].values()), 1)

    def test_stale_response_cannot_erase_new_completion(self):
        self.sing()
        old = self.sync()
        self.sing()
        self.app._merge_remote_singer_history(old)
        remote = self.sync()
        self.assertEqual(remote["singers"]["shawn"]["total_performances"], 2)
        self.assertEqual(sum(s["play_count"] for s in remote["singers"]["shawn"]["songs"].values()), 2)

    def test_provider_song_deletion_stays_deleted_across_sync(self):
        self.sing()
        self.sing("", "local")
        self.sync()
        record = self.app.singer_history["singers"]["shawn"]
        key = next(key for key in record["songs"] if key.startswith("karafun"))
        song = record["songs"][key]
        self.app._record_singer_history_song_tombstone("shawn", key, song, deleted_at=song["updated_at"] + 1)
        record["songs"].pop(key)
        for _ in range(3):
            remote = self.sync()
            self.assertEqual(list(remote["singers"]["shawn"]["songs"]), ["temper city|self aware"])
            self.assertEqual(remote["singers"]["shawn"]["total_performances"], 1)

    def test_completion_callback_counts_once_and_aborted_song_counts_zero(self):
        self.app._complete_remote_request = lambda *a, **k: None
        self.app._schedule_save_data = lambda *a, **k: None
        self.app._sync_singer_history_async = lambda *a, **k: None
        pending = {
            "singer_name": "Shawn", "song_path": "", "tempo_percent": 100,
            "entry": {"artist": "Temper City", "title": "Self Aware"},
        }
        self.app._pending_performance = copy.deepcopy(pending)
        self.assertTrue(self.app._commit_pending_performance())
        self.assertFalse(self.app._commit_pending_performance())
        self.app._pending_performance = copy.deepcopy(pending)
        self.app._discard_pending_performance()
        self.assertFalse(self.app._commit_pending_performance())
        remote = self.sync()
        self.assertEqual(remote["singers"]["shawn"]["total_performances"], 1)

    def test_singer_total_is_derived_from_song_counts(self):
        self.sing()
        self.app.singer_history["singers"]["shawn"]["total_performances"] = 999
        remote = self.sync()
        self.assertEqual(remote["singers"]["shawn"]["total_performances"], 1)
        self.assertEqual(self.app.singer_history["singers"]["shawn"]["total_performances"], 1)


if __name__ == "__main__":
    unittest.main()
