"""Engine: 20 Hz tick — per channel: gate -> target -> slew -> send (v2 model).

Every beamer holds several channels (one per Millumin layer), all driven
continuously and independently. Runs as an asyncio task in the FastAPI lifespan.
Each tick is clock-driven (tick(now) with an injected now) so tests drive it
with a fake clock and a fake OSC sender. Disarmed means TOTAL OSC silence except
channels explicitly in calibrate mode, which drive their manual values live so
the operator can fit every layer at one scrim position. Arming snaps; every
other discontinuity glides through the slew limiters.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from . import show as showmod
from .config import Config
from .interp import PARAMS, apply_trim, interpolate, round_for_send
from .smoothing import SendPolicy, SlewLimiter
from .state import CadreurState

log = logging.getLogger("cadreur.engine")

TICK_S = 0.05  # 20 Hz

# Gate reasons (the UI maps them to i18n strings)
R_DISARMED = "disarmed"
R_DISABLED = "disabled"
R_UNCALIBRATED = "uncalibrated"
R_NO_POINTS = "no_points"
R_CALIBRATING = "calibrating"
R_NO_DISTANCE = "no_distance"


class _ChannelRuntime:
    def __init__(self) -> None:
        self.slews = {k: SlewLimiter(0.05) for k in PARAMS}
        self.policy = SendPolicy()
        self.pending: Optional[object] = None  # None | "snap" | seed dict
        self.mode = "idle"  # idle | manual (calibrate drive) | play (interpolated)


class Engine:
    def __init__(self, cfg: Config, state: CadreurState, io, probe_enabled: bool = False) -> None:
        self.cfg = cfg
        self.state = state
        self.io = io  # needs send_value(address, value)
        self._rt: dict = {}  # keyed "{beamer}/{cid}"
        self._prev_armed = False
        self._last_tick: Optional[float] = None

    # --- discontinuity hooks (called by the web layer) ------------------------
    def request_snap(self) -> None:
        for rt in self._rt.values():
            rt.pending = "snap"

    def request_reseed(self, key: str, values: Optional[dict]) -> None:
        rt = self._rt.get(key)
        if rt is not None:
            rt.pending = dict(values) if values else "snap"

    # --- the tick -------------------------------------------------------------
    def tick(self, now: float) -> None:
        dt = TICK_S if self._last_tick is None else min(0.25, max(0.0, now - self._last_tick))
        self._last_tick = now

        self.state.maybe_autosave(now)

        doc = self.state.show
        sm = doc["smoothing"]
        armed = self.state.armed
        if armed and not self._prev_armed:
            self.request_snap()  # snap, don't slew, on Arm
        self._prev_armed = armed

        abs_m, ever_usable = self.state.distance()

        live_keys = set()
        for b in showmod.BEAMER_KEYS:
            for ch in doc["beamers"][b]["channels"]:
                key = self.state.chan_key(b, ch["id"])
                live_keys.add(key)
                self.state.channels_state[key] = self._tick_channel(
                    b, ch, key, now, dt, sm, armed, abs_m, ever_usable)
        # Drop runtime/state for channels that no longer exist.
        for stale in [k for k in self._rt if k not in live_keys]:
            del self._rt[stale]
        for stale in [k for k in self.state.channels_state if k not in live_keys]:
            del self.state.channels_state[stale]

    def _tick_channel(self, b: str, ch: dict, key: str, now: float, dt: float, sm: dict,
                      armed: bool, abs_m: Optional[float], ever_usable: bool) -> dict:
        rt = self._rt.get(key)
        if rt is None:
            rt = self._rt[key] = _ChannelRuntime()
        for k in PARAMS:  # scale, pos_x (H), pos_y (V) — all normalised 0..1
            rt.slews[k].rate_per_s = sm["slew_scale_per_s"]

        cset = showmod.cal_set_for(self.state.show, b, ch)
        calibrating = key in self.state.calibrate

        # Calibrate drives manual values independently of the master Arm.
        if calibrating:
            reason = R_CALIBRATING
        elif not armed:
            reason = R_DISARMED
        elif not ch["enabled"]:
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

        if calibrating:
            mode = "manual"
        elif gate and target is not None:
            mode = "play"
        else:
            mode = "idle"
        if mode != rt.mode:
            rt.policy.reset()
            rt.mode = mode

        def emit(vv):
            self.io.send_value(ch["osc_scale"], vv["scale"])
            self.io.send_value(ch["osc_posh"], vv["pos_x"])  # horizontal
            self.io.send_value(ch["osc_posv"], vv["pos_y"])  # vertical

        values = None
        sending = False
        if mode == "manual":
            m = self.state.manual_of(key)
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
                for k in PARAMS:
                    rt.slews[k].snap(rt.pending.get(k, target[k]))
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
            "clamped": clamped,
            "values": values,
            "sending": sending,
            "n_points": len(cset["points"]) if cset else 0,
        }

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
