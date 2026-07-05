"""Off-hardware tests for the TF02-Pro frame parser.

Run on the dev machine with zero third-party deps:
    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from telemetre.frames import (  # noqa: E402
    FrameStreamParser,
    checksum,
    find_frames,
    parse_frame,
    valid_checksum,
)

# Verified test vector: 123 cm, strength 100, temp raw 0x0940 (= 40 C).
GOOD = bytes([0x59, 0x59, 0x7B, 0x00, 0x64, 0x00, 0x40, 0x09, 0xDA])


def make_frame(dist: int, strength: int, temp: int = 0x0940) -> bytes:
    body = bytes(
        [
            0x59,
            0x59,
            dist & 0xFF,
            (dist >> 8) & 0xFF,
            strength & 0xFF,
            (strength >> 8) & 0xFF,
            temp & 0xFF,
            (temp >> 8) & 0xFF,
        ]
    )
    return body + bytes([sum(body) & 0xFF])


class TestChecksum(unittest.TestCase):
    def test_checksum_value(self):
        self.assertEqual(checksum(GOOD), 0xDA)

    def test_valid(self):
        self.assertTrue(valid_checksum(GOOD))

    def test_bad_checksum(self):
        self.assertFalse(valid_checksum(GOOD[:-1] + b"\x00"))

    def test_bad_header(self):
        self.assertFalse(valid_checksum(bytes([0x58, 0x59]) + GOOD[2:]))

    def test_short_frame(self):
        self.assertFalse(valid_checksum(GOOD[:5]))


class TestParse(unittest.TestCase):
    def test_fields(self):
        f = parse_frame(GOOD)
        self.assertIsNotNone(f)
        self.assertEqual(f.distance_cm, 123)
        self.assertEqual(f.strength, 100)
        self.assertAlmostEqual(f.temperature_c, 40.0, places=3)

    def test_bad_returns_none(self):
        self.assertIsNone(parse_frame(GOOD[:-1] + b"\x00"))

    def test_good_is_valid(self):
        self.assertTrue(parse_frame(GOOD).valid)


class TestValidity(unittest.TestCase):
    def test_weak_signal_invalid(self):
        self.assertFalse(parse_frame(make_frame(200, 30)).valid)

    def test_sentinel_4500_invalid(self):
        self.assertFalse(parse_frame(make_frame(4500, 100)).valid)

    def test_zero_distance_invalid(self):
        self.assertFalse(parse_frame(make_frame(0, 100)).valid)

    def test_saturation_invalid(self):
        self.assertFalse(parse_frame(make_frame(65534, 65535)).valid)

    def test_normal_valid(self):
        self.assertTrue(parse_frame(make_frame(345, 250)).valid)

    def test_strength_at_threshold_valid(self):
        self.assertTrue(parse_frame(make_frame(345, 60)).valid)


class TestResync(unittest.TestCase):
    def test_skip_leading_garbage(self):
        frames = list(find_frames(bytes([0x00, 0xFF, 0x12]) + GOOD + GOOD))
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(fr.distance_cm == 123 for fr in frames))

    def test_false_header_then_real_frame(self):
        frames = list(find_frames(bytes([0x59, 0x59, 0x00, 0x00, 0x00]) + GOOD))
        self.assertGreaterEqual(len(frames), 1)
        self.assertEqual(frames[-1].distance_cm, 123)


class TestStreamParser(unittest.TestCase):
    def test_split_across_reads(self):
        p = FrameStreamParser()
        # Deliver GOOD split into two chunks; frame completes on 2nd feed.
        self.assertEqual(p.feed(GOOD[:4]), [])
        out = p.feed(GOOD[4:])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].distance_cm, 123)

    def test_multiple_frames_and_tail(self):
        p = FrameStreamParser()
        out = p.feed(GOOD + GOOD + GOOD[:3])  # 2 full + partial tail
        self.assertEqual(len(out), 2)
        out2 = p.feed(GOOD[3:])  # completes the 3rd
        self.assertEqual(len(out2), 1)


if __name__ == "__main__":
    unittest.main()
