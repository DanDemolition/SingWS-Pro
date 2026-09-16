"""Fake SingWSSoundHelper for tests. Behaviour chosen by FAKE_MODE env var."""
import json
import os
import sys
import time

mode = os.environ.get("FAKE_MODE", "normal")
out = sys.stdout


def say(obj):
    out.write(json.dumps(obj) + "\n")
    out.flush()


if mode == "crash":
    say({"type": "hello", "model": "fake", "protocol": 1})
    sys.exit(3)
if mode == "error":
    say({"type": "hello", "model": "fake", "protocol": 1})
    say({"type": "error", "message": "permission denied"})
    time.sleep(5)
    sys.exit(1)
say({"type": "hello", "model": "fake.v1", "protocol": 1})
if mode == "silent":            # no heartbeat, no windows
    time.sleep(30)
    sys.exit(0)
if mode == "malformed":
    for _ in range(200):
        out.write("not json\n")
    out.flush()
    time.sleep(30)
if mode == "rss":
    say({"type": "heartbeat", "windows": 0, "dropped": 0, "rss_mb": 999})
    time.sleep(30)
i = 0
while True:
    say({"type": "window", "t": i * 0.5, "top": [["singing", 0.9], ["music", 0.8]]})
    say({"type": "heartbeat", "windows": i, "dropped": 0, "rss_mb": 40})
    i += 1
    time.sleep(0.1)
