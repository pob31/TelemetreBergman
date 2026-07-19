#!/usr/bin/env python3
"""Byte-compatible fake Telemetre Pi for the any-OS dev loop (PRD §15).

Serves GET /stream as Server-Sent Events at 20 Hz with the exact §5 payload.
Point cadreur.toml at it:  [telemetre] url = "http://127.0.0.1:8090"

    python3 scripts/sim_telemetre.py --start_m 3.2 --speed_cm_min 4 \
        --direction -1 --osc_amp_cm 1 --osc_hz 0.5 --zero_cm 197 --sign -1

--zero_cm / --sign make the tare non-trivial, exercising the abs_m
reconstruction. --stale_burst alternates 15 s live / 5 s stale to rehearse
hold behavior. --staircase_cm emulates the Pi's display hysteresis steps.
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS = None
T0 = time.monotonic()


def payload(now: float, hyst_state: dict) -> dict:
    a = ARGS
    t = now - T0
    abs_m = a.start_m + a.direction * (a.speed_cm_min / 100.0 / 60.0) * t \
        + (a.osc_amp_cm / 100.0) * math.sin(2 * math.pi * a.osc_hz * t)
    filtered_cm = abs_m * 100.0
    if a.staircase_cm > 0:  # the Pi's hysteresis staircase
        last = hyst_state.get("cm")
        if last is not None and abs(filtered_cm - last) < a.staircase_cm:
            filtered_cm = last
        hyst_state["cm"] = filtered_cm
    stale = bool(a.stale_burst) and (t % 20.0) >= 15.0
    return {
        "position_m": round((filtered_cm - a.zero_cm) * a.sign / 100.0, 3),
        "raw_m": round(abs_m, 3),
        "strength": 240,
        "temp_c": 31.0,
        "connected": True,
        "port": "/dev/ttySC1",
        "stale": stale,
        "zero_cm": round(a.zero_cm, 1),
        "sign": a.sign,
        "units": "m",
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") not in ("", "/stream") and self.path != "/stream":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        hyst: dict = {}
        last_beat = time.monotonic()
        try:
            while True:
                now = time.monotonic()
                d = payload(now, hyst)
                self.wfile.write(f"data: {json.dumps(d)}\n\n".encode())
                if now - last_beat > 15:
                    last_beat = now
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                time.sleep(1.0 / 20.0)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):  # quiet: one line per connection is enough
        pass


def main() -> None:
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--start_m", type=float, default=3.2)
    ap.add_argument("--speed_cm_min", type=float, default=4.0)
    ap.add_argument("--direction", type=int, default=-1, choices=(-1, 1),
                    help="-1: scrim recedes (abs shrinks)? sign of travel")
    ap.add_argument("--osc_amp_cm", type=float, default=0.0, help="pendulum amplitude")
    ap.add_argument("--osc_hz", type=float, default=0.5)
    ap.add_argument("--zero_cm", type=float, default=0.0)
    ap.add_argument("--sign", type=int, default=1, choices=(-1, 1))
    ap.add_argument("--stale_burst", action="store_true",
                    help="alternate 15 s live / 5 s stale")
    ap.add_argument("--staircase_cm", type=float, default=0.0,
                    help="emulate the Pi's 0.75 cm hysteresis (0 = off)")
    ARGS = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    print(f"sim_telemetre: SSE on http://127.0.0.1:{ARGS.port}/stream "
          f"(start {ARGS.start_m} m, {ARGS.speed_cm_min} cm/min, dir {ARGS.direction}, "
          f"zero {ARGS.zero_cm} cm, sign {ARGS.sign})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
