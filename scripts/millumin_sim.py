#!/usr/bin/env python3
"""Fake Millumin for the dev loop (PRD §15).

Listens for OSC on :5000, pretty-prints every message with a timestamp, keeps
per-layer last state, and answers /layer:*/scale/? and /layer:*/position/xy/?
queries toward the feedback destination (default 127.0.0.1:8000).

    python3 scripts/millumin_sim.py
    python3 scripts/millumin_sim.py --no-feedback      # rehearse capture timeout
    python3 scripts/millumin_sim.py --reply-split-xy   # two-message position reply

Unknown queried layers are invented (scale 1.0 @ 960,540) so capture works
before any traffic. Requires python-osc (already a repo dependency).
"""
from __future__ import annotations

import argparse
import threading
import time
from datetime import datetime

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

LAYERS: dict[str, dict] = {}
LOCK = threading.Lock()
ARGS = None
FEEDBACK: SimpleUDPClient | None = None


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def layer_state(name: str) -> dict:
    with LOCK:
        return LAYERS.setdefault(name, {"scale": 1.0, "pos_x": 960.0, "pos_y": 540.0})


def show_table() -> None:
    with LOCK:
        for name, st in sorted(LAYERS.items()):
            print(f"{stamp()}   [{name}] scale={st['scale']:.4f} "
                  f"x={st['pos_x']:.2f} y={st['pos_y']:.2f}")


def handle(address: str, *args) -> None:
    print(f"{stamp()} {address} {list(args)}")
    if not address.startswith("/layer:"):
        return
    rest = address[len("/layer:"):]
    name, _, tail = rest.partition("/")
    st = layer_state(name)

    if tail == "scale" and args:
        st["scale"] = float(args[0])
    elif tail == "position/xy" and len(args) >= 2:
        st["pos_x"], st["pos_y"] = float(args[0]), float(args[1])
    elif tail.endswith("/?") or tail == "?":
        if ARGS.no_feedback or FEEDBACK is None:
            print(f"{stamp()}   (query ignored: --no-feedback)")
            return
        base = f"/millumin/layer:{name}"
        if tail.startswith("scale"):
            FEEDBACK.send_message(f"{base}/scale", float(st["scale"]))
            print(f"{stamp()}   -> {base}/scale {st['scale']:.4f}")
        elif tail.startswith("position/xy"):
            if ARGS.reply_split_xy:
                FEEDBACK.send_message(f"{base}/position/x", float(st["pos_x"]))
                FEEDBACK.send_message(f"{base}/position/y", float(st["pos_y"]))
                print(f"{stamp()}   -> {base}/position/x + /y (split arity)")
            else:
                FEEDBACK.send_message(f"{base}/position/xy",
                                      [float(st["pos_x"]), float(st["pos_y"])])
                print(f"{stamp()}   -> {base}/position/xy {st['pos_x']:.2f} {st['pos_y']:.2f}")
        return
    show_table()


def main() -> None:
    global ARGS, FEEDBACK
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=5000, help="OSC input port")
    ap.add_argument("--feedback-host", default="127.0.0.1")
    ap.add_argument("--feedback-port", type=int, default=8000)
    ap.add_argument("--no-feedback", action="store_true",
                    help="never answer queries (rehearse the timeout path)")
    ap.add_argument("--reply-split-xy", action="store_true",
                    help="answer position queries as /position/x + /position/y")
    ARGS = ap.parse_args()
    if not ARGS.no_feedback:
        FEEDBACK = SimpleUDPClient(ARGS.feedback_host, ARGS.feedback_port)

    disp = Dispatcher()
    disp.set_default_handler(handle)
    srv = ThreadingOSCUDPServer(("0.0.0.0", ARGS.port), disp)
    print(f"millumin_sim: OSC in on :{ARGS.port}, feedback -> "
          f"{ARGS.feedback_host}:{ARGS.feedback_port}"
          f"{' (DISABLED)' if ARGS.no_feedback else ''}"
          f"{' (split xy replies)' if ARGS.reply_split_xy else ''}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
