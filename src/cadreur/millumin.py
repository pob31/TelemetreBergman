"""Millumin OSC I/O: UDP client out, feedback listener in, query-with-timeout.

Out (to millumin.host:port, default 127.0.0.1:5000):
    /layer:NAME/scale <f>              uniform multiplier, 1.0 = 100 %
    /layer:NAME/position/xy <f f>      pixels, canvas top-left origin

In (feedback listener on UDP feedback_port, default 8000 — must be enabled
manually in Millumin: Device manager -> OSC -> API feedback -> 127.0.0.1:8000):
    send /layer:NAME/scale/?        -> /millumin/layer:NAME/scale <f>
    send /layer:NAME/position/xy/?  -> /millumin/layer:NAME/position/xy <f f>
                                       OR /position/x <f> + /position/y <f>
Both position reply arities are tolerated (verify the real shape on day 1).
Queries correlate strictly by expected reply address; unsolicited feedback
traffic is ignored. query() BLOCKS up to the timeout — call it off the event
loop (asyncio.to_thread).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from .config import MilluminCfg

log = logging.getLogger("cadreur.millumin")


class MilluminIO:
    def __init__(self, cfg: MilluminCfg) -> None:
        self.cfg = cfg
        self._client = SimpleUDPClient(cfg.host, cfg.port)
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}  # reply address -> query slot
        self._server: Optional[ThreadingOSCUDPServer] = None
        self._server_thread: Optional[threading.Thread] = None

    # --- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        if not self.cfg.feedback:
            # Custom Interaction addresses don't answer /? queries — no listener.
            log.info("OSC out -> %s:%d (feedback disabled)", self.cfg.host, self.cfg.port)
            return
        disp = Dispatcher()
        disp.set_default_handler(self._on_feedback)
        try:
            self._server = ThreadingOSCUDPServer(("0.0.0.0", self.cfg.feedback_port), disp)
        except OSError as e:  # port taken: queries will time out, sends still work
            log.error("Cannot bind feedback port %d: %s — capture/test will fail",
                      self.cfg.feedback_port, e)
            return
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="osc-feedback")
        self._server_thread.start()
        log.info("OSC out -> %s:%d, feedback listener on :%d",
                 self.cfg.host, self.cfg.port, self.cfg.feedback_port)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None

    # --- absolute sends (engine tick) -----------------------------------------
    def send_value(self, address: str, value: float) -> None:
        """Send one float to an explicit OSC address. Addresses are configured
        per beamer (e.g. /front/scale/1, /front/positionV/1), so both custom
        Interaction bindings and the standard /layer:NAME API are just data."""
        if not address:
            return
        try:
            self._client.send_message(address, float(value))
        except OSError as e:
            log.warning("OSC send failed: %s", e)

    def send_bang(self, address: str) -> None:
        """Send an argument-less OSC message (pure path trigger, e.g. to reveal
        a layer in Millumin: the address alone, no value)."""
        if not address:
            return
        try:
            self._client.send_message(address, [])
        except OSError as e:
            log.warning("OSC send failed: %s", e)

    # --- correlated query (blocking; run via asyncio.to_thread) ---------------
    def query(self, layer: str, timeout_ms: Optional[int] = None) -> Optional[dict]:
        """Current {scale, pos_x, pos_y, latency_ms} of a layer, or None on
        timeout (Millumin absent / wrong layer name / feedback disabled —
        indistinguishable causes)."""
        timeout = (timeout_ms or self.cfg.feedback_timeout_ms) / 1000.0
        base = f"/millumin/layer:{layer}"
        slot = {"event": threading.Event(), "scale": None, "pos_x": None, "pos_y": None}
        addresses = (f"{base}/scale", f"{base}/position/xy",
                     f"{base}/position/x", f"{base}/position/y")
        with self._lock:
            for a in addresses:
                self._pending[a] = slot
        t0 = time.monotonic()
        try:
            self._client.send_message(f"/layer:{layer}/scale/?", [])
            self._client.send_message(f"/layer:{layer}/position/xy/?", [])
            deadline = t0 + timeout
            while time.monotonic() < deadline:
                if slot["event"].wait(timeout=deadline - time.monotonic()):
                    slot["event"].clear()
                    if all(slot[k] is not None for k in ("scale", "pos_x", "pos_y")):
                        break
        except OSError as e:
            log.warning("OSC query send failed: %s", e)
        finally:
            with self._lock:
                for a in addresses:
                    if self._pending.get(a) is slot:
                        del self._pending[a]
        if any(slot[k] is None for k in ("scale", "pos_x", "pos_y")):
            return None
        return {
            "scale": float(slot["scale"]),
            "pos_x": float(slot["pos_x"]),
            "pos_y": float(slot["pos_y"]),
            "latency_ms": round((time.monotonic() - t0) * 1000.0, 1),
        }

    def _on_feedback(self, address: str, *args) -> None:
        with self._lock:
            slot = self._pending.get(address)
        if slot is None or not args:
            return  # unsolicited feedback traffic — ignored
        try:
            if address.endswith("/scale"):
                slot["scale"] = float(args[0])
            elif address.endswith("/position/xy") and len(args) >= 2:
                slot["pos_x"], slot["pos_y"] = float(args[0]), float(args[1])
            elif address.endswith("/position/x"):
                slot["pos_x"] = float(args[0])
            elif address.endswith("/position/y"):
                slot["pos_y"] = float(args[0])
        except (TypeError, ValueError):
            return
        slot["event"].set()
