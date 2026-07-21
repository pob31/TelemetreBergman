"""Off-network tests for the cadreur smoothing chain (PRD §8).

    python -m unittest discover -s tests -v
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cadreur.smoothing import Median3, SendPolicy, SlewLimiter, TauEma  # noqa: E402


class TestMedian3(unittest.TestCase):
    def test_single_hiccup_rejected(self):
        m = Median3()
        m.update(3.0)
        m.update(3.0)
        self.assertEqual(m.update(9.9), 3.0)  # one wild SSE sample

    def test_tracks_level(self):
        m = Median3()
        out = 0.0
        for _ in range(3):
            out = m.update(2.5)
        self.assertEqual(out, 2.5)


class TestTauEma(unittest.TestCase):
    def test_pendulum_attenuation(self):
        # 0.5 Hz +/-1 cm sway with tau=5 s -> ~16x attenuation, residual < 1 mm.
        ema = TauEma(5.0)
        dt = 0.05  # 20 Hz feed
        residual = 0.0
        n = int(60.0 / dt)
        for i in range(n):
            t = i * dt
            x = 3.0 + 0.01 * math.sin(2 * math.pi * 0.5 * t)
            y = ema.update(x, dt)
            if t > 50.0:  # after the transient settles
                residual = max(residual, abs(y - 3.0))
        self.assertLess(residual, 0.001)

    def test_ramp_lag_is_tau_times_v(self):
        # 4 cm/min ramp: steady-state lag = tau*v ~ 3.3 mm.
        tau, v, dt = 5.0, 0.04 / 60.0, 0.05
        ema = TauEma(tau)
        x = y = 0.0
        for i in range(int(60.0 / dt)):
            x = v * i * dt
            y = ema.update(x, dt)
        self.assertAlmostEqual(x - y, tau * v, delta=0.0005)

    def test_freeze_resume_without_reset(self):
        ema = TauEma(5.0)
        for _ in range(100):
            ema.update(3.0, 0.05)
        held = ema.value
        # Stale: no updates happen; value simply holds.
        self.assertAlmostEqual(ema.value, held)
        # Recovery near the held value: no jump.
        y = ema.update(3.01, 0.05)
        self.assertLess(abs(y - held), 0.001)

    def test_zero_tau_passthrough(self):
        ema = TauEma(0.0)
        ema.update(1.0, 0.05)
        self.assertEqual(ema.update(2.0, 0.05), 2.0)


class TestSlewLimiter(unittest.TestCase):
    def test_step_response_duration(self):
        # 0.1 step at 0.05/s -> 2 s glide.
        sl = SlewLimiter(0.05)
        sl.snap(0.5)
        dt, ticks = 0.05, 0
        while sl.step(0.6, dt) != 0.6:
            ticks += 1
            self.assertLess(ticks, 100, "never reached target")
        self.assertAlmostEqual(ticks * dt, 2.0, delta=2 * dt)

    def test_snap_on_arm(self):
        sl = SlewLimiter(0.05)
        sl.snap(0.9)  # arming snaps, no glide
        self.assertEqual(sl.value, 0.9)
        self.assertEqual(sl.step(0.9, 0.05), 0.9)

    def test_tracking_speed_never_limited(self):
        # Normal tracking (~0.0001 scale/s) is far below the slew rate.
        sl = SlewLimiter(0.05)
        sl.snap(0.5)
        self.assertEqual(sl.step(0.5001, 0.05), 0.5001)


class TestSendPolicy(unittest.TestCase):
    # scale and pos_y (vertical) are both normalised 0..1 -> one dead-band.
    V = {"scale": 0.5, "pos_x": 0.0, "pos_y": 0.5}

    def decide(self, sp, values, now):
        return sp.due(values, now, 0.0005, 1.0)

    def test_first_send_always_due(self):
        sp = SendPolicy()
        self.assertTrue(self.decide(sp, self.V, 0.0))

    def test_deadband_suppression(self):
        sp = SendPolicy()
        sp.mark_sent(self.V, 0.0)
        tiny = {"scale": 0.5002, "pos_x": 0.0, "pos_y": 0.5002}
        self.assertFalse(self.decide(sp, tiny, 0.5))

    def test_deadband_exceeded_sends_on_vertical(self):
        sp = SendPolicy()
        sp.mark_sent(self.V, 0.0)
        moved = {"scale": 0.5, "pos_x": 0.0, "pos_y": 0.5006}
        self.assertTrue(self.decide(sp, moved, 0.1))

    def test_refresh_due_sends_even_at_rest(self):
        sp = SendPolicy()
        sp.mark_sent(self.V, 0.0)
        self.assertFalse(self.decide(sp, self.V, 0.9))
        self.assertTrue(self.decide(sp, self.V, 1.0))


if __name__ == "__main__":
    unittest.main()
