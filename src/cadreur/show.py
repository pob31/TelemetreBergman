"""Show file: schema, load/save, rules of PRD §6.

One JSON file = one show — everything the operator edits, including smoothing.
Armed is NEVER persisted (it is runtime state, and save writes only the known
schema). Loads are defensive: unknown keys ignored, points sorted + deduped,
a bad reference falls back to the first valid one.
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

VERSION = 1
BEAMER_KEYS = ("front", "rear")
REAR_CAL_KEY = "default"  # rear has a single calibration set per look
LAYER_RE = re.compile(r"^[A-Za-z0-9._-]+$")  # spaces break OSC addresses

DEFAULT_SMOOTHING = {
    "ema_tau_s": 5.0,
    "deadband_scale": 0.0005,
    "deadband_px": 0.5,
    "slew_scale_per_s": 0.05,
    "slew_px_per_s": 50.0,
    "refresh_hz": 1.0,
}
SMOOTHING_LIMITS = {  # operator-tunable ranges (Advanced drawer)
    "ema_tau_s": (0.0, 30.0),
    "deadband_scale": (0.0, 0.1),
    "deadband_px": (0.0, 50.0),
    "slew_scale_per_s": (0.001, 10.0),
    "slew_px_per_s": (1.0, 10000.0),
    "refresh_hz": (0.1, 20.0),
}
DEFAULT_LENS_MEMORIES = ["M1", "M2", "M3"]


class ShowError(ValueError):
    """A show file we refuse to load/apply, with an operator-readable reason."""


def valid_layer_name(name: str) -> bool:
    return bool(LAYER_RE.match(name or ""))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "look"


def unique_id(base: str, taken: List[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


# --- schema ------------------------------------------------------------------

def default_trim() -> dict:
    return {"scale_mul": 1.0, "dx_px": 0.0, "dy_px": 0.0}


def default_cal_set() -> dict:
    return {"interp": "linear", "trim": default_trim(), "points": []}


def default_beamer(layer: str) -> dict:
    return {"layer": layer, "enabled": True, "calibrations": {}}


def new_show(name: str = "Nouveau spectacle") -> dict:
    return {
        "app": "cadreur",
        "version": VERSION,
        "meta": {"name": name, "saved_at": None, "notes": ""},
        "settings": {"active_look": "look-1", "active_lens_memory": "M1"},
        "lens_memories": list(DEFAULT_LENS_MEMORIES),
        "smoothing": dict(DEFAULT_SMOOTHING),
        "looks": [
            {
                "id": "look-1",
                "name": "Look 1",
                "beamers": {
                    "front": default_beamer("front"),
                    "rear": default_beamer("rear"),
                },
            }
        ],
    }


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


def _norm_beamer(raw) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    layer = raw.get("layer")
    if not isinstance(layer, str):
        layer = ""
    cals = raw.get("calibrations")
    cals = cals if isinstance(cals, dict) else {}
    return {
        "layer": layer,
        "enabled": bool(raw.get("enabled", True)),
        "calibrations": {str(k): _norm_cal_set(v) for k, v in cals.items()},
    }


def normalize(data) -> dict:
    """Validated deep copy holding only the known schema (PRD §6 rules).

    Raises ShowError on a missing/newer version or a structurally hopeless
    document. Anything recoverable is repaired silently (defensive I/O).
    """
    if not isinstance(data, dict):
        raise ShowError("Not a Cadreur show file (expected a JSON object).")
    v = data.get("version")
    if not isinstance(v, int) or isinstance(v, bool):
        raise ShowError("No schema version — not a Cadreur show file.")
    if v > VERSION:
        raise ShowError(
            f"Show file version {v} was made by a newer Cadreur (this build reads v{VERSION})."
        )
    # v < VERSION would run migrations here; v1 is the first schema.

    meta_raw = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    settings_raw = data.get("settings") if isinstance(data.get("settings"), dict) else {}

    memories = [str(m) for m in (data.get("lens_memories") or []) if str(m).strip()]
    if not memories:
        memories = list(DEFAULT_LENS_MEMORIES)

    smoothing = dict(DEFAULT_SMOOTHING)
    raw_smooth = data.get("smoothing") if isinstance(data.get("smoothing"), dict) else {}
    for k in smoothing:
        try:
            lo, hi = SMOOTHING_LIMITS[k]
            smoothing[k] = min(hi, max(lo, float(raw_smooth.get(k, smoothing[k]))))
        except (TypeError, ValueError):
            pass

    looks: List[dict] = []
    ids: List[str] = []
    for raw_look in data.get("looks") or []:
        if not isinstance(raw_look, dict):
            continue
        name = str(raw_look.get("name") or "Look")
        lid = str(raw_look.get("id") or slugify(name))
        lid = unique_id(lid, ids)
        ids.append(lid)
        beamers = {}
        raw_beamers = raw_look.get("beamers") if isinstance(raw_look.get("beamers"), dict) else {}
        for b in BEAMER_KEYS:  # keys are exactly front/rear; each optional
            nb = _norm_beamer(raw_beamers.get(b)) if b in raw_beamers else None
            if nb is not None:
                beamers[b] = nb
        looks.append({"id": lid, "name": name, "beamers": beamers})
    if not looks:
        looks = new_show()["looks"]
        ids = [lk["id"] for lk in looks]

    active_look = str(settings_raw.get("active_look") or "")
    if active_look not in ids:
        active_look = ids[0]
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
        "settings": {"active_look": active_look, "active_lens_memory": active_mem},
        "lens_memories": memories,
        "smoothing": smoothing,
        "looks": looks,
    }


# --- helpers on a normalized show -------------------------------------------

def get_look(data: dict, look_id: str) -> Optional[dict]:
    for lk in data["looks"]:
        if lk["id"] == look_id:
            return lk
    return None


def active_look(data: dict) -> Optional[dict]:
    return get_look(data, data["settings"]["active_look"])


def cal_key_for(data: dict, beamer: str) -> str:
    """Front resolves via the global active lens memory; rear always uses the
    reserved 'default' key — one uniform code path."""
    return data["settings"]["active_lens_memory"] if beamer == "front" else REAR_CAL_KEY


def cal_set_for(data: dict, look: Optional[dict], beamer: str) -> Optional[dict]:
    """The active calibration set, or None (=> that beamer is inhibited).
    Never falls back to another memory's set."""
    if not look:
        return None
    b = look["beamers"].get(beamer)
    if not b:
        return None
    return b["calibrations"].get(cal_key_for(data, beamer))


