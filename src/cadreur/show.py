"""Show file: schema, load/save, migration.

Schema v2 (2026-07-21): no Looks. Each beamer (front/rear) carries a flat list
of **channels** — one per Millumin layer — driven continuously and simultaneously
(the operator selects a mode by layer visibility in Millumin, not by switching in
Cadreur). Each channel has its own OSC addresses (scale / positionH / positionV,
all normalised 0..1) and its own calibration: keyed by lens memory on the front
(the projector has lens memories), single "default" set on the rear.

One JSON file = one show — everything the operator edits, including smoothing.
Armed is NEVER persisted. Loads are defensive: unknown keys ignored, points
sorted + deduped, bad references repaired. A v1 (Looks) file is migrated: the
active look's front/rear become channel 1, the rest are filled with fresh
channels.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .interp import normalize_points

log = logging.getLogger("cadreur.show")

VERSION = 2
BEAMER_KEYS = ("front", "rear")
REAR_CAL_KEY = "default"  # rear has no lens memories: one calibration per channel
DEFAULT_CHANNELS = 4  # 4 front + 4 rear, per the show design
OSC_PREFIX = {"front": "front", "rear": "retro"}
CHANNEL_NAME = {"front": "Face", "rear": "Lointain"}
LAYER_RE = re.compile(r"^[A-Za-z0-9._-]+$")  # spaces break OSC addresses
OSC_ADDR_RE = re.compile(r"^/[A-Za-z0-9._:/-]+$")  # a plausible OSC address

DEFAULT_SMOOTHING = {
    "ema_tau_s": 5.0,
    "deadband_scale": 0.0005,
    "slew_scale_per_s": 0.05,
    "refresh_hz": 1.0,
}
SMOOTHING_LIMITS = {  # operator-tunable ranges (Advanced drawer)
    "ema_tau_s": (0.0, 30.0),
    "deadband_scale": (0.0, 0.1),
    "slew_scale_per_s": (0.001, 10.0),
    "refresh_hz": (0.1, 20.0),
}
DEFAULT_LENS_MEMORIES = ["M1", "M2", "M3"]
OSC_KEYS = ("osc_scale", "osc_posv", "osc_posh")


class ShowError(ValueError):
    """A show file we refuse to load/apply, with an operator-readable reason."""


def valid_layer_name(name: str) -> bool:
    return bool(LAYER_RE.match(name or ""))


def valid_osc_addr(addr: str) -> bool:
    return bool(OSC_ADDR_RE.match(addr or ""))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "chan"


def unique_id(base: str, taken: List[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


# --- schema pieces -----------------------------------------------------------

def default_trim() -> dict:
    return {"scale_mul": 1.0, "dx_px": 0.0, "dy_px": 0.0}


def default_cal_set() -> dict:
    return {"interp": "linear", "trim": default_trim(), "points": []}


def default_osc(beamer: str, index: int) -> dict:
    p = OSC_PREFIX.get(beamer, beamer)
    return {"osc_scale": f"/{p}/scale/{index}",
            "osc_posv": f"/{p}/positionV/{index}",
            "osc_posh": f"/{p}/positionH/{index}"}


def default_channel(beamer: str, index: int) -> dict:
    return {
        "id": f"{beamer}-{index}",
        "name": f"{CHANNEL_NAME.get(beamer, beamer.title())} {index}",
        "enabled": True,
        **default_osc(beamer, index),
        "calibrations": {},
    }


def new_show(name: str = "Nouveau spectacle") -> dict:
    return {
        "app": "cadreur",
        "version": VERSION,
        "meta": {"name": name, "saved_at": None, "notes": ""},
        "settings": {"active_lens_memory": "M1"},
        "lens_memories": list(DEFAULT_LENS_MEMORIES),
        "smoothing": dict(DEFAULT_SMOOTHING),
        "beamers": {
            b: {"channels": [default_channel(b, i) for i in range(1, DEFAULT_CHANNELS + 1)]}
            for b in BEAMER_KEYS
        },
    }


# --- normalization -----------------------------------------------------------

def _norm_trim(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = default_trim()
    for k in out:
        try:
            out[k] = float(raw.get(k, out[k]))
        except (TypeError, ValueError):
            pass
    return out


def _norm_cal_set(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "interp": raw.get("interp") if raw.get("interp") in ("linear",) else "linear",
        "trim": _norm_trim(raw.get("trim")),
        "points": normalize_points(raw.get("points") or []),
    }


def _norm_channel(raw, beamer: str, index: int, taken: List[str]) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    d = default_channel(beamer, index)
    cid = str(raw.get("id") or d["id"])
    cid = unique_id(cid, taken)
    name = str(raw.get("name") or d["name"])
    cals = raw.get("calibrations")
    cals = cals if isinstance(cals, dict) else {}
    out = {
        "id": cid,
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "calibrations": {str(k): _norm_cal_set(v) for k, v in cals.items()},
    }
    for k in OSC_KEYS:
        v = raw.get(k)
        out[k] = v if isinstance(v, str) and v else d[k]
    return out


def _norm_beamer(raw, beamer: str) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    raw_channels = raw.get("channels")
    raw_channels = raw_channels if isinstance(raw_channels, list) else []
    channels: List[dict] = []
    taken: List[str] = []
    for i, rc in enumerate(raw_channels, start=1):
        ch = _norm_channel(rc, beamer, i, taken)
        taken.append(ch["id"])
        channels.append(ch)
    if not channels:  # a beamer always has at least the default set of channels
        channels = [default_channel(beamer, i) for i in range(1, DEFAULT_CHANNELS + 1)]
    return {"channels": channels}


def _norm_smoothing(raw) -> dict:
    smoothing = dict(DEFAULT_SMOOTHING)
    raw = raw if isinstance(raw, dict) else {}
    for k in smoothing:
        try:
            lo, hi = SMOOTHING_LIMITS[k]
            smoothing[k] = min(hi, max(lo, float(raw.get(k, smoothing[k]))))
        except (TypeError, ValueError):
            pass
    return smoothing


def _migrate_v1(data: dict) -> dict:
    """v1 (Looks) -> v2 (channels). The active look's front/rear beamer becomes
    channel 1 of each (osc + calibrations preserved); channels 2..N are fresh.
    Other looks are dropped."""
    looks = data.get("looks") or []
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    active_id = settings.get("active_look")
    src = None
    for lk in looks:
        if isinstance(lk, dict) and lk.get("id") == active_id:
            src = lk
            break
    if src is None and looks and isinstance(looks[0], dict):
        src = looks[0]
    src_beamers = (src or {}).get("beamers") if isinstance((src or {}).get("beamers"), dict) else {}

    beamers = {}
    for b in BEAMER_KEYS:
        chans = [default_channel(b, i) for i in range(1, DEFAULT_CHANNELS + 1)]
        old = src_beamers.get(b)
        if isinstance(old, dict):  # carry the old single beamer into channel 1
            ch1 = chans[0]
            cals = old.get("calibrations")
            if isinstance(cals, dict):
                ch1["calibrations"] = cals
            for k in OSC_KEYS:  # keep whatever addresses were configured
                if isinstance(old.get(k), str) and old[k]:
                    ch1[k] = old[k]
        beamers[b] = {"channels": chans}
    log.info("Migrated a v1 show (Looks) to v2 channels: active look -> channel 1")
    return {
        "app": "cadreur",
        "version": VERSION,
        "meta": data.get("meta") if isinstance(data.get("meta"), dict) else {},
        "settings": {"active_lens_memory": settings.get("active_lens_memory", "M1")},
        "lens_memories": data.get("lens_memories") or list(DEFAULT_LENS_MEMORIES),
        "smoothing": data.get("smoothing") or {},
        "beamers": beamers,
    }


def normalize(data) -> dict:
    """Validated deep copy holding only the known schema. Raises ShowError on a
    missing/newer version. A v1 document is migrated. Recoverable issues are
    repaired silently (defensive I/O)."""
    if not isinstance(data, dict):
        raise ShowError("Not a Cadreur show file (expected a JSON object).")
    v = data.get("version")
    if not isinstance(v, int) or isinstance(v, bool):
        raise ShowError("No schema version — not a Cadreur show file.")
    if v > VERSION:
        raise ShowError(
            f"Show file version {v} was made by a newer Cadreur (this build reads v{VERSION})."
        )
    if v < 2:
        data = _migrate_v1(data)

    meta_raw = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    settings_raw = data.get("settings") if isinstance(data.get("settings"), dict) else {}

    memories = [str(m) for m in (data.get("lens_memories") or []) if str(m).strip()]
    if not memories:
        memories = list(DEFAULT_LENS_MEMORIES)

    raw_beamers = data.get("beamers") if isinstance(data.get("beamers"), dict) else {}
    beamers = {b: _norm_beamer(raw_beamers.get(b), b) for b in BEAMER_KEYS}

    active_mem = str(settings_raw.get("active_lens_memory") or "")
    if active_mem not in memories:
        active_mem = memories[0]

    return {
        "app": "cadreur",
        "version": VERSION,
        "meta": {
            "name": str(meta_raw.get("name") or "Sans titre"),
            "saved_at": meta_raw.get("saved_at"),
            "notes": str(meta_raw.get("notes") or ""),
        },
        "settings": {"active_lens_memory": active_mem},
        "lens_memories": memories,
        "smoothing": _norm_smoothing(data.get("smoothing")),
        "beamers": beamers,
    }


# --- helpers on a normalized show -------------------------------------------

def channels_of(data: dict, beamer: str) -> List[dict]:
    return data["beamers"][beamer]["channels"]


def get_channel(data: dict, beamer: str, cid: str) -> Optional[dict]:
    for ch in channels_of(data, beamer):
        if ch["id"] == cid:
            return ch
    return None


def cal_key_for(data: dict, beamer: str) -> str:
    """Front resolves via the global active lens memory; rear uses the reserved
    'default' key — one uniform code path."""
    return data["settings"]["active_lens_memory"] if beamer == "front" else REAR_CAL_KEY


def cal_set_for(data: dict, beamer: str, channel: dict) -> Optional[dict]:
    """The channel's active calibration set, or None (=> channel inhibited).
    Never falls back to another memory's set."""
    if not channel:
        return None
    return channel["calibrations"].get(cal_key_for(data, beamer))


