"""Fetch the current calibration points from Cadreur and assess linearity.

For each beamer and each axis (scale, vertical=pos_y, horizontal=pos_x), fit a
straight line over (distance, value), and measure how far the interior points
deviate from the straight chord between the two end points — that deviation is
exactly the error you'd get from a 2-point (endpoints-only) linear mapping.
"""
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8080/stream", timeout=4) as r:
    snap = None
    for raw in r:
        line = raw.decode().strip()
        if line.startswith("data:"):
            snap = json.loads(line[5:].strip())
            break

look = snap["look"]
setts = snap["settings"]
print(f"look={look['id']}  lens_memory={setts['active_lens_memory']}")

AXES = [("scale", "scale"), ("pos_y", "vertical"), ("pos_x", "horizontal")]


def lin_fit(xs, ys):
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx if sxx else 0.0
    a = my - b * mx
    return a, b


for b, beamer in look["beamers"].items():
    key = setts["active_lens_memory"] if b == "front" else "default"
    cset = (beamer.get("calibrations") or {}).get(key)
    pts = cset["points"] if cset else []
    print(f"\n=== {b.upper()}  ({len(pts)} points) ===")
    for p in pts:
        print(f"  d={p['distance_m']:.3f}  scale={p['scale']:.4f}  "
              f"vert={p['pos_y']:.4f}  horiz={p['pos_x']:.4f}")
    if len(pts) < 3:
        print("  -> need >= 3 points to judge linearity (2 points are always a line).")
        continue
    ds = [p["distance_m"] for p in pts]
    span = ds[-1] - ds[0]
    for key_axis, label in AXES:
        ys = [p[key_axis] for p in pts]
        rng = max(ys) - min(ys)
        # chord between the endpoints, evaluated at each interior distance
        d0, dN, y0, yN = ds[0], ds[-1], ys[0], ys[-1]
        max_dev = 0.0
        worst = None
        for d, y in zip(ds, ys):
            if dN != d0:
                chord = y0 + (yN - y0) * (d - d0) / (dN - d0)
                dev = y - chord
                if abs(dev) > abs(max_dev):
                    max_dev, worst = dev, d
        # least-squares residual as a second view
        a, slope = lin_fit(ds, ys)
        max_res = max(abs(y - (a + slope * d)) for d, y in zip(ds, ys))
        pct = (abs(max_dev) / rng * 100) if rng else 0.0
        print(f"  {label:11s}: range {rng:.4f} over {span:.3f} m | "
              f"max chord dev {max_dev:+.4f} at d={worst} "
              f"({pct:.1f}% of range) | LS resid {max_res:.4f}")
