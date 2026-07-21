"""Off-network engine tests with a fake clock and fake OSC sender (v2 channels).

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cadreur.config import Config  # noqa: E402
from cadreur.engine import (  # noqa: E402
    R_CALIBRATING,
    R_DISARMED,
    R_NO_POINTS,
    R_UNCALIBRATED,
    TICK_S,
    Engine,
)
from cadreur.show import new_show  # noqa: E402
from cadreur.state import CadreurState  # noqa: E402

F1, R1 = "front/front-1", "rear/rear-1"
F_SCALE, F_POSV, F_POSH = "/front/scale/1", "/front/positionV/1", "/front/positionH/1"
R_SCALE, R_POSV, R_POSH = "/retro/scale/1", "/retro/positionV/1", "/retro/positionH/1"


class FakeIO:
    def __init__(self):
        self.sent = []  # (address, value)

    def send_value(self, address, value):
        self.sent.append((address, value))


def vals_to(io, addr):
    return [v for a, v in io.sent if a == addr]


def any_to(io, *addrs):
    return any(a in addrs for a, _ in io.sent)


def pt(d, s, x, y):
    return {"distance_m": d, "scale": s, "pos_x": x, "pos_y": y}


def _cal(points):
    return {"interp": "linear", "trim": {"scale_mul": 1.0, "dx_px": 0.0, "dy_px": 0.0},
            "points": points}


def make_state():
    cfg = Config()
    cfg.shows.autosave = False
    st = CadreurState(cfg)
    doc = new_show("Test")
    doc["beamers"]["front"]["channels"][0]["calibrations"]["M1"] = _cal(
        [pt(2.0, 0.6, 0.5, 0.4), pt(4.0, 0.4, 0.5, 0.6)])
    doc["beamers"]["rear"]["channels"][0]["calibrations"]["default"] = _cal(
        [pt(3.0, 0.7, 0.5, 0.5)])
    st.show = doc
    return st


def feed_distance(st, abs_m, now=0.0):
    st.sse_status(True)
    st.update_distance(abs_m, abs_m, abs_m, {"connected": True, "stale": False}, now)


class EngineHarness:
    def __init__(self):
        self.state = make_state()
        self.io = FakeIO()
        self.engine = Engine(self.state.cfg, self.state, self.io, probe_enabled=False)
        self.now = 100.0

    def run_ticks(self, n):
        for _ in range(n):
            self.engine.tick(self.now)
            self.now += TICK_S

    def reason(self, key):
        return self.state.channels_state[key]["reason"]

    def values(self, key):
        return self.state.channels_state[key]["values"]


class TestGates(unittest.TestCase):
    def setUp(self):
        self.h = EngineHarness()
        feed_distance(self.h.state, 3.0)

    def test_disarmed_total_silence(self):
        self.h.run_ticks(40)
        self.assertEqual(self.h.io.sent, [])
        self.assertEqual(self.h.reason(F1), R_DISARMED)
        self.assertAlmostEqual(self.h.values(F1)["scale"], 0.5)  # would-be value shown

    def test_armed_sends_both_channel1(self):
        self.h.state.armed = True
        self.h.run_ticks(2)
        self.assertTrue(vals_to(self.h.io, F_SCALE))
        self.assertTrue(vals_to(self.h.io, F_POSV))
        self.assertTrue(vals_to(self.h.io, F_POSH))
        self.assertTrue(vals_to(self.h.io, R_SCALE))  # N=1: constant hold

    def test_snap_on_arm_first_send_is_target(self):
        self.h.state.armed = True
        self.h.run_ticks(1)
        self.assertAlmostEqual(vals_to(self.h.io, F_SCALE)[0], 0.5)  # interp at 3.0, no glide

    def test_disabled_channel_silent(self):
        self.h.state.show["beamers"]["front"]["channels"][0]["enabled"] = False
        self.h.state.armed = True
        self.h.run_ticks(5)
        self.assertFalse(any_to(self.h.io, F_SCALE, F_POSV, F_POSH))
        self.assertTrue(any_to(self.h.io, R_SCALE, R_POSV))

    def test_uncalibrated_memory_inhibits_no_fallback(self):
        self.h.state.show["settings"]["active_lens_memory"] = "M2"
        self.h.state.armed = True
        self.h.run_ticks(5)
        self.assertFalse(any_to(self.h.io, F_SCALE, F_POSV, F_POSH))
        self.assertEqual(self.h.reason(F1), R_UNCALIBRATED)

    def test_empty_points_inhibits(self):
        self.h.state.show["beamers"]["front"]["channels"][0]["calibrations"]["M1"]["points"] = []
        self.h.state.armed = True
        self.h.run_ticks(5)
        self.assertFalse(any_to(self.h.io, F_SCALE, F_POSV, F_POSH))
        self.assertEqual(self.h.reason(F1), R_NO_POINTS)

    def test_calibrate_mode_drives_manual_even_disarmed(self):
        self.h.state.manual["front/front-1"] = {"scale": 0.8, "pos_v": 0.3, "pos_h": 0.7}
        self.h.state.calibrate.add(F1)
        self.h.run_ticks(5)
        self.assertAlmostEqual(vals_to(self.h.io, F_SCALE)[-1], 0.8)
        self.assertAlmostEqual(vals_to(self.h.io, F_POSV)[-1], 0.3)
        self.assertAlmostEqual(vals_to(self.h.io, F_POSH)[-1], 0.7)
        self.assertEqual(self.h.reason(F1), R_CALIBRATING)
        self.assertFalse(any_to(self.h.io, R_SCALE, R_POSV))  # rear disarmed, silent

    def test_two_channels_calibrate_at_once(self):
        self.h.state.manual["front/front-1"] = {"scale": 0.8, "pos_v": 0.5, "pos_h": 0.5}
        self.h.state.manual["rear/rear-1"] = {"scale": 0.2, "pos_v": 0.5, "pos_h": 0.5}
        self.h.state.calibrate.update({F1, R1})
        self.h.run_ticks(3)
        self.assertAlmostEqual(vals_to(self.h.io, F_SCALE)[-1], 0.8)
        self.assertAlmostEqual(vals_to(self.h.io, R_SCALE)[-1], 0.2)


class TestSendPolicy(unittest.TestCase):
    def test_rest_only_refresh_cadence(self):
        h = EngineHarness()
        feed_distance(h.state, 3.0)
        h.state.armed = True
        h.run_ticks(1)
        h.io.sent.clear()
        h.run_ticks(64)  # ~3.2 s at rest, refresh_hz = 1.0
        self.assertEqual(len(vals_to(h.io, F_SCALE)), 3)  # one per refresh period, not 64

    def test_movement_beyond_deadband_sends(self):
        h = EngineHarness()
        feed_distance(h.state, 3.0)
        h.state.armed = True
        h.run_ticks(1)
        h.io.sent.clear()
        feed_distance(h.state, 3.1, now=h.now)  # big move -> slew glides
        h.run_ticks(4)
        self.assertTrue(any_to(h.io, F_SCALE, F_POSV, F_POSH))


class TestStaleHold(unittest.TestCase):
    def test_stale_holds_values_and_keeps_refreshing(self):
        h = EngineHarness()
        feed_distance(h.state, 3.0)
        h.state.armed = True
        h.run_ticks(2)
        h.io.sent.clear()
        h.state.sse_status(False)  # SSE drops: distance value holds
        h.run_ticks(60)  # 3 s
        sends = vals_to(h.io, F_SCALE)
        self.assertGreaterEqual(len(sends), 2)  # refresh cadence continues
        for v in sends:
            self.assertAlmostEqual(v, 0.5)  # held value, unchanged


class TestGlides(unittest.TestCase):
    def test_point_edit_glides_not_jumps(self):
        h = EngineHarness()
        feed_distance(h.state, 3.0)
        h.state.armed = True
        h.run_ticks(2)
        # Same channel, very different calibration -> discontinuity.
        h.state.show["beamers"]["front"]["channels"][0]["calibrations"]["M1"]["points"] = [
            pt(2.0, 0.9, 0.5, 0.2), pt(4.0, 0.9, 0.5, 0.2)]
        h.io.sent.clear()
        h.run_ticks(20)  # 1 s of glide at slew 0.05/s: scale can move <= 0.05
        scales = vals_to(h.io, F_SCALE)
        self.assertTrue(scales)
        self.assertLess(max(scales), 0.5 + 0.06)  # no jump to 0.9
        self.assertGreater(max(scales), 0.5)  # but it is gliding upward

    def test_reseed_after_calibrate_exit(self):
        h = EngineHarness()
        feed_distance(h.state, 3.0)
        h.state.armed = True
        h.run_ticks(2)
        h.state.calibrate.add(F1)
        h.run_ticks(5)
        h.state.calibrate.discard(F1)
        h.engine.request_reseed(F1, {"scale": 0.8, "pos_x": 0.5, "pos_y": 0.5})
        h.io.sent.clear()
        h.run_ticks(1)
        self.assertAlmostEqual(vals_to(h.io, F_SCALE)[0], 0.7975, places=3)  # 0.8 -> 0.5


if __name__ == "__main__":
    unittest.main()
