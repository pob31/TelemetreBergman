"""Signal smoothing for the distance readout — pure, no I/O.

The TF02-Pro can emit up to ~100 valid frames/second, but a human reads a
number ~10-20x/second. We use the surplus samples to reject spikes and
present a stable-but-responsive value:

    raw valid distance (cm)
        -> median-of-N        (rejects single-sample spikes / dropouts)
        -> exponential MA      (smooths, sets the "feel"/time constant)
        -> display hysteresis  (kills last-digit flicker when the cart is still)

All parameters are tunable from config. Dependency-free so it can be
unit-tested off-hardware. See tests/test_filters.py.
"""
from __future__ import annotations

import statistics
from collections import deque
from typing import Optional


class MedianFilter:
    """Median over the last `size` samples. Odd `size` recommended."""

    def __init__(self, size: int = 5) -> None:
        if size < 1:
            raise ValueError("median size must be >= 1")
        self.size = size
        self._buf: deque[float] = deque(maxlen=size)

    def update(self, x: float) -> float:
        self._buf.append(x)
        return statistics.median(self._buf)

    def reset(self) -> None:
        self._buf.clear()


class EMAFilter:
    """Exponential moving average: y += alpha * (x - y).

    Larger alpha -> snappier/noisier; smaller -> smoother/laggier.
    With a fixed publish tick dt, time constant tau ~= dt*(1-alpha)/alpha.
    """

    def __init__(self, alpha: float = 0.25) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        self.alpha = alpha
        self._y: Optional[float] = None

    def update(self, x: float) -> float:
        if self._y is None:
            self._y = x
        else:
            self._y += self.alpha * (x - self._y)
        return self._y

    def reset(self) -> None:
        self._y = None


class Hysteresis:
    """Deadband: hold the last value until the input moves by >= threshold.

    Applied to the *displayed* number only, so a still cart shows a rock-steady
    reading. The unfiltered smoothed value is still available for OSC/logging.
    """

    def __init__(self, threshold: float = 0.75) -> None:
        if threshold < 0:
            raise ValueError("threshold must be >= 0")
        self.threshold = threshold
        self._held: Optional[float] = None

    def update(self, x: float) -> float:
        if self._held is None or abs(x - self._held) >= self.threshold:
            self._held = x
        return self._held

    def reset(self) -> None:
        self._held = None


class DistanceFilter:
    """median -> EMA -> hysteresis pipeline over valid distance samples (cm)."""

    def __init__(
        self,
        median_size: int = 5,
        ema_alpha: float = 0.25,
        hysteresis_cm: float = 0.75,
    ) -> None:
        self.median = MedianFilter(median_size)
        self.ema = EMAFilter(ema_alpha)
        self.hyst = Hysteresis(hysteresis_cm)

    def update(self, distance_cm: float) -> float:
        m = self.median.update(distance_cm)
        e = self.ema.update(m)
        return self.hyst.update(e)

    def reset(self) -> None:
        self.median.reset()
        self.ema.reset()
        self.hyst.reset()
