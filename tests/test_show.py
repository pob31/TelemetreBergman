"""Off-network tests for the cadreur show file (PRD §6).

    python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cadreur.show import (  # noqa: E402
    ShowError,
    active_look,
    cal_set_for,
    create_look,
    delete_look,
    duplicate_look,
    ensure_cal_set,
    load_show,
    new_show,
    normalize,
    save_show,
    startup_backup,
    valid_layer_name,
)

EXAMPLE = Path(__file__).parent.parent / "shows" / "example-show.json"


class TestNormalize(unittest.TestCase):
    def test_round_trip_example(self):
        raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        doc = normalize(raw)
        self.assertEqual(doc["version"], 1)
        self.assertEqual(doc["settings"]["active_look"], "cinemascope")
        front = doc["looks"][0]["beamers"]["front"]
        self.assertEqual(front["layer"], "scope-front")
        self.assertEqual(len(front["calibrations"]["M1"]["points"]), 3)
        # Normalizing its own output is a fixed point.
        self.assertEqual(normalize(doc), doc)

    def test_unknown_keys_ignored(self):
        raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        raw["future_field"] = {"x": 1}
        raw["looks"][0]["mystery"] = True
        doc = normalize(raw)
        self.assertNotIn("future_field", doc)
        self.assertNotIn("mystery", doc["looks"][0])

    def test_version_missing_refused(self):
        raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        del raw["version"]
        with self.assertRaises(ShowError):
            normalize(raw)

    def test_version_newer_refused(self):
        raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        raw["version"] = 2
        with self.assertRaises(ShowError) as cm:
            normalize(raw)
        self.assertIn("newer Cadreur", str(cm.exception))

    def test_armed_never_serialized(self):
        raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        raw["armed"] = True
        raw["settings"]["armed"] = True
        doc = normalize(raw)
        self.assertNotIn("armed", doc)
        self.assertNotIn("armed", doc["settings"])

    def test_defensive_point_sort(self):
        raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        pts = raw["looks"][0]["beamers"]["front"]["calibrations"]["M1"]["points"]
        pts.reverse()
        doc = normalize(raw)
        out = doc["looks"][0]["beamers"]["front"]["calibrations"]["M1"]["points"]
        self.assertEqual([p["distance_m"] for p in out], [2.10, 3.20, 4.30])

    def test_bad_active_refs_fall_back(self):
        raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        raw["settings"] = {"active_look": "nope", "active_lens_memory": "M9"}
        doc = normalize(raw)
        self.assertEqual(doc["settings"]["active_look"], "cinemascope")
        self.assertEqual(doc["settings"]["active_lens_memory"], "M1")

    def test_beamer_keys_restricted(self):
        raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        raw["looks"][0]["beamers"]["side"] = {"layer": "x", "calibrations": {}}
        doc = normalize(raw)
        self.assertNotIn("side", doc["looks"][0]["beamers"])


class TestResolution(unittest.TestCase):
    def setUp(self):
        self.doc = normalize(json.loads(EXAMPLE.read_text(encoding="utf-8")))

    def test_front_resolves_active_memory(self):
        s = cal_set_for(self.doc, active_look(self.doc), "front")
        self.assertEqual(len(s["points"]), 3)

    def test_missing_lens_memory_inhibits(self):
        self.doc["settings"]["active_lens_memory"] = "M2"  # no M2 set: no fallback
        self.assertIsNone(cal_set_for(self.doc, active_look(self.doc), "front"))

    def test_rear_uses_default_key(self):
        s = cal_set_for(self.doc, active_look(self.doc), "rear")
        self.assertEqual(s["points"], [])

    def test_ensure_cal_set_creates_lazily(self):
        self.doc["settings"]["active_lens_memory"] = "M2"
        s = ensure_cal_set(self.doc, active_look(self.doc), "front")
        self.assertEqual(s["points"], [])
        self.assertIsNotNone(cal_set_for(self.doc, active_look(self.doc), "front"))


class TestLookOps(unittest.TestCase):
    def setUp(self):
        self.doc = new_show()

    def test_create_and_duplicate_unique_ids(self):
        a = create_look(self.doc, "Acteurs")
        b = duplicate_look(self.doc, a["id"])
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(self.doc["looks"]), 3)

    def test_delete_last_look_refused(self):
        with self.assertRaises(ShowError):
            delete_look(self.doc, self.doc["looks"][0]["id"])

    def test_delete_active_switches(self):
        create_look(self.doc, "Deux")
        first = self.doc["looks"][0]["id"]
        self.doc["settings"]["active_look"] = first
        delete_look(self.doc, first)
        self.assertEqual(self.doc["settings"]["active_look"], self.doc["looks"][0]["id"])


class TestFiles(unittest.TestCase):
    def test_save_load_round_trip_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "show.json"
            doc = new_show("Test")
            saved = save_show(path, doc)
            self.assertIsNotNone(saved["meta"]["saved_at"])
            self.assertFalse(path.with_name("show.json.tmp").exists())
            loaded = load_show(path)
            self.assertEqual(loaded, saved)

    def test_load_garbage_raises_showerror(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ShowError):
                load_show(path)

    def test_startup_backup_rotation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "show.json"
            save_show(path, new_show())
            made = []
            for i in range(12):
                dest = startup_backup(path, keep=10)
                # Same-second stamps overwrite; force distinct names for the test.
                forced = dest.with_name(f"show-{i:04d}.json")
                dest.rename(forced)
                made.append(forced)
            backups = list((path.parent / "backups").glob("show-*.json"))
            self.assertLessEqual(len(backups), 10)  # rotation pruned to the newest 10

    def test_layer_name_validation(self):
        self.assertTrue(valid_layer_name("scope-front"))
        self.assertTrue(valid_layer_name("Layer_2.b"))
        self.assertFalse(valid_layer_name("has space"))
        self.assertFalse(valid_layer_name(""))
        self.assertFalse(valid_layer_name("accént"))


if __name__ == "__main__":
    unittest.main()
