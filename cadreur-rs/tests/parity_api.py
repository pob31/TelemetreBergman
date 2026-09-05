#!/usr/bin/env python3
"""Differential test: every REST endpoint, Python reference vs Rust port.

Both implementations are run side by side and every response compared, error
paths included. This is what makes the API port defensible — the web UI is
shared verbatim between the two builds, so any divergence in a response shape
would break the interface in a way unit tests cannot see.

    # terminal 1 — the Python reference
    cd <repo> && CADREUR_CONFIG=/tmp/py.toml .venv/bin/python -m cadreur   # port 8098
    # terminal 2 — the Rust port
    cd cadreur-rs && CADREUR_DATA_DIR=/tmp/rs ./target/debug/cadreur --headless  # port 8099
    # terminal 3
    python3 cadreur-rs/tests/parity_api.py

Volatile fields (timestamps, file names, the shows listing) are scrubbed: the
two run against different data directories on purpose.
"""
import json
import subprocess
import sys

PY_PORT, RS_PORT = 8098, 8099


def call(port, method, path, body=None):
    cmd = ["curl", "-s", "-m", "5", "-X", method, f"http://127.0.0.1:{port}{path}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True).stdout.decode()
    try:
        return json.loads(out)
    except ValueError:
        return {"__raw__": out[:120]}


CASES = [
    ("POST", "/api/arm", {"armed": True}),
    ("POST", "/api/arm", {"armed": False}),
    ("POST", "/api/lens_memory", {"id": "M2"}),
    ("POST", "/api/lens_memory", {"id": "M9"}),
    ("POST", "/api/beamer/front/channel/add", {"name": "Extra"}),
    ("POST", "/api/beamer/bogus/channel/add", {"name": "X"}),
    ("POST", "/api/channel/front/front-1/rename", {"name": "Scope"}),
    ("POST", "/api/channel/front/nope/rename", {"name": "X"}),
    ("POST", "/api/channel/front/front-1/osc", {"osc_scale": "/front/scale/9"}),
    ("POST", "/api/channel/front/front-1/osc", {"osc_scale": "bad addr"}),
    ("POST", "/api/channel/front/front-1/enable", {"enabled": False}),
    ("POST", "/api/channel/front/front-1/calibrate", {"on": True}),
    ("POST", "/api/channel/front/front-1/manual", {"scale": 0.8, "pos_v": 0.3, "pos_h": 0.7}),
    ("POST", "/api/channel/front/front-1/manual", {"scale": "nope"}),
    ("POST", "/api/channel/front/front-1/manual", {"scale": 5.0}),
    ("POST", "/api/channel/front/front-1/capture", None),
    ("POST", "/api/capture_all", None),
    ("POST", "/api/channel/front/front-1/points",
     {"op": "add", "point": {"distance_m": 2.0, "scale": 0.6, "pos_x": 0.5, "pos_y": 0.4}}),
    ("POST", "/api/channel/front/front-1/points",
     {"op": "add", "point": {"distance_m": 4.0, "scale": 0.4, "pos_x": 0.5, "pos_y": 0.6}}),
    ("POST", "/api/channel/front/front-1/points", {"op": "add", "point": {"distance_m": "junk"}}),
    ("POST", "/api/channel/front/front-1/points", {"op": "delete", "index": 0}),
    ("POST", "/api/channel/front/front-1/points", {"op": "delete", "index": 99}),
    ("POST", "/api/channel/front/front-1/points", {"op": "bogus"}),
    ("POST", "/api/channel/front/front-1/trim", {"scale_mul": 1.02, "dx_px": -3.0}),
    ("POST", "/api/channel/front/front-1/trim", {"scale_mul": "bad"}),
    ("POST", "/api/channel/front/front-1/trim/bake", None),
    ("POST", "/api/channel/front/front-1/trim/reset", None),
    ("POST", "/api/smoothing", {"ema_tau_s": 3.0}),
    ("POST", "/api/smoothing", {"ema_tau_s": 999.0}),
    ("POST", "/api/smoothing", {"bogus": 1}),
    ("POST", "/api/test_millumin", None),
    ("POST", "/api/save", None),
    ("POST", "/api/meta", {"name": "Renamed", "notes": "hello"}),
    ("POST", "/api/channel/front/front-2/delete", None),
    ("POST", "/api/channel/front/nope/delete", None),
    ("POST", "/api/import", {"version": 1, "app": "cadreur", "looks": []}),
    ("POST", "/api/import", {"nope": 1}),
]

VOLATILE = {"saved_at", "file", "shows", "current", "distance_m"}


def scrub(o):
    if isinstance(o, dict):
        return {k: ("<volatile>" if k in VOLATILE else scrub(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(x) for x in o]
    return o


def main():
    for port, what in ((PY_PORT, "python"), (RS_PORT, "rust")):
        if "status" not in call(port, "GET", "/api/health"):
            sys.exit(f"{what} is not answering on :{port} — see the docstring")

    diffs, same = [], 0
    for method, path, body in CASES:
        p = scrub(call(PY_PORT, method, path, body))
        r = scrub(call(RS_PORT, method, path, body))
        if p == r:
            same += 1
        else:
            diffs.append((method, path, body, p, r))

    print(f"{same}/{len(CASES)} endpoint responses identical")
    for m, pth, b, p, r in diffs:
        print(f"\nDIFF {m} {pth} {json.dumps(b) if b else ''}")
        print(f"  python: {json.dumps(p)[:300]}")
        print(f"  rust  : {json.dumps(r)[:300]}")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
