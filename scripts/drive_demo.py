"""Drive a beamer through Cadreur's own API (proves engine -> OSC).

    python scripts/drive_demo.py [front|rear]
"""
import json
import sys
import time
import urllib.request

beamer = sys.argv[1] if len(sys.argv) > 1 else "front"
BASE = f"http://127.0.0.1:8080/api/beamer/{beamer}"


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.read().decode()


print(f"[{beamer}] calibrate on ->", post("/calibrate", {"on": True}), flush=True)
for s, v in [(0.3, 0.5), (0.6, 0.5), (0.9, 0.5), (0.5, 0.2), (0.5, 0.8), (0.5, 0.5)]:
    post("/manual", {"scale": s, "pos_v": v})
    print(f"drive scale={s} pos_v={v}", flush=True)
    time.sleep(1.3)
print(f"[{beamer}] calibrate off ->", post("/calibrate", {"on": False}), flush=True)