def ensure_cal_set(data: dict, beamer: str, channel: dict) -> dict:
    """Get-or-create the channel's active set (capture creates it lazily)."""
    key = cal_key_for(data, beamer)
    if key not in channel["calibrations"]:
        channel["calibrations"][key] = default_cal_set()
    return channel["calibrations"][key]


# --- channel operations ------------------------------------------------------

def _next_osc_index(data: dict, beamer: str) -> int:
    """Lowest positive index not already used by a channel's scale address."""
    used = set()
    p = OSC_PREFIX.get(beamer, beamer)
    for ch in channels_of(data, beamer):
        m = re.match(rf"^/{re.escape(p)}/scale/(\d+)$", ch.get("osc_scale", ""))
        if m:
            used.add(int(m.group(1)))
    i = 1
    while i in used:
        i += 1
    return i


def add_channel(data: dict, beamer: str, name: Optional[str] = None) -> dict:
    if beamer not in BEAMER_KEYS:
        raise ShowError(f"Unknown beamer '{beamer}'.")
    idx = _next_osc_index(data, beamer)
    ch = default_channel(beamer, idx)
    ch["id"] = unique_id(ch["id"], [c["id"] for c in channels_of(data, beamer)])
    if name:
        ch["name"] = name
    channels_of(data, beamer).append(ch)
    return ch


