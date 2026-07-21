"""Off-network tests for cadreur calibration-point math (PRD §7).

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cadreur.interp import (  # noqa: E402
    apply_trim,
    bake_trim,
    insert_point,
    interpolate,
    normalize_points,
    round_for_send,
)


def pt(d, s, x, y):
    return {"distance_m": d, "scale": s, "pos_x": x, "pos_y": y}


POINTS = [  # the PRD §6 example set
    pt(2.10, 0.620, 960.0, 540.0),
    pt(3.20, 0.535, 960.0, 574.0),
    pt(4.30, 0.458, 960.0, 610.0),
]


class TestInterpolate(unittest.TestCase):
    def test_exact_at_points(self):
        for p in POINTS:
            v, clamped = interpolate(POINTS, p["distance_m"])
            self.assertIsNone(clamped)
            self.assertAlmostEqual(v["scale"], p["scale"])
            self.assertAlmostEqual(v["pos_x"], p["pos_x"])
            self.assertAlmostEqual(v["pos_y"], p["pos_y"])

    def test_midpoint_worked_example(self):
        # PRD §7: at 2.65, t=0.5 -> scale 0.5775, pos_y 557.0
        v, clamped = interpolate(POINTS, 2.65)
        self.assertIsNone(clamped)
        self.assertAlmostEqual(v["scale"], 0.5775)
        self.assertAlmostEqual(v["pos_y"], 557.0)

    def test_clamp_low(self):
        v, clamped = interpolate(POINTS, 1.00)
        self.assertEqual(clamped, "low")
        self.assertAlmostEqual(v["scale"], 0.620)
        self.assertAlmostEqual(v["pos_y"], 540.0)

    def test_clamp_high(self):
        v, clamped = interpolate(POINTS, 9.99)
        self.assertEqual(clamped, "high")
        self.assertAlmostEqual(v["scale"], 0.458)
        self.assertAlmostEqual(v["pos_y"], 610.0)

    def test_n0_inhibits(self):
        v, clamped = interpolate([], 3.0)
        self.assertIsNone(v)
        self.assertIsNone(clamped)

    def test_n1_constant_hold(self):
        v, clamped = interpolate([POINTS[1]], 99.0)
        self.assertIsNone(clamped)
        self.assertAlmostEqual(v["scale"], 0.535)


class TestEdits(unittest.TestCase):
    def test_sorted_insert(self):
        pts, replaced = insert_point(POINTS, pt(2.80, 0.57, 960.0, 555.0))
        self.assertFalse(replaced)
        self.assertEqual([p["distance_m"] for p in pts], [2.10, 2.80, 3.20, 4.30])

    def test_merge_replace_within_3cm(self):
        pts, replaced = insert_point(POINTS, pt(3.21, 0.540, 961.0, 575.0))
        self.assertTrue(replaced)
        self.assertEqual(len(pts), 3)
        self.assertAlmostEqual(pts[1]["distance_m"], 3.21)
        self.assertAlmostEqual(pts[1]["scale"], 0.540)

    def test_no_merge_beyond_3cm(self):
        pts, replaced = insert_point(POINTS, pt(3.24, 0.540, 961.0, 575.0))
        self.assertFalse(replaced)
        self.assertEqual(len(pts), 4)

    def test_dedup_1mm_keeps_later(self):
        pts = normalize_points([pt(3.2000, 0.5, 0, 0), pt(3.2005, 0.6, 1, 1)])
        self.assertEqual(len(pts), 1)
        self.assertAlmostEqual(pts[0]["scale"], 0.6)  # later one wins

    def test_defensive_sort_and_malformed_drop(self):
        pts = normalize_points([pt(4.0, 0.4, 0, 0), {"distance_m": "junk"}, pt(2.0, 0.6, 0, 0)])
        self.assertEqual([p["distance_m"] for p in pts], [2.0, 4.0])


class TestTrimAndRounding(unittest.TestCase):
    def test_apply_trim(self):
        v = apply_trim({"scale": 0.5, "pos_x": 960.0, "pos_y": 540.0},
                       {"scale_mul": 1.02, "dx_px": -3.0, "dy_px": 5.0})
        self.assertAlmostEqual(v["scale"], 0.51)
        self.assertAlmostEqual(v["pos_x"], 957.0)
        self.assertAlmostEqual(v["pos_y"], 545.0)

    def test_bake_trim(self):
        baked = bake_trim(POINTS, {"scale_mul": 2.0, "dx_px": 1.0, "dy_px": -1.0})
        self.assertAlmostEqual(baked[0]["scale"], 1.240)
        self.assertAlmostEqual(baked[0]["pos_x"], 961.0)
        self.assertAlmostEqual(baked[0]["pos_y"], 539.0)
        self.assertAlmostEqual(baked[0]["distance_m"], 2.10)  # distance untouched

    def test_rounding(self):
        # All outputs are normalised 0..1 -> 4 dp.
        v = round_for_send({"scale": 0.123456, "pos_x": 0.0, "pos_y": 0.876543})
        self.assertEqual(v["scale"], 0.1235)
        self.assertEqual(v["pos_y"], 0.8765)


if __name__ == "__main__":
    unittest.main()
