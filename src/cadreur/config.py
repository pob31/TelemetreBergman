"""Configuration loading (TOML + defaults) — machine settings only (PRD §13).

Everything the operator edits lives in the show file, not here. A config path
can be given via the CADREUR_CONFIG env var, else `cadreur.toml` at the repo
root is used if present. Unknown keys are ignored so an old config never
crashes a newer build.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class TelemetreCfg:
    url: str = "http://192.168.1.36"  # cadreur appends /stream
    stale_after_ms: int = 1500


@dataclass
class MilluminCfg:
    host: str = "127.0.0.1"
    port: int = 5000  # Millumin OSC input
    # Feedback/readback is only used with the standard /layer:NAME API. Custom
    # Interaction addresses (the default here) don't answer /? queries, so it is
    # OFF by default: no listener is bound and the armed probe is disabled.
    feedback: bool = False
    feedback_port: int = 8001  # (8000 is often taken); must match Millumin's feedback dest
    feedback_timeout_ms: int = 1500


@dataclass
class WebCfg:
    host: str = "127.0.0.1"  # 0.0.0.0 hands control to the stage LAN — deliberate choice
    port: int = 8080


@dataclass
class ShowsCfg:
    dir: str = "shows"  # repo-relative
    autosave: bool = True
    autosave_debounce_s: float = 5.0


@dataclass
class Config:
    telemetre: TelemetreCfg = field(default_factory=TelemetreCfg)
    millumin: MilluminCfg = field(default_factory=MilluminCfg)
    web: WebCfg = field(default_factory=WebCfg)
    shows: ShowsCfg = field(default_factory=ShowsCfg)

    def shows_dir(self) -> Path:
        p = Path(self.shows.dir)
        return p if p.is_absolute() else REPO_ROOT / p


def _section(cls, data: dict, name: str):
    raw = data.get(name) or {}
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in raw.items() if k in known})


def load_config(path: Optional[str] = None) -> Config:
    path = path or os.environ.get("CADREUR_CONFIG")
    candidate = Path(path) if path else REPO_ROOT / "cadreur.toml"
    data: dict = {}
    if candidate.exists():
        data = tomllib.loads(candidate.read_text(encoding="utf-8"))
    return Config(
        telemetre=_section(TelemetreCfg, data, "telemetre"),
        millumin=_section(MilluminCfg, data, "millumin"),
        web=_section(WebCfg, data, "web"),
        shows=_section(ShowsCfg, data, "shows"),
    )
