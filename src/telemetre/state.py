"""Shared latest-reading state — the single source of truth.

The serial reader thread writes here; the web layer reads snapshots. All access
is guarded by one lock. The tare zero and direction sign are persisted to a
small JSON file so they survive a restart.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from .config import REPO_ROOT, Config
from .frames import Frame

log = logging.getLogger("telemetre.state")


class State:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()

        self._raw_cm: float | None = None
        self._filtered_cm: float | None = None
        self._strength: int = 0
        self._temp_c: float = 0.0
        self._last_valid: float = 0.0  # time.monotonic() of last valid frame
        self._connected: bool = False
        self._port: str | None = None

        self.zero_cm: float = 0.0
        self.sign: int = -1 if cfg.position.sign < 0 else 1

        self._state_path = self._resolve_state_path()
        self._load()

    # --- persistence ---------------------------------------------------------
    def _resolve_state_path(self) -> Path:
        p = Path(self.cfg.position.state_file)
        return p if p.is_absolute() else REPO_ROOT / p

    def _load(self) -> None:
        try:
            d = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.zero_cm = float(d.get("zero_cm", 0.0))
            self.sign = -1 if int(d.get("sign", self.sign)) < 0 else 1
            log.info("Loaded state: zero_cm=%.1f sign=%d", self.zero_cm, self.sign)
        except FileNotFoundError:
            pass
        except Exception as e:  # never let a bad state file stop startup
            log.warning("Ignoring unreadable state file %s: %s", self._state_path, e)

    def _save(self) -> None:
        try:
            self._state_path.write_text(
                json.dumps({"zero_cm": self.zero_cm, "sign": self.sign}),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning("Could not persist state: %s", e)

    # --- writes (reader thread) ----------------------------------------------
    def update_valid(self, frame: Frame, filtered_cm: float) -> None:
        with self._lock:
            self._raw_cm = float(frame.distance_cm)
            self._filtered_cm = filtered_cm
            self._strength = frame.strength
            self._temp_c = frame.temperature_c
            self._last_valid = time.monotonic()

    def update_invalid(self, frame: Frame) -> None:
        # Keep strength fresh (drives the signal meter) but do NOT refresh the
        # last-valid timestamp, so the readout correctly goes "stale/no signal".
        with self._lock:
            self._strength = frame.strength

    def mark_connected(self, port: str) -> None:
        with self._lock:
            self._connected = True
            self._port = port

    def mark_disconnected(self) -> None:
        with self._lock:
            self._connected = False

    # --- controls (web thread) -----------------------------------------------
    def set_zero(self) -> None:
        with self._lock:
            if self._filtered_cm is not None:
                self.zero_cm = self._filtered_cm
            self._save()

    def clear_zero(self) -> None:
        with self._lock:
            self.zero_cm = 0.0
            self._save()

    def toggle_sign(self) -> None:
        with self._lock:
            self.sign = -self.sign
            self._save()

    # --- reads ---------------------------------------------------------------
    @property
    def position_m(self) -> float | None:
        with self._lock:
            if self._filtered_cm is None:
                return None
            return round((self._filtered_cm - self.zero_cm) * self.sign / 100.0, 3)

    def snapshot(self) -> dict:
        now = time.monotonic()
        with self._lock:
            has = self._filtered_cm is not None
            stale = (not has) or ((now - self._last_valid) * 1000.0 > self.cfg.filter.stale_after_ms)
            pos = None if not has else round((self._filtered_cm - self.zero_cm) * self.sign / 100.0, 3)
            raw_m = None if self._raw_cm is None else round(self._raw_cm / 100.0, 3)
            return {
                "position_m": pos,
                "raw_m": raw_m,
                "strength": self._strength,
                "temp_c": round(self._temp_c, 1),
                "connected": self._connected,
                "port": self._port,
                "stale": stale,
                "zero_cm": round(self.zero_cm, 1),
                "sign": self.sign,
                "units": "m",
            }

    def health(self) -> dict:
        with self._lock:
            return {"status": "ok", "connected": self._connected, "port": self._port}
