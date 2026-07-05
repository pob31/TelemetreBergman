"""Off-hardware tests for the smoothing filters.

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from telemetre.filters import (  # noqa: E402
    DistanceFilter,
    EMAFilter,
    Hysteresis,
    MedianFilter,
)


class TestMedian(unittest.TestCase):
    def test_spike_rejected(self):
        f = MedianFilter(5)
        for _ in range(5):
            f.update(100.0)
        # One wild sample must not move the median.
        self.assertEqual(f.update(999.0), 100.0)

    def test_tracks_level(self):
        f = MedianFilter(5)
        out = 0.0
        for _ in range(5):
            out = f.update(200.0)
        self.assertEqual(out, 200.0)


class TestEMA(unittest.TestCase):
    def test_first_sample_initializes(self):
        self.assertEqual(EMAFilter(0.25).update(0.0), 0.0)

    def test_settles_monotonically_toward_step(self):
        f = EMAFilter(0.25)
        f.update(0.0)
        prev = 0.0
        y = 0.0
        for _ in range(100):
            y = f.update(100.0)
            self.assertGreaterEqual(y, prev)
            prev = y
        self.assertAlmostEqual(y, 100.0, delta=0.5)

    def test_rejects_bad_alpha(self):
        with self.assertRaises(ValueError):
            EMAFilter(0.0)
        with self.assertRaises(ValueError):
            EMAFilter(1.5)


class TestHysteresis(unittest.TestCase):
    def test_deadband_holds_then_moves(self):
        h = Hysteresis(1.0)
        self.assertEqual(h.update(50.0), 50.0)
        self.assertEqual(h.update(50.4), 50.0)  # within band -> held
        self.assertEqual(h.update(51.2), 51.2)  # exceeds band -> moves


class TestPipeline(unittest.TestCase):
    def test_single_spike_suppressed(self):
        f = DistanceFilter(median_size=5, ema_alpha=0.25, hysteresis_cm=0.75)
        for _ in range(10):
            f.update(300.0)
        base = f.update(300.0)
        after = f.update(999.0)  # one spike
        self.assertLess(abs(after - base), 1.0)

    def test_reset_clears_state(self):
        f = DistanceFilter()
        for _ in range(5):
            f.update(300.0)
        f.reset()
        # After reset the first new sample defines the value again.
        self.assertAlmostEqual(f.update(100.0), 100.0, delta=0.001)


if __name__ == "__main__":
    unittest.main()
