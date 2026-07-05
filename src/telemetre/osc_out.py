"""OSC output provision (future add-on) — disabled by default.

When enabled in config, sends the filtered position (meters) as a single OSC
float to `address`, throttled to `rate_hz`. Import and send are both defensive:
a missing python-osc or an unreachable target can never disrupt the readout.
"""
from __future__ import annotations

import logging

from .config import OscCfg

log = logging.getLogger("telemetre.osc")

try:
    from pythonosc.udp_client import SimpleUDPClient
except Exception:  # library optional
    SimpleUDPClient = None  # type: ignore


class OscSender:
    def __init__(self, cfg: OscCfg) -> None:
        self.cfg = cfg
        self._client = None
        self._last = 0.0
        if not cfg.enabled:
            return
        if SimpleUDPClient is None:
            log.warning("OSC enabled in config but python-osc is not installed; OSC off.")
            return
        try:
            self._client = SimpleUDPClient(cfg.host, cfg.port)
            log.info("OSC -> %s:%d %s @ %d Hz", cfg.host, cfg.port, cfg.address, cfg.rate_hz)
        except Exception as e:
            log.warning("OSC init failed (%s); OSC off.", e)

    @property
    def active(self) -> bool:
        return self._client is not None

    def maybe_send(self, value, now: float) -> None:
        """Send `value` if OSC is active and the throttle interval has elapsed."""
        if self._client is None or value is None:
            return
        if self.cfg.rate_hz > 0 and (now - self._last) < 1.0 / self.cfg.rate_hz:
            return
        self._last = now
        try:
            self._client.send_message(self.cfg.address, float(value))
        except OSError as e:
            log.debug("OSC send failed: %s", e)
