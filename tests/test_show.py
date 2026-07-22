"""Off-network tests for the cadreur show file — v2 channels + v1 migration.

    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cadreur.show import (  # noqa: E402
    DEFAULT_CHANNELS,
    VERSION,
    ShowError,
    add_channel,
    cal_key_for,
    cal_set_for,
    channels_of,
    delete_channel,
    ensure_cal_set,
    get_channel,
    load_show,
    new_show,
    normalize,
    rename_channel,
    save_show,
    set_channel_osc,
    valid_layer_name,
    valid_osc_addr,
)


def _pt(d, s, x, y):
    return {"distance_m": d, "scale": s, "pos_x": x, "pos_y": y}


def _cal(points):
    return {"interp": "linear", "trim": {"scale_mul": 1.0, "dx_px": 0, "dy_px": 0},
            "points": points}


class TestNewShow(unittest.TestCase):
    def test_shape(self):
        doc = new_show("X")
        self.assertEqual(doc["version"], VERSION)
        self.assertEqual(len(doc["beamers"]["front"]["channels"]), DEFAULT_CHANNELS)
        self.assertEqual(len(doc["beamers"]["rear"]["channels"]), DEFAULT_CHANNELS)
        self.assertEqual(doc["beamers"]["front"]["channels"][0]["osc_scale"], "/front/scale/1")
        self.assertEqual(doc["beamers"]["rear"]["channels"][3]["osc_posh"], "/retro/positionH/4")
        self.assertEqual(doc["beamers"]["front"]["channels"][0]["osc_show"], "/front/layer/1")
        self.assertEqual(doc["beamers"]["rear"]["channels"][0]["osc_show"], "/retro/layer/1")

    def test_normalize_is_fixed_point(self):
        doc = normalize(new_show())
        self.assertEqual(normalize(doc), doc)


class TestNormalize(unittest.TestCase):
    def test_version_missing_refused(self):
        with self.assertRaises(ShowError):
            normalize({"beamers": {}})

    def test_version_newer_refused(self):
        with self.assertRaises(ShowError) as cm:
            normalize({"version": VERSION + 1})
        self.assertIn("newer Cadreur", str(cm.exception))

    def test_unknown_keys_ignored(self):
        raw = new_show()
        raw["mystery"] = 1
        raw["beamers"]["front"]["channels"][0]["surprise"] = 2
        doc = normalize(raw)
        self.assertNotIn("mystery", doc)
        self.assertNotIn("surprise", doc["beamers"]["front"]["channels"][0])

    def test_armed_never_serialized(self):
        raw = new_show()
        raw["armed"] = True
        self.assertNotIn("armed", normalize(raw))

    def test_defensive_point_sort(self):
        raw = new_show()
        raw["beamers"]["front"]["channels"][0]["calibrations"]["M1"] = _cal(
            [_pt(4, 0.4, 0.5, 0.6), _pt(2, 0.6, 0.5, 0.4)])
        doc = normalize(raw)
        pts = doc["beamers"]["front"]["channels"][0]["calibrations"]["M1"]["points"]
        self.assertEqual([p["distance_m"] for p in pts], [2.0, 4.0])

    def test_bad_active_memory_falls_back(self):
        raw = new_show()
        raw["settings"]["active_lens_memory"] = "M9"
        self.assertEqual(normalize(raw)["settings"]["active_lens_memory"], "M1")

    def test_empty_channels_get_defaults(self):
        raw = new_show()
        raw["beamers"]["front"]["channels"] = []
        self.assertEqual(len(normalize(raw)["beamers"]["front"]["channels"]), DEFAULT_CHANNELS)


class TestMigrationV1(unittest.TestCase):
    def _v1(self):
        return {
            "app": "cadreur", "version": 1,
            "settings": {"active_look": "look-1", "active_lens_memory": "M2"},
            "lens_memories": ["M1", "M2", "M3"],
            "looks": [{"id": "look-1", "name": "L", "beamers": {
                "front": {"layer": "scope-front", "enabled": True,
                          "osc_scale": "/front/scale/1", "osc_posv": "/front/positionV/1",
                          "osc_posh": "/front/positionH/1",
                          "calibrations": {"M2": _cal([_pt(2, 0.6, 0.5, 0.4), _pt(4, 0.4, 0.5, 0.6)])}},
                "rear": {"layer": "scope-rear", "enabled": True,
                         "osc_scale": "/retro/scale/1", "osc_posv": "/retro/positionV/1",
                         "osc_posh": "/retro/positionH/1",
                         "calibrations": {"default": _cal([_pt(3, 0.7, 0.5, 0.5)])}}}}],
        }

    def test_v1_migrates_preserving_channel1(self):
        doc = normalize(self._v1())
        self.assertEqual(doc["version"], VERSION)
        self.assertNotIn("looks", doc)
        self.assertEqual(doc["settings"]["active_lens_memory"], "M2")
        f = doc["beamers"]["front"]["channels"]
        self.assertEqual(len(f), DEFAULT_CHANNELS)
        self.assertEqual(len(f[0]["calibrations"]["M2"]["points"]), 2)  # preserved into ch1
        r = doc["beamers"]["rear"]["channels"]
        self.assertEqual(len(r[0]["calibrations"]["default"]["points"]), 1)


class TestResolution(unittest.TestCase):
    def setUp(self):
        self.doc = new_show()
        self.f1 = self.doc["beamers"]["front"]["channels"][0]
        self.f1["calibrations"]["M1"] = _cal([_pt(2, 0.6, 0.5, 0.4)])

    def test_front_resolves_active_memory(self):
        self.assertIsNotNone(cal_set_for(self.doc, "front", self.f1))

    def test_missing_memory_inhibits_no_fallback(self):
        self.doc["settings"]["active_lens_memory"] = "M2"
        self.assertIsNone(cal_set_for(self.doc, "front", self.f1))

    def test_rear_uses_default_key(self):
        r1 = self.doc["beamers"]["rear"]["channels"][0]
        self.assertEqual(cal_key_for(self.doc, "rear"), "default")
        self.assertIsNone(cal_set_for(self.doc, "rear", r1))  # none captured yet

    def test_ensure_cal_set_creates_lazily(self):
        r1 = self.doc["beamers"]["rear"]["channels"][0]
        s = ensure_cal_set(self.doc, "rear", r1)
        self.assertEqual(s["points"], [])
        self.assertIsNotNone(cal_set_for(self.doc, "rear", r1))


class TestChannelOps(unittest.TestCase):
    def setUp(self):
        self.doc = new_show()

    def test_add_channel_next_index(self):
        ch = add_channel(self.doc, "front", "Extra")
        self.assertEqual(ch["osc_scale"], "/front/scale/5")
        self.assertEqual(ch["name"], "Extra")
        self.assertEqual(len(channels_of(self.doc, "front")), 5)

    def test_delete_channel(self):
        cid = channels_of(self.doc, "front")[0]["id"]
        delete_channel(self.doc, "front", cid)
        self.assertEqual(len(channels_of(self.doc, "front")), 3)

    def test_delete_last_refused(self):
        for cid in [c["id"] for c in channels_of(self.doc, "rear")[1:]]:
            delete_channel(self.doc, "rear", cid)
        last = channels_of(self.doc, "rear")[0]["id"]
        with self.assertRaises(ShowError):
            delete_channel(self.doc, "rear", last)

    def test_rename(self):
        cid = channels_of(self.doc, "front")[0]["id"]
        rename_channel(self.doc, "front", cid, "Scope")
        self.assertEqual(get_channel(self.doc, "front", cid)["name"], "Scope")

    def test_set_osc_valid_and_invalid(self):
        cid = channels_of(self.doc, "front")[0]["id"]
        set_channel_osc(self.doc, "front", cid, {"osc_scale": "/front/scale/9"})
        self.assertEqual(get_channel(self.doc, "front", cid)["osc_scale"], "/front/scale/9")
        with self.assertRaises(ShowError):
            set_channel_osc(self.doc, "front", cid, {"osc_scale": "bad addr"})


class TestFiles(unittest.TestCase):
    def test_save_load_round_trip_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "show.json"
            saved = save_show(path, new_show("Test"))
            self.assertIsNotNone(saved["meta"]["saved_at"])
            self.assertFalse(path.with_name("show.json.tmp").exists())
            self.assertEqual(load_show(path), saved)

    def test_load_garbage_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ShowError):
                load_show(path)

    def test_validators(self):
        self.assertTrue(valid_layer_name("scope-front"))
        self.assertFalse(valid_layer_name("has space"))
        self.assertTrue(valid_osc_addr("/front/scale/1"))
        self.assertFalse(valid_osc_addr("front/scale/1"))


if __name__ == "__main__":
    unittest.main()
