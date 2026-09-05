#!/usr/bin/env python3
"""Recover a show after the rangefinder was physically moved.

Calibration points are keyed on ABSOLUTE distance, and the maths is
deliberately immune to Set Zero / Clear Zero / Invert on the Pi — so a re-zero
cannot compensate for the sensor itself moving. If the magic arm slid along the
beam axis, every stored distance is wrong by the same amount, and that is pure
arithmetic. If the arm was TILTED instead, the error grows with distance and no
single offset can fix it.

    # Which case is it? Park the scrim on two known marks and compare the
    # distance shown now against the calibration point for that mark.
    ./scripts/offset_show.py --diagnose 2.10 2.26 4.30 4.46

    # Slide confirmed: shift every point, into a NEW file.
    ./scripts/offset_show.py --apply -0.16 shows/bergman.json

Never writes in place, never overwrites, and validates the result loads.
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from cadreur.show import load_show, normalize  # noqa: E402

# The TF02-Pro resolves about a centimetre; anything under this is noise.
TOL_M = 0.02


def diagnose(old1, new1, old2, new2):
    """Slide (constant error) or tilt (error proportional to distance)?"""
    d1, d2 = new1 - old1, new2 - old2
    print(f"  repère 1 : {old1:.3f} m calibré -> {new1:.3f} m mesuré   (écart {d1:+.3f} m)")
    print(f"  repère 2 : {old2:.3f} m calibré -> {new2:.3f} m mesuré   (écart {d2:+.3f} m)")
    print()

    if abs(d1) < TOL_M and abs(d2) < TOL_M:
        print("VERDICT : aucun décalage significatif. Le télémètre n'a pas bougé.")
        print("          Chercher la cause ailleurs (calque, Interaction Millumin…).")
        return

    if abs(d1 - d2) < TOL_M:
        delta = (d1 + d2) / 2.0
        print(f"VERDICT : GLISSEMENT le long de l'axe. Écart constant de {delta:+.3f} m.")
        print("          Réparable sans recalibrer :")
        print(f"          ./scripts/offset_show.py --apply {-delta:.3f} shows/<ton-fichier>.json")
        print()
        print("          (Le mieux reste de remettre le bras exactement où il était.)")
        return

    # measured = true / cos(theta) for a tilt, so the ratio is constant, not the delta.
    r1, r2 = (new1 / old1 if old1 else 0), (new2 / old2 if old2 else 0)
    if r1 and r2 and abs(r1 - r2) < 0.02:
        angle = math.degrees(math.acos(min(1.0, 1.0 / r1))) if r1 >= 1 else None
        print(f"VERDICT : BASCULE (angle). L'erreur est proportionnelle, x{r1:.4f}.")
        if angle:
            print(f"          Correspond à environ {angle:.1f}° de rotation du boîtier.")
        print("          Un décalage constant ne peut PAS corriger ça.")
        print("          -> remettre le bras d'aplomb, ou recapturer les points.")
        return

    print("VERDICT : ni un glissement pur ni une bascule pure (les deux, sans doute).")
    print("          Recapturer est plus sûr que de bricoler les distances :")
    print("          2 points aux extrémités par canal suffisent presque.")


def apply_offset(delta, path):
    doc = load_show(path)
    moved = 0
    lo = None
    for beamer in doc["beamers"].values():
        for ch in beamer["channels"]:
            for cset in ch["calibrations"].values():
                for p in cset["points"]:
                    p["distance_m"] = round(p["distance_m"] + delta, 4)
                    lo = p["distance_m"] if lo is None else min(lo, p["distance_m"])
                    moved += 1

    if moved == 0:
        sys.exit("Aucun point de calibration dans ce fichier — rien à décaler.")
    if lo is not None and lo < 0:
        sys.exit(f"Refus : le décalage rendrait une distance négative ({lo:.3f} m). "
                 "Vérifier le signe.")

    normalize(doc)  # refuse to write something the app would reject
    base, ext = os.path.splitext(path)
    out = f"{base}-recale{ext}"
    if os.path.exists(out):
        sys.exit(f"Refus : {out} existe déjà. Le renommer ou le supprimer d'abord.")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"{moved} point(s) décalé(s) de {delta:+.3f} m")
    print(f"Écrit : {out}")
    print(f"L'original n'a pas été touché : {path}")
    print()
    print("Ouvrir ce nouveau fichier depuis l'interface et vérifier sur un repère")
    print("connu AVANT de l'utiliser en spectacle.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--diagnose", nargs=4, type=float,
                   metavar=("CAL1", "MESURE1", "CAL2", "MESURE2"),
                   help="deux repères : distance calibrée puis distance mesurée maintenant")
    g.add_argument("--apply", type=float, metavar="DELTA",
                   help="décalage en mètres à ajouter à chaque point")
    ap.add_argument("show", nargs="?", help="fichier spectacle (avec --apply)")
    a = ap.parse_args()

    if a.diagnose:
        diagnose(*a.diagnose)
    else:
        if not a.show:
            ap.error("--apply a besoin du fichier spectacle")
        apply_offset(a.apply, a.show)


if __name__ == "__main__":
    main()
