"""Fake SingWSVocalFX for tests. Behaviour chosen by FAKE_MODE env var.

Echoes control lines back as {"type":"ack"} so tests can assert what the host
actually sent, without any audio device.
"""
import json
import os
import sys
import threading
import time

mode = os.environ.get("FAKE_MODE", "normal")
out = sys.stdout


def say(obj):
    out.write(json.dumps(obj) + "\n")
    out.flush()


def hello():
    say({"type": "hello", "protocol": 1, "device": "FAKE", "sample_rate": 48000,
         "frames": 128, "effect": "reverb", "estimated_latency_ms": 8.4,
         "channels": [1, 2]})


if mode == "crash":
    hello()
    sys.exit(3)
if mode == "device_lost":
    hello()
    say({"type": "device_lost"})
    sys.exit(0)
if mode == "format_changed":
    hello()
    say({"type": "format_changed"})
    sys.exit(0)
if mode == "garbage":
    out.write("this is not json\n")
    out.flush()
    time.sleep(5)
    sys.exit(0)

hello()


def reader():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        say({"type": "ack", "got": obj})
    os._exit(0)          # stdin EOF: the app is gone


threading.Thread(target=reader, daemon=True).start()

frames = 0
while True:
    time.sleep(0.2)
    frames += 9600
    say({"type": "stats", "frames": frames, "clipped": 0, "overruns": 0,
         "peak_db": -18.0, "silent": True, "rss_mb": 12.0})
