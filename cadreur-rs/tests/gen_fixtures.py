"""Generates parity fixtures from the Python implementation (the reference).

    ../.venv/bin/python cadreur-rs/tests/gen_fixtures.py

Regenerate only when the Python reference intentionally changes; the Rust port
must reproduce these outputs exactly.
"""
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))

from cadreur.interp import insert_point, interpolate, normalize_points, round_for_send

rng = random.Random(20260905)  # fixed seed: fixtures are reproducible
cases = []

for _ in range(400):
    n = rng.choice([0, 1, 2, 3, 4, 5, 8])
    pts = []
    d = rng.uniform(1.0, 3.0)
    for _ in range(n):
        d += rng.uniform(0.0005, 1.2)  # sometimes inside the 1 mm dedup window
        pts.append({
            "distance_m": round(d, 4),
            "scale": round(rng.uniform(0.1, 1.0), 4),
            "pos_x": round(rng.uniform(0.0, 1.0), 4),
            "pos_y": round(rng.uniform(0.0, 1.0), 4),
        })
    rng.shuffle(pts)  # exercise the defensive sort
    norm = normalize_points(pts)
    query = rng.uniform(0.5, 12.0)
    values, clamped = interpolate(norm, query)
    new = {
        "distance_m": round(rng.uniform(0.5, 12.0), 4),
        "scale": round(rng.uniform(0.1, 1.0), 4),
        "pos_x": round(rng.uniform(0.0, 1.0), 4),
        "pos_y": round(rng.uniform(0.0, 1.0), 4),
    }
    inserted, replaced = insert_point(norm, new)
    cases.append({
        "raw": pts,
        "normalized": norm,
        "query": query,
        "values": round_for_send(values) if values else None,
        "clamped": clamped,
        "insert": new,
        "inserted": inserted,
        "replaced": replaced,
    })

out = os.path.join(HERE, "fixtures", "interp_cases.json")
with open(out, "w") as f:
    json.dump({"generated_by": "src/cadreur/interp.py", "cases": cases}, f, indent=1)
print(f"{len(cases)} cases -> {out}")
