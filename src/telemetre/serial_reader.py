"""Serial reader thread — blocking pyserial reads, port auto-detect, reconnect.

Runs off the web event loop. Opens the sensor port (auto-detecting which
/dev/tty* it is by sniffing for valid TF02-Pro framing), parses frames, filters
the valid distances, and pushes results into shared State. On any serial error
it drops the connection, marks State disconnected, and retries with backoff.
"""
from __future__ import annotations

import glob
import logging
import threading
import time
from typing import List, Optional

import serial  # pyserial

from .config import Config
from .filters import DistanceFilter
from .frames import FrameStreamParser, find_frames
from .osc_out import OscSender
from .state import State

log = logging.getLogger("telemetre.serial")


def _expand(candidates: List[str]) -> List[str]:
    out: List[str] = []
    for c in candidates:
        if any(ch in c for ch in "*?["):
            out.extend(sorted(glob.glob(c)))
        else:
            out.append(c)
    return out


def probe_port(path: str, baud: int, timeout: float = 0.4) -> bool:
    """True if `path` yields at least two valid TF02-Pro frames quickly."""
    try:
        with serial.Serial(path, baud, timeout=timeout) as s:
            data = s.read(256)
    except Exception:
        return False
    return len(list(find_frames(data))) >= 2


def detect_port(candidates: List[str], baud: int) -> Optional[str]:
    for path in _expand(candidates):
        if probe_port(path, baud):
            log.info("Detected TF02-Pro on %s", path)
            return path
    return None


class SerialReader(threading.Thread):
    def __init__(self, cfg: Config, state: State, osc: Optional[OscSender] = None) -> None:
        super().__init__(daemon=True, name="serial-reader")
        self.cfg = cfg
        self.state = state
        self.osc = osc
        self._stop = threading.Event()
        self._filter = DistanceFilter(
            median_size=cfg.filter.median_size,
            ema_alpha=cfg.filter.ema_alpha,
            hysteresis_cm=cfg.filter.hysteresis_cm,
        )

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            port = self.cfg.serial.port or detect_port(self.cfg.serial.candidates, self.cfg.serial.baud)
            if not port:
                self.state.mark_disconnected()
                log.warning("No TF02-Pro found; retrying...")
                self._stop.wait(1.5)
                continue
            try:
                self._read_loop(port)
            except serial.SerialException as e:
                log.warning("Serial error on %s: %s; reconnecting", port, e)
            except Exception:
                log.exception("Unexpected reader error; reconnecting")
            finally:
                self.state.mark_disconnected()
                self._filter.reset()
                self._stop.wait(0.5)

    def _read_loop(self, port: str) -> None:
        parser = FrameStreamParser()
        with serial.Serial(port, self.cfg.serial.baud, timeout=0.2) as s:
            self.state.mark_connected(port)
            log.info("Reading TF02-Pro on %s @ %d 8N1", port, self.cfg.serial.baud)
            while not self._stop.is_set():
                data = s.read(64)
                if not data:
                    continue
                for frame in parser.feed(data):
                    if frame.valid:
                        filtered = self._filter.update(float(frame.distance_cm))
                        self.state.update_valid(frame, filtered)
                        if self.osc is not None:
                            self.osc.maybe_send(self.state.position_m, time.monotonic())
                    else:
                        self.state.update_invalid(frame)
