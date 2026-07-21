"""Configuration loading (TOML + defaults).

Everything has a sane default so the app runs even with no config file. A
config path can be given via the TELEMETRE_CONFIG env var, else `config.toml`
at the repo root is used if present. Unknown keys are ignored so an old config
never crashes a newer build.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class SerialCfg:
    # Sensor is on a Waveshare Serial Expansion HAT (SC16IS752 over I2C @ 0x48),
    # channel B => /dev/ttySC1. Requires, in /boot/firmware/config.txt:
    #   dtparam=i2c_arm=on
    #   dtoverlay=sc16is752-i2c,int_pin=24,addr=0x48
    # CRITICAL: this is an *I2C* board — never load the *SPI* overlay
    # (sc16is75x-spi) for it. The SPI driver then registers a phantom ttySC0 and
    # close() dead-locks in synchronize_irq (unkillable D-state, needs a reboot).
    port: str = "/dev/ttySC1"
    baud: int = 115200
    candidates: List[str] = field(
        default_factory=lambda: [
            "/dev/ttySC1",  # Serial Expansion HAT channel B (our sensor)
            "/dev/ttySC0",  # channel A
            "/dev/serial/by-id/*",  # stable USB-serial names
            "/dev/ttyUSB0",
            "/dev/ttyACM0",
        ]
    )


@dataclass
class FilterCfg:
    median_size: int = 5
    ema_alpha: float = 0.25
    hysteresis_cm: float = 0.75
    publish_hz: int = 20
    stale_after_ms: int = 400


@dataclass
class PositionCfg:
    sign: int = 1  # +1 or -1: which way the relative position counts
    state_file: str = "state.json"  # persisted tare/zero + sign


@dataclass
class WebCfg:
    host: str = "0.0.0.0"
    port: int = 80


@dataclass
class OscCfg:
    enabled: bool = False
    host: str = "127.0.0.1"  # single target, used only when `hosts` is empty
    hosts: List[str] = field(default_factory=list)  # fan-out targets (same port)
    port: int = 9000
    address: str = "/telemetre/position"
    rate_hz: int = 30

    @property
    def targets(self) -> List[str]:
        return list(self.hosts) if self.hosts else [self.host]


@dataclass
class Config:
    serial: SerialCfg = field(default_factory=SerialCfg)
    filter: FilterCfg = field(default_factory=FilterCfg)
    position: PositionCfg = field(default_factory=PositionCfg)
    web: WebCfg = field(default_factory=WebCfg)
    osc: OscCfg = field(default_factory=OscCfg)


def _section(cls, data: dict, name: str):
    raw = data.get(name) or {}
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in raw.items() if k in known})


def load_config(path: Optional[str] = None) -> Config:
    path = path or os.environ.get("TELEMETRE_CONFIG")
    candidate = Path(path) if path else REPO_ROOT / "config.toml"
    data: dict = {}
    if candidate.exists():
        data = tomllib.loads(candidate.read_text(encoding="utf-8"))
    return Config(
        serial=_section(SerialCfg, data, "serial"),
        filter=_section(FilterCfg, data, "filter"),
        position=_section(PositionCfg, data, "position"),
        web=_section(WebCfg, data, "web"),
        osc=_section(OscCfg, data, "osc"),
    )