def delete_channel(data: dict, beamer: str, cid: str) -> None:
    chans = channels_of(data, beamer)
    ch = get_channel(data, beamer, cid)
    if ch is None:
        raise ShowError(f"Unknown channel '{cid}'.")
    if len(chans) <= 1:
        raise ShowError("Cannot delete the last channel of a beamer.")
    chans.remove(ch)


def rename_channel(data: dict, beamer: str, cid: str, name: str) -> dict:
    ch = get_channel(data, beamer, cid)
    if ch is None:
        raise ShowError(f"Unknown channel '{cid}'.")
    ch["name"] = name or ch["name"]
    return ch


def set_channel_osc(data: dict, beamer: str, cid: str, addrs: dict) -> dict:
    ch = get_channel(data, beamer, cid)
    if ch is None:
        raise ShowError(f"Unknown channel '{cid}'.")
    for k in OSC_KEYS:
        if k in addrs:
            a = str(addrs[k])
            if not valid_osc_addr(a):
                raise ShowError(f"Invalid OSC address for {k}: '{a}'.")
            ch[k] = a
    return ch


# --- files -------------------------------------------------------------------

def load_show(path: Path | str) -> dict:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ShowError(f"Show file not found: {path.name}")
    except (OSError, json.JSONDecodeError) as e:
        raise ShowError(f"Unreadable show file {path.name}: {e}")
    return normalize(raw)


def save_show(path: Path | str, data: dict) -> dict:
    """Atomic write (tmp + os.replace) of the known schema only; stamps
    meta.saved_at. Returns the document actually written."""
    path = Path(path)
    doc = normalize(data)
    doc["meta"]["saved_at"] = _utc_now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    data["meta"]["saved_at"] = doc["meta"]["saved_at"]
    return doc


def startup_backup(path: Path | str, keep: int = 10) -> Optional[Path]:
    """Before loading on app start, copy the current file to
    shows/backups/<name>-<stamp>.json and prune to the `keep` newest."""
    path = Path(path)
    if not path.exists():
        return None
    backups = path.parent / "backups"
    try:
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backups / f"{path.stem}-{stamp}.json"
        dest.write_bytes(path.read_bytes())
        old = sorted(backups.glob(f"{path.stem}-*.json"), key=lambda p: p.name)
        for p in old[:-keep]:
            p.unlink(missing_ok=True)
        return dest
    except OSError as e:  # a failed backup must never block startup
        log.warning("Startup backup failed for %s: %s", path, e)
        return None
