"""Smoothing chain building blocks (PRD §8) — pure, clock passed in.

Stages 1-2 (median-of-3 + tau-EMA) run on SSE arrival with measured dt;
stages 4-5 (slew limiter + send policy) run in the 20 Hz engine tick. At
4 cm/min the scrim moves 0.67 mm/s, so seconds of filter lag are invisible.
"""
from __future__ import annotations

from typing import Optional


class Median3:
    """Median of the last 3 samples — SSE hiccup/replay insurance. Fixed size."""

    def __init__(self) -> None:
        self._buf: list[float] = []

    def update(self, x: float) -> float:
        self._buf.append(float(x))
        if len(self._buf) > 3:
            self._buf.pop(0)
        if len(self._buf) < 3:
            return self._buf[-1]  # not enough history: pass through
        return sorted(self._buf)[1]


class TauEma:
    """EMA parameterized in seconds: alpha = dt/(tau+dt) with the measured dt.

    tau=0 -> pass-through. Freezing on stale = simply not calling update();
    the value holds and is NOT reset, so recovery resumes from where it was.
    """

    def __init__(self, tau_s: float) -> None:
        self.tau_s = float(tau_s)
        self.value: Optional[float] = None

    def update(self, x: float, dt: float) -> float:
        x = float(x)
        if self.value is None or self.tau_s <= 0.0:
            self.value = x
        elif dt > 0.0:  # dt<=0 -> no time elapsed -> hold (never snap)
            alpha = dt / (self.tau_s + dt)
            self.value = self.value + alpha * (x - self.value)
        return self.value


class SlewLimiter:
    """Moves the output toward the target at most rate/s — turns any
    discontinuity (look/memory switch, point edit, staircase step) into a
    short glide. snap() seeds/jumps immediately (used on Arm)."""

    def __init__(self, rate_per_s: float) -> None:
        self.rate_per_s = float(rate_per_s)
        self.value: Optional[float] = None

    def snap(self, value: float) -> float:
        self.value = float(value)
        return self.value

    def step(self, target: float, dt: float) -> float:
        target = float(target)
        if self.value is None:
            self.value = target
            return self.value
        max_step = self.rate_per_s * max(0.0, dt)
        delta = target - self.value
        if abs(delta) <= max_step:
            self.value = target
        else:
            self.value += max_step if delta > 0 else -max_step
        return self.value


class SendPolicy:
    """Stage 5: send when any output moved >= its dead-band since the last
    send, OR the refresh period elapsed (absolute values self-heal Millumin
    restarts). One decision per beamer per tick; `now` is injected."""

    def __init__(self) -> None:
        self.last: Optional[dict] = None
        self.last_t: Optional[float] = None

    def reset(self) -> None:
        self.last = None
        self.last_t = None

    def due(self, values: dict, now: float, deadband_scale: float,
            deadband_px: float, refresh_hz: float) -> bool:
        if self.last is None or self.last_t is None:
            return True
        if refresh_hz > 0 and now - self.last_t >= 1.0 / refresh_hz:
            return True
        if abs(values["scale"] - self.last["scale"]) >= deadband_scale:
            return True
        if abs(values["pos_x"] - self.last["pos_x"]) >= deadband_px:
            return True
        if abs(values["pos_y"] - self.last["pos_y"]) >= deadband_px:
            return True
        return False

    def mark_sent(self, values: dict, now: float) -> None:
        self.last = dict(values)
        self.last_t = now
