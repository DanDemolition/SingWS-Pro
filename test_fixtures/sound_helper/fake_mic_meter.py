"""Fake SingWSMicMeter. FAKE_MODE: normal | lost_once | format_change | loud_hum"""
import json, os, sys, time
mode = os.environ.get("FAKE_MODE", "normal")
state_file = os.environ.get("FAKE_STATE", "")

def say(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

if "--list-devices" in sys.argv:
    say({"type": "devices", "devices": [{"uid": "UI24R-UID", "name": "Soundcraft Ui24R", "input_channels": 32, "sample_rate": 48000}]})
    sys.exit(0)
launches = 0
if state_file:
    launches = int(open(state_file).read() or 0) if os.path.exists(state_file) else 0
    open(state_file, "w").write(str(launches + 1))
say({"type": "hello", "protocol": 1, "device": "UI24R-UID", "name": "Soundcraft Ui24R", "sample_rate": 48000, "input_channels": 32, "channels": [1, 2, 3]})
if mode == "lost_once" and launches == 0:
    time.sleep(0.3)
    say({"type": "device_lost", "reason": "not_alive"}); sys.exit(3)
if mode == "format_change":
    time.sleep(0.3)
    say({"type": "format_changed", "sample_rate": 44100}); sys.exit(4)
i = 0
while True:
    singer = -20.0 if mode != "loud_hum" else -30.0
    say({"type": "levels", "t": i * 0.1, "ch": {"1": {"rms_db": singer, "peak_db": -10.0},
                                                  "2": {"rms_db": -70.0, "peak_db": -60.0},
                                                  "3": {"rms_db": -70.0, "peak_db": -60.0}}})
    if i % 10 == 0:
        say({"type": "heartbeat", "frames": i * 4800, "rss_mb": 12})
    i += 1
    time.sleep(0.1)
