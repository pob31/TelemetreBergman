"""Off-hardware tests for config loading — OSC multi-target resolution.

    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from telemetre.config import load_config  # noqa: E402


def _load(toml_text: str):
    with tempfile.NamedTemporaryFile(
        "w", suffix=".toml", delete=False, encoding="utf-8"
    ) as f:
        f.write(toml_text)
        path = f.name
    try:
        return load_config(path)
    finally:
        os.unlink(path)


class TestOscTargets(unittest.TestCase):
    def test_defaults(self):
        cfg = _load("")
        self.assertFalse(cfg.osc.enabled)
        self.assertEqual(cfg.osc.targets, ["127.0.0.1"])

    def test_hosts_list_wins_over_host(self):
        cfg = _load(
            '[osc]\nenabled = true\n'
            'hosts = ["192.168.0.12", "192.168.0.11"]\nhost = "10.0.0.1"\n'
        )
        self.assertEqual(cfg.osc.targets, ["192.168.0.12", "192.168.0.11"])

    def test_empty_hosts_falls_back_to_host(self):
        cfg = _load('[osc]\nhosts = []\nhost = "192.168.0.12"\n')
        self.assertEqual(cfg.osc.targets, ["192.168.0.12"])

    def test_unknown_keys_ignored(self):
        # An old/newer config must never crash the loader (house rule).
        cfg = _load('[osc]\nhosts = ["192.168.0.12"]\nfuture_knob = 42\n')
        self.assertEqual(cfg.osc.targets, ["192.168.0.12"])


if __name__ == "__main__":
    unittest.main()