def ensure_cal_set(data: dict, look: dict, beamer: str) -> dict:
    """Get-or-create the active set (capture creates it lazily)."""
    b = look["beamers"].get(beamer)
    if b is None:
        raise ShowError(f"No {beamer} beamer in this look.")
    key = cal_key_for(data, beamer)
    if key not in b["calibrations"]:
        b["calibrations"][key] = default_cal_set()
    return b["calibrations"][key]


# --- look operations ---------------------------------------------------------

def create_look(data: dict, name: str) -> dict:
    ids = [lk["id"] for lk in data["looks"]]
    lid = unique_id(slugify(name), ids)
    look = {
        "id": lid,
        "name": name or "Look",
        "beamers": {"front": default_beamer("front"), "rear": default_beamer("rear")},
    }
    data["looks"].append(look)
    return look


def duplicate_look(data: dict, look_id: str, name: Optional[str] = None) -> dict:
    src = get_look(data, look_id)
    if not src:
        raise ShowError(f"Unknown look '{look_id}'.")
    ids = [lk["id"] for lk in data["looks"]]
    new_name = name or f"{src['name']} (copie)"
    copy = json.loads(json.dumps(src))
    copy["id"] = unique_id(slugify(new_name), ids)
    copy["name"] = new_name
    data["looks"].append(copy)
    return copy


def rename_look(data: dict, look_id: str, name: str) -> dict:
    look = get_look(data, look_id)
    if not look:
        raise ShowError(f"Unknown look '{look_id}'.")
    look["name"] = name or look["name"]
    return look


def delete_look(data: dict, look_id: str) -> None:
    look = get_look(data, look_id)
    if not look:
        raise ShowError(f"Unknown look '{look_id}'.")
    if len(data["looks"]) <= 1:
        raise ShowError("Cannot delete the last look.")
    data["looks"].remove(look)
    if data["settings"]["active_look"] == look_id:
        data["settings"]["active_look"] = data["looks"][0]["id"]


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
