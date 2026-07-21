"""Shared runtime state — the single source of truth.

The telemetre client thread writes the distance; the web layer and the engine
(both on the event loop) read snapshots and edit the show. One lock guards the
cross-thread fields. Armed is runtime-only and always starts False — it is
never persisted anywhere (PRD §10). `cadreur_state.json` remembers only the
last-opened show path.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from . import show as showmod
from .config import REPO_ROOT, Config

log = logging.getLogger("cadreur.state")

STATE_FILE = "cadreur_state.json"

SOURCE_LIVE = "live"
SOURCE_STALE = "stale"
SOURCE_DISCONNECTED = "disconnected"


class CadreurState:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()

        # --- distance (written by the telemetre client thread) ---
        self._abs_m: Optional[float] = None  # smoothed (median+EMA), held on stale
        self._abs_m_raw: Optional[float] = None  # last reconstructed, unsmoothed
        self._position_m: Optional[float] = None  # Pi tared value (crew reference)
        self._pi: dict = {}  # last raw SSE payload (subset)
        self._sse_connected = False
        self._last_usable: float = 0.0  # time.monotonic() of last usable payload
        self._ever_usable = False

        # --- show (edited on the event loop only) ---
        self.show: dict = showmod.new_show()
        self.show_path: Optional[Path] = None
        self.dirty = False
        self._dirty_since: Optional[float] = None
        self.last_autosave: Optional[float] = None

        # --- runtime controls (per channel, keyed "{beamer}/{cid}") ---
        self.armed = False  # never persisted
        # Channels currently in calibrate mode (driven live). Several may be on
        # at once, so all layers can be adjusted at one scrim position.
        self.calibrate: set = set()
        # Manual drive values ("drive from Cadreur"): scale, pos_v (vertical),
        # pos_h (horizontal), all normalised 0..1 (0.5 = centred). Lazily created
        # per channel; runtime-only, never persisted.
        self.manual: dict = {}

        # --- written by the engine, read by the UI (keyed "{beamer}/{cid}") ---
        self.channels_state: dict = {}
        self.millumin: dict = {"ok": None, "latency_ms": None, "warning": None}

        self._state_path = REPO_ROOT / STATE_FILE

    # --- per-channel runtime helpers -----------------------------------------
    @staticmethod
    def chan_key(beamer: str, cid: str) -> str:
        return f"{beamer}/{cid}"

    def manual_of(self, key: str) -> dict:
        m = self.manual.get(key)
        if m is None:
            m = {"scale": 0.5, "pos_v": 0.5, "pos_h": 0.5}
            self.manual[key] = m
        return m

    # --- cadreur_state.json (last-opened show only) --------------------------
    def load_last_show_path(self) -> Optional[Path]:
        try:
            d = json.loads(self._state_path.read_text(encoding="utf-8"))
            p = d.get("last_show")
            return Path(p) if p else None
        except FileNotFoundError:
            return None
        except Exception as e:  # never let a bad state file stop startup
            log.warning("Ignoring unreadable %s: %s", self._state_path, e)
            return None

    def remember_show_path(self) -> None:
        try:
            self._state_path.write_text(
                json.dumps({"last_show": str(self.show_path) if self.show_path else None}),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning("Could not persist %s: %s", self._state_path, e)

    # --- distance writes (client thread) --------------------------------------
    def sse_status(self, connected: bool) -> None:
        with self._lock:
            self._sse_connected = connected

    def update_distance(self, abs_m_smoothed: float, abs_m_raw: float,
                        position_m: Optional[float], payload: dict, now: float) -> None:
        with self._lock:
            self._abs_m = abs_m_smoothed
            self._abs_m_raw = abs_m_raw
            self._position_m = position_m
            self._pi = payload
            self._last_usable = now
            self._ever_usable = True

    def note_unusable(self, payload: dict) -> None:
        """SSE event arrived but is not usable (Pi stale/disconnected/null).
        Distance holds; do NOT refresh the usable timestamp."""
        with self._lock:
            self._pi = payload

    # --- distance reads --------------------------------------------------------
    def smoothing_params(self) -> dict:
        return dict(self.show["smoothing"])

    def source_state(self, now: Optional[float] = None) -> str:
        now = time.monotonic() if now is None else now
        with self._lock:
            if not self._sse_connected:
                return SOURCE_DISCONNECTED
            fresh = (now - self._last_usable) * 1000.0 <= self.cfg.telemetre.stale_after_ms
            return SOURCE_LIVE if (self._ever_usable and fresh) else SOURCE_STALE

    def distance(self) -> tuple[Optional[float], bool]:
        """(smoothed abs_m or None, ever_usable) — the engine's input."""
        with self._lock:
            return self._abs_m, self._ever_usable

    # --- show edits (event loop) ----------------------------------------------
    def mark_dirty(self) -> None:
        self.dirty = True
        self._dirty_since = time.monotonic()

    def maybe_autosave(self, now: float) -> bool:
        """Debounced: saves `autosave_debounce_s` after the LAST edit."""
        if not (self.dirty and self.cfg.shows.autosave and self.show_path):
            return False
        if self._dirty_since is None or now - self._dirty_since < self.cfg.shows.autosave_debounce_s:
            return False
        try:
            showmod.save_show(self.show_path, self.show)
            self.dirty = False
            self._dirty_since = None
            self.last_autosave = now
            return True
        except Exception as e:  # a failed autosave must never crash the engine
            log.warning("Autosave failed: %s", e)
            self._dirty_since = now  # retry after another debounce period
            return False

    # --- snapshot for the UI SSE ----------------------------------------------
    def snapshot(self) -> dict:
        now = time.monotonic()
        source = self.source_state(now)
        with self._lock:
            dist = {
                "abs_m": None if self._abs_m is None else round(self._abs_m, 4),
                "abs_m_raw": None if self._abs_m_raw is None else round(self._abs_m_raw, 4),
                "position_m": self._position_m,
                "source": source,
            }
        doc = self.show
        return {
            "distance": dist,
            "armed": self.armed,
            "settings": {"active_lens_memory": doc["settings"]["active_lens_memory"]},
            "lens_memories": list(doc["lens_memories"]),
            "smoothing": dict(doc["smoothing"]),
            "beamers": {
                b: [self._channel_public(b, ch) for ch in doc["beamers"][b]["channels"]]
                for b in showmod.BEAMER_KEYS
            },
            "show": {
                "name": doc["meta"]["name"],
                "notes": doc["meta"]["notes"],
                "saved_at": doc["meta"]["saved_at"],
                "file": self.show_path.name if self.show_path else None,
                "dirty": self.dirty,
                "autosave": bool(self.cfg.shows.autosave),
            },
            "millumin": dict(self.millumin),
        }

    def _channel_public(self, b: str, ch: dict) -> dict:
        """Merge a channel definition with its live runtime state for the UI."""
        key = self.chan_key(b, ch["id"])
        cset = showmod.cal_set_for(self.show, b, ch)
        rt = self.channels_state.get(key, {})
        return {
            "id": ch["id"], "name": ch["name"], "enabled": ch["enabled"],
            "osc_scale": ch["osc_scale"], "osc_posv": ch["osc_posv"], "osc_posh": ch["osc_posh"],
            "cal_key": showmod.cal_key_for(self.show, b),
            "points": cset["points"] if cset else [],
            "trim": cset["trim"] if cset else showmod.default_trim(),
            "calibrating": key in self.calibrate,
            "manual": dict(self.manual_of(key)),
            "reason": rt.get("reason"),
            "gate": rt.get("gate", False),
            "clamped": rt.get("clamped"),
            "values": rt.get("values"),
            "sending": rt.get("sending", False),
            "n_points": len(cset["points"]) if cset else 0,
        }

    def health(self) -> dict:
        return {"status": "ok", "source": self.source_state(), "armed": self.armed}
