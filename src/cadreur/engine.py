"""Engine: 20 Hz tick — gates -> targets -> slew -> send policy (PRD §10).

Runs as an asyncio task in the FastAPI lifespan. Each tick is pure-ish and
clock-driven (tick(now) with an injected now), so tests drive it with a fake
clock and a fake OSC sender. Disarmed means TOTAL OSC silence; disarming never
sends a "return to neutral". Arming snaps (setup-time act); every other
discontinuity glides through the slew limiters.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Optional

from . import show as showmod
from .config import Config
from .interp import PARAMS, apply_trim, interpolate, round_for_send
from .smoothing import SendPolicy, SlewLimiter
from .state import CadreurState

log = logging.getLogger("cadreur.engine")

TICK_S = 0.05  # 20 Hz
PROBE_PERIOD_S = 10.0

# Gate reasons (the UI maps them to i18n strings; cal_key fills the blank)
R_DISARMED = "disarmed"
R_NO_BEAMER = "no_beamer"
R_DISABLED = "disabled"
R_UNCALIBRATED = "uncalibrated"
R_NO_POINTS = "no_points"
R_CALIBRATING = "calibrating"
R_NO_DISTANCE = "no_distance"


class _BeamerRuntime:
    def __init__(self) -> None:
        self.slews = {"scale": SlewLimiter(0.05), "pos_x": SlewLimiter(50.0),
                      "pos_y": SlewLimiter(50.0)}
        self.policy = SendPolicy()
        self.pending: Optional[object] = None  # None | "snap" | seed dict
        self.mode = "idle"  # idle | manual (calibrate drive) | play (interpolated)


class Engine:
    def __init__(self, cfg: Config, state: CadreurState, io, probe_enabled: bool = True) -> None:
        self.cfg = cfg
        self.state = state
        self.io = io  # needs send_scale / send_position (+ query when probing)
        self.probe_enabled = probe_enabled
        self._rt = {b: _BeamerRuntime() for b in showmod.BEAMER_KEYS}
        self._prev_armed = False
        self._last_tick: Optional[float] = None
        self._probe_next = 0.0
        self._probe_idx = 0
        self._probe_misses = 0
        self._probe_inflight = False

    # --- discontinuity hooks (called by the web layer) ------------------------
    def request_snap(self, beamer: Optional[str] = None) -> None:
        for b in ([beamer] if beamer else showmod.BEAMER_KEYS):
            self._rt[b].pending = "snap"

    def request_reseed(self, beamer: str, values: Optional[dict]) -> None:
        """Calibrate-mode exit: re-seed the slew from the layer's actual values
        (the operator just moved it); if feedback failed, snap."""
        self._rt[beamer].pending = dict(values) if values else "snap"

    # --- the tick -------------------------------------------------------------
    def tick(self, now: float) -> None:
        dt = TICK_S if self._last_tick is None else min(0.25, max(0.0, now - self._last_tick))
        self._last_tick = now

        self.state.maybe_autosave(now)

        doc = self.state.show
        look = showmod.active_look(doc)
        sm = doc["smoothing"]
        armed = self.state.armed
        if armed and not self._prev_armed:
            self.request_snap()  # snap, don't slew, on Arm
        self._prev_armed = armed

        abs_m, ever_usable = self.state.distance()

        for b in showmod.BEAMER_KEYS:
            self.state.beamers[b] = self._tick_beamer(b, now, dt, doc, look, sm,
                                                      armed, abs_m, ever_usable)
        self._maybe_probe(now, armed)

    def _tick_beamer(self, b: str, now: float, dt: float, doc: dict, look: Optional[dict],
                     sm: dict, armed: bool, abs_m: Optional[float], ever_usable: bool) -> dict:
        rt = self._rt[b]
        # scale, pos_x (horizontal), pos_y (vertical) are all normalised 0..1.
        for k in PARAMS:
            rt.slews[k].rate_per_s = sm["slew_scale_per_s"]

        beamer = look["beamers"].get(b) if look else None
        cal_key = showmod.cal_key_for(doc, b)
        cset = showmod.cal_set_for(doc, look, b)
        calibrating = self.state.calibrate[b]

        # Calibrate drives manual values independently of the master Arm (you
        # calibrate before arming the show), so it ranks above the arm check.
        if beamer is None:
            reason = R_NO_BEAMER
        elif calibrating:
            reason = R_CALIBRATING
        elif not armed:
            reason = R_DISARMED
        elif not beamer["enabled"]:
            reason = R_DISABLED
        elif cset is None:
            reason = R_UNCALIBRATED  # never fall back to another memory's set
        elif not cset["points"]:
            reason = R_NO_POINTS
        elif not ever_usable:
            reason = R_NO_DISTANCE
        else:
            reason = None
        gate = reason is None

        target = None
        clamped = None
        if cset and cset["points"] and abs_m is not None:
            v, clamped = interpolate(cset["points"], abs_m)
            if v is not None:
                target = apply_trim(v, cset["trim"])

        # Mode: calibrate DRIVES manual values live (takes priority); else armed
        # playback interpolates; else idle (total OSC silence). A mode change
        # resets the send policy so the first tick in the new mode sends.
        if calibrating and beamer is not None:
            mode = "manual"
        elif gate and target is not None:
            mode = "play"
        else:
            mode = "idle"
        if mode != rt.mode:
            rt.policy.reset()
            rt.mode = mode

        addr_scale = beamer["osc_scale"] if beamer else ""
        addr_posv = beamer["osc_posv"] if beamer else ""
        addr_posh = beamer.get("osc_posh", "") if beamer else ""
        values = None
        sending = False

        def emit(v):
            self.io.send_value(addr_scale, v["scale"])
            self.io.send_value(addr_posh, v["pos_x"])  # horizontal
            self.io.send_value(addr_posv, v["pos_y"])  # vertical

        if mode == "manual":
            m = self.state.manual[b]
            values = round_for_send({"scale": m["scale"], "pos_x": m["pos_h"], "pos_y": m["pos_v"]})
            for k in PARAMS:  # keep slews seeded so the exit handover glides
                rt.slews[k].snap(values[k])
            rt.pending = None
            if rt.policy.due(values, now, sm["deadband_scale"], sm["refresh_hz"]):
                emit(values)
                rt.policy.mark_sent(values, now)
                sending = True
        elif mode == "play":
            if rt.pending == "snap":
                for k in PARAMS:
                    rt.slews[k].snap(target[k])
            elif isinstance(rt.pending, dict):
                seed = rt.pending
                rt.slews["scale"].snap(seed.get("scale", target["scale"]))
                rt.slews["pos_x"].snap(seed.get("pos_x", target["pos_x"]))
                rt.slews["pos_y"].snap(seed.get("pos_y", target["pos_y"]))
            rt.pending = None
            slewed = {k: rt.slews[k].step(target[k], dt) for k in PARAMS}
            values = round_for_send(slewed)
            if rt.policy.due(values, now, sm["deadband_scale"], sm["refresh_hz"]):
                emit(values)
                rt.policy.mark_sent(values, now)
                sending = True
        else:
            # Idle: zero OSC. Show the would-be values so the operator sees what
            # arming/enabling would send.
            values = round_for_send(target) if target is not None else None

        return {
            "gate": gate,
            "reason": reason,
            "cal_key": cal_key,
            "layer": beamer["layer"] if beamer else None,
            "enabled": bool(beamer["enabled"]) if beamer else False,
            "calibrating": calibrating,
            "clamped": clamped,
            "values": values,
            "sending": sending,
            "n_points": len(cset["points"]) if cset else 0,
        }

    # --- armed probe (PRD §9): round-robin feedback check every 10 s ----------
    def _maybe_probe(self, now: float, armed: bool) -> None:
        if not (self.probe_enabled and armed) or self._probe_inflight or now < self._probe_next:
            return
        layers = [st["layer"] for st in self.state.beamers.values()
                  if st.get("gate") and st.get("layer")]
        self._probe_next = now + PROBE_PERIOD_S
        if not layers:
            return
        layer = layers[self._probe_idx % len(layers)]
        self._probe_idx += 1
        self._probe_inflight = True

        def work() -> None:
            try:
                res = self.io.query(layer)
                if res is None:
                    self._probe_misses += 1
                    if self._probe_misses >= 2:
                        self.state.millumin = {
                            "ok": False, "latency_ms": None,
                            "warning": f"layer '{layer}' unreachable — or API feedback down",
                        }
                else:
                    self._probe_misses = 0
                    self.state.millumin = {"ok": True, "latency_ms": res["latency_ms"],
                                           "warning": None}
            finally:
                self._probe_inflight = False

        threading.Thread(target=work, daemon=True, name="millumin-probe").start()

    # --- asyncio wrapper ------------------------------------------------------
    async def run(self) -> None:
        log.info("Engine running (20 Hz)")
        try:
            while True:
                try:
                    self.tick(time.monotonic())
                except Exception:  # a bad tick must never kill the engine
                    log.exception("Engine tick failed")
                await asyncio.sleep(TICK_S)
        except asyncio.CancelledError:
            log.info("Engine stopped")
            raise
