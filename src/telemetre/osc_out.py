"""OSC output — disabled by default.

When enabled in config, sends the filtered position (meters) as a single OSC
float to `address` on every configured target (`hosts`, or the single `host`),
throttled to `rate_hz`. Import and send are both defensive: a missing
python-osc or an unreachable target can never disrupt the readout.
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
        self._clients = []
        self._last = 0.0
        if not cfg.enabled:
            return
        if SimpleUDPClient is None:
            log.warning("OSC enabled in config but python-osc is not installed; OSC off.")
            return
        for host in cfg.targets:
            try:
                self._clients.append(SimpleUDPClient(host, cfg.port))
                log.info("OSC -> %s:%d %s @ %d Hz", host, cfg.port, cfg.address, cfg.rate_hz)
            except Exception as e:
                log.warning("OSC init failed for %s (%s); target skipped.", host, e)

    @property
    def active(self) -> bool:
        return bool(self._clients)

    def maybe_send(self, value, now: float) -> None:
        """Send `value` to every target if OSC is active and the throttle allows."""
        if not self._clients or value is None:
            return
        if self.cfg.rate_hz > 0 and (now - self._last) < 1.0 / self.cfg.rate_hz:
            return
        self._last = now
        for client in self._clients:
            try:
                client.send_message(self.cfg.address, float(value))
            except OSError as e:
                log.debug("OSC send failed: %s", e)
