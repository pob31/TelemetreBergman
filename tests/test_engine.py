"""Off-network engine tests with a fake clock and fake OSC sender (PRD §15).

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


class FakeIO:
    def __init__(self):
        self.sent = []  # (kind, layer, values...)

    def send_scale(self, layer, v):
        self.sent.append(("scale", layer, v))

    def send_position(self, layer, x, y):
        self.sent.append(("xy", layer, x, y))


def pt(d, s, x, y):
    return {"distance_m": d, "scale": s, "pos_x": x, "pos_y": y}


def make_state():
    cfg = Config()
    cfg.shows.autosave = False
    st = CadreurState(cfg)
    doc = new_show("Test")
    doc["looks"][0]["beamers"]["front"]["calibrations"]["M1"] = {
        "interp": "linear",
        "trim": {"scale_mul": 1.0, "dx_px": 0.0, "dy_px": 0.0},
        "points": [pt(2.0, 0.6, 960.0, 540.0), pt(4.0, 0.4, 960.0, 600.0)],
    }
    doc["looks"][0]["beamers"]["rear"]["calibrations"]["default"] = {
        "interp": "linear",
        "trim": {"scale_mul": 1.0, "dx_px": 0.0, "dy_px": 0.0},
        "points": [pt(3.0, 0.7, 960.0, 512.0)],
    }
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


class TestGates(unittest.TestCase):
    def setUp(self):
        self.h = EngineHarness()
        feed_distance(self.h.state, 3.0)

    def test_disarmed_total_silence(self):
        self.h.run_ticks(40)
        self.assertEqual(self.h.io.sent, [])
        self.assertEqual(self.h.state.beamers["front"]["reason"], R_DISARMED)
        # Would-be values still shown while gated.
        self.assertAlmostEqual(self.h.state.beamers["front"]["values"]["scale"], 0.5)

    def test_armed_sends_both_beamers(self):
        self.h.state.armed = True
        self.h.run_ticks(2)
        kinds = {(k, layer) for k, layer, *_ in self.h.io.sent}
        self.assertIn(("scale", "front"), kinds)
        self.assertIn(("xy", "front"), kinds)
        self.assertIn(("scale", "rear"), kinds)  # N=1: constant hold

    def test_snap_on_arm_first_send_is_target(self):
        self.h.state.armed = True
        self.h.run_ticks(1)
        first_scale = next(v for k, layer, v in
                           [(k, l, a[0]) for k, l, *a in self.h.io.sent]
                           if k == "scale" and layer == "front")
        self.assertAlmostEqual(first_scale, 0.5)  # interpolated at 3.0, no glide

    def test_disabled_beamer_silent(self):
        self.h.state.show["looks"][0]["beamers"]["front"]["enabled"] = False
        self.h.state.armed = True
        self.h.run_ticks(5)
        self.assertFalse(any(l == "front" for _, l, *_ in self.h.io.sent))
        self.assertTrue(any(l == "rear" for _, l, *_ in self.h.io.sent))

    def test_uncalibrated_memory_inhibits_no_fallback(self):
        self.h.state.show["settings"]["active_lens_memory"] = "M2"
        self.h.state.armed = True
        self.h.run_ticks(5)
        self.assertFalse(any(l == "front" for _, l, *_ in self.h.io.sent))
        self.assertEqual(self.h.state.beamers["front"]["reason"], R_UNCALIBRATED)

    def test_empty_points_inhibits(self):
        self.h.state.show["looks"][0]["beamers"]["front"]["calibrations"]["M1"]["points"] = []
        self.h.state.armed = True
        self.h.run_ticks(5)
        self.assertFalse(any(l == "front" for _, l, *_ in self.h.io.sent))
        self.assertEqual(self.h.state.beamers["front"]["reason"], R_NO_POINTS)

    def test_calibrate_mode_suspends_output(self):
        self.h.state.armed = True
        self.h.run_ticks(2)
        self.h.io.sent.clear()
        self.h.state.calibrate["front"] = True
        self.h.run_ticks(30)
        self.assertFalse(any(l == "front" for _, l, *_ in self.h.io.sent))
        self.assertEqual(self.h.state.beamers["front"]["reason"], R_CALIBRATING)
        self.assertTrue(any(l == "rear" for _, l, *_ in self.h.io.sent))


class TestSendPolicy(unittest.TestCase):
    def test_rest_only_refresh_cadence(self):
        h = EngineHarness()
        feed_distance(h.state, 3.0)
        h.state.armed = True
        h.run_ticks(1)  # initial send
        h.io.sent.clear()
        h.run_ticks(64)  # ~3.2 s at rest, refresh_hz = 1.0
        front_scales = [s for k, l, *s in h.io.sent if l == "front" and k == "scale"]
        self.assertEqual(len(front_scales), 3)  # one per refresh period, not 64

    def test_movement_beyond_deadband_sends(self):
        h = EngineHarness()
        feed_distance(h.state, 3.0)
        h.state.armed = True
        h.run_ticks(1)
        h.io.sent.clear()
        feed_distance(h.state, 3.1, now=h.now)  # big move -> slew glides
        h.run_ticks(4)  # 0.2 s << refresh period
        self.assertTrue(any(l == "front" for _, l, *_ in h.io.sent))


class TestStaleHold(unittest.TestCase):
    def test_stale_holds_values_and_keeps_refreshing(self):
        h = EngineHarness()
        feed_distance(h.state, 3.0)
        h.state.armed = True
        h.run_ticks(2)
        h.io.sent.clear()
        h.state.sse_status(False)  # SSE drops: distance value holds
        h.run_ticks(60)  # 3 s
        sends = [s[0] for k, l, *s in h.io.sent if l == "front" and k == "scale"]
        self.assertGreaterEqual(len(sends), 2)  # refresh cadence continues
        for v in sends:
            self.assertAlmostEqual(v, 0.5)  # held value, unchanged


class TestGlides(unittest.TestCase):
    def test_look_switch_glides_not_jumps(self):
        h = EngineHarness()
        feed_distance(h.state, 3.0)
        h.state.armed = True
        h.run_ticks(2)
        # New look, same layer, very different calibration -> discontinuity.
        from cadreur.show import duplicate_look
        doc = h.state.show
        copy = duplicate_look(doc, doc["looks"][0]["id"])
        copy["beamers"]["front"]["calibrations"]["M1"]["points"] = [
            pt(2.0, 0.9, 960.0, 300.0), pt(4.0, 0.9, 960.0, 300.0)]
        doc["settings"]["active_look"] = copy["id"]
        h.io.sent.clear()
        h.run_ticks(20)  # 1 s of glide at slew 0.05/s: scale can move <= 0.05
        scales = [s[0] for k, l, *s in h.io.sent if l == "front" and k == "scale"]
        self.assertTrue(scales)
        self.assertLess(max(scales), 0.5 + 0.06)  # no jump to 0.9
        self.assertGreater(max(scales), 0.5)  # but it is gliding upward

    def test_reseed_after_calibrate_exit(self):
        h = EngineHarness()
        feed_distance(h.state, 3.0)
        h.state.armed = True
        h.run_ticks(2)
        h.state.calibrate["front"] = True
        h.run_ticks(5)
        # Operator dragged the layer; exit re-seeds from feedback values.
        h.state.calibrate["front"] = False
        h.engine.request_reseed("front", {"scale": 0.8, "pos_x": 900.0, "pos_y": 500.0})
        h.io.sent.clear()
        h.run_ticks(1)
        first = next(s[0] for k, l, *s in h.io.sent if l == "front" and k == "scale")
        self.assertAlmostEqual(first, 0.7975, places=3)  # 0.8 gliding toward 0.5


if __name__ == "__main__":
    unittest.main()
