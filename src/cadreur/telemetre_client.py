"""Telemetre SSE client thread — stdlib http.client, reconnect with backoff.

Mirrors the serial reader's pattern on the Pi: connect -> stream -> on error
mark disconnected, back off (1 -> 5 s), retry. Each usable payload is turned
into abs_m (tare-independent, PRD §5) and pushed through stages 1-2 of the
smoothing chain (median-of-3 + tau-EMA) with the measured dt. On stale or
disconnect the smoothed value simply stops being updated — it holds, no reset.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from http.client import HTTPConnection
from urllib.parse import urlsplit

from .config import Config
from .smoothing import Median3, TauEma
from .state import CadreurState

log = logging.getLogger("cadreur.telemetre")


def reconstruct_abs_m(payload: dict) -> float | None:
    """abs_m = position_m * sign + zero_cm / 100 — immune to Set Zero /
    Clear Zero / Invert on the Pi (sign^2 = 1)."""
    pos = payload.get("position_m")
    if pos is None:
        return None
    sign = -1 if payload.get("sign", 1) < 0 else 1
    zero_cm = float(payload.get("zero_cm", 0.0))
    return float(pos) * sign + zero_cm / 100.0


def usable(payload: dict) -> bool:
    return bool(payload.get("connected")) and not payload.get("stale") \
        and payload.get("position_m") is not None


class TelemetreClient(threading.Thread):
    def __init__(self, cfg: Config, state: CadreurState) -> None:
        super().__init__(daemon=True, name="telemetre-sse")
        self.cfg = cfg
        self.state = state
        self._stop = threading.Event()
        self._median = Median3()
        self._ema = TauEma(state.smoothing_params()["ema_tau_s"])
        self._last_usable_t: float | None = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._stream_once()
                backoff = 1.0  # a successful connection resets the backoff
            except (OSError, socket.timeout) as e:
                log.warning("Telemetre stream error: %s; reconnecting in %.0fs", e, backoff)
            except Exception:
                log.exception("Unexpected telemetre client error; reconnecting")
            finally:
                self.state.sse_status(False)
            self._stop.wait(backoff)
            backoff = min(5.0, backoff * 2)

    # --- one connection lifetime ---------------------------------------------
    def _stream_once(self) -> None:
        parts = urlsplit(self.cfg.telemetre.url)
        host = parts.hostname or self.cfg.telemetre.url
        port = parts.port or 80
        path = (parts.path.rstrip("/") or "") + "/stream"
        # Read timeout: generous enough for the 20 Hz feed + 15 s keepalives;
        # a silent dead TCP path is cut after ~20 s and reconnected.
        conn = HTTPConnection(host, port, timeout=20)
        try:
            conn.request("GET", path, headers={"Accept": "text/event-stream"})
            resp = conn.getresponse()
            if resp.status != 200:
                raise OSError(f"GET {path} -> HTTP {resp.status}")
            self.state.sse_status(True)
            log.info("Telemetre stream connected: %s:%d%s", host, port, path)
            buf = b""
            while not self._stop.is_set():
                chunk = resp.read1(4096) if hasattr(resp, "read1") else resp.read(4096)
                if not chunk:
                    raise OSError("stream closed by server")
                buf += chunk
                while b"\n\n" in buf:
                    event, buf = buf.split(b"\n\n", 1)
                    self._handle_event(event)
        finally:
            conn.close()

    def _handle_event(self, event: bytes) -> None:
        data_lines = [ln[5:].strip() for ln in event.split(b"\n") if ln.startswith(b"data:")]
        if not data_lines:
            return  # keepalive comment
        try:
            payload = json.loads(b"\n".join(data_lines).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        now = time.monotonic()
        if not usable(payload):
            self._last_usable_t = None  # measure dt across the gap correctly
            self.state.note_unusable(payload)
            return
        abs_m = reconstruct_abs_m(payload)
        if abs_m is None:
            self.state.note_unusable(payload)
            return
        # After a gap (first payload / stale recovery) use one nominal 20 Hz
        # tick: the EMA then absorbs small jumps instead of snapping.
        dt = 0.05 if self._last_usable_t is None else max(0.0, now - self._last_usable_t)
        self._last_usable_t = now
        self._ema.tau_s = float(self.state.smoothing_params()["ema_tau_s"])
        smoothed = self._ema.update(self._median.update(abs_m), dt)
        self.state.update_distance(smoothed, abs_m, payload.get("position_m"), payload, now)
