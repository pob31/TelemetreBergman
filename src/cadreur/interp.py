"""Calibration-point math: piecewise-linear interpolation, trim, edits (PRD §7).

Points are plain JSON-native dicts {"distance_m","scale","pos_x","pos_y"},
kept sorted by distance_m. All functions are pure (inputs are never mutated).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

DEDUP_M = 0.001  # loader: two points closer than this -> the later one wins
REPLACE_M = 0.03  # capture: an existing point this close is replaced

PARAMS = ("scale", "pos_x", "pos_y")
POINT_KEYS = ("distance_m",) + PARAMS


def clean_point(p: dict) -> Optional[dict]:
    """A well-formed copy with float values, or None if malformed."""
    try:
        return {k: float(p[k]) for k in POINT_KEYS}
    except (TypeError, KeyError, ValueError):
        return None


def normalize_points(points: List[dict]) -> List[dict]:
    """Defensive load: drop malformed, dedup within 1 mm (later in the list
    wins — a re-capture overwrites), sort by distance."""
    kept: List[dict] = []
    for raw in points or []:
        p = clean_point(raw)
        if p is None:
            continue
        for i, q in enumerate(kept):
            if abs(q["distance_m"] - p["distance_m"]) < DEDUP_M:
                kept[i] = p
                break
        else:
            kept.append(p)
    return sorted(kept, key=lambda p: p["distance_m"])


def insert_point(points: List[dict], new: dict) -> Tuple[List[dict], bool]:
    """Sorted insert; an existing point within REPLACE_M (0.03 m) is replaced
    (natural re-capture at a mark). Returns (new list, replaced?)."""
    p = clean_point(new)
    if p is None:
        raise ValueError("malformed calibration point")
    out = [q for q in points]
    nearest = None
    for i, q in enumerate(out):
        d = abs(q["distance_m"] - p["distance_m"])
        if d <= REPLACE_M and (nearest is None or d < nearest[1]):
            nearest = (i, d)
    replaced = nearest is not None
    if replaced:
        out[nearest[0]] = p
    else:
        out.append(p)
    return sorted(out, key=lambda q: q["distance_m"]), replaced


def interpolate(points: List[dict], d: float) -> Tuple[Optional[dict], Optional[str]]:
    """abs_m -> {"scale","pos_x","pos_y"} over sorted points.

    Returns (values, clamped) where clamped is "low"/"high" outside the
    calibrated range, else None. N=0 -> (None, None): beamer inhibited.
    N=1 -> constant hold. N>=2 -> piecewise-linear, clamped at the ends.
    """
    n = len(points)
    if n == 0:
        return None, None
    if n == 1:
        p = points[0]
        return {k: p[k] for k in PARAMS}, None
    if d <= points[0]["distance_m"]:
        p = points[0]
        return {k: p[k] for k in PARAMS}, ("low" if d < p["distance_m"] else None)
    if d >= points[-1]["distance_m"]:
        p = points[-1]
        return {k: p[k] for k in PARAMS}, ("high" if d > p["distance_m"] else None)
    for a, b in zip(points, points[1:]):
        if d <= b["distance_m"]:
            t = (d - a["distance_m"]) / (b["distance_m"] - a["distance_m"])
            return {k: a[k] + t * (b[k] - a[k]) for k in PARAMS}, None
    # Unreachable with sorted points; be defensive anyway.
    p = points[-1]
    return {k: p[k] for k in PARAMS}, "high"


def apply_trim(values: dict, trim: dict) -> dict:
    """Post-interpolation correction: scale multiplies, pixels add."""
    return {
        "scale": values["scale"] * float(trim.get("scale_mul", 1.0)),
        "pos_x": values["pos_x"] + float(trim.get("dx_px", 0.0)),
        "pos_y": values["pos_y"] + float(trim.get("dy_px", 0.0)),
    }


def bake_trim(points: List[dict], trim: dict) -> List[dict]:
    """Fold trim into every point (scale multiplies, px adds)."""
    mul = float(trim.get("scale_mul", 1.0))
    dx = float(trim.get("dx_px", 0.0))
    dy = float(trim.get("dy_px", 0.0))
    return [
        {
            "distance_m": p["distance_m"],
            "scale": p["scale"] * mul,
            "pos_x": p["pos_x"] + dx,
            "pos_y": p["pos_y"] + dy,
        }
        for p in points
    ]


def round_for_send(values: dict) -> dict:
    """Scale -> 4 dp, positions -> 2 dp — both below the dead-bands, so
    rounding never fights the send policy."""
    return {
        "scale": round(values["scale"], 4),
        "pos_x": round(values["pos_x"], 2),
        "pos_y": round(values["pos_y"], 2),
    }
