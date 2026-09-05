#!/usr/bin/env python3
"""Generates Cadreur's icon set — no image library, just zlib and struct.

    python3 scripts/make_icon.py <out.iconset>

The motif is the job itself: a projection frame with the scrim crossing it.
"""
import os
import struct
import sys
import zlib

BG = (11, 15, 20)        # the UI's dark ground
FRAME = (232, 236, 242)  # projected image
SCRIM = (120, 170, 230)  # the moving tulle


def png(width, height, pixels):
    """pixels: list of rows, each a list of (r,g,b)."""
    raw = b"".join(b"\x00" + b"".join(bytes(p) for p in row) for row in pixels)
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def render(n):
    # Proportions held in fractions of n so every size looks the same.
    m = n * 0.17           # margin
    fw, fh = n - 2 * m, (n - 2 * m) * 0.62
    fy = (n - fh) / 2
    r = n * 0.06           # frame corner radius
    scrim_y, scrim_h = n * 0.60, max(1.0, n * 0.045)

    rows = []
    for y in range(n):
        row = []
        for x in range(n):
            c = BG
            inside_x = m <= x < m + fw
            inside_y = fy <= y < fy + fh
            if inside_x and inside_y:
                # round the frame's corners
                cx = min(max(x, m + r), m + fw - r)
                cy = min(max(y, fy + r), fy + fh - r)
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    c = FRAME
            # the scrim crosses the whole icon, in front of the frame
            if scrim_y <= y < scrim_y + scrim_h and n * 0.06 <= x < n - n * 0.06:
                c = SCRIM
            row.append(c)
        rows.append(row)
    return png(n, n, rows)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "Cadreur.iconset"
    os.makedirs(out, exist_ok=True)
    # The sizes iconutil expects.
    for size in (16, 32, 128, 256, 512):
        open(os.path.join(out, f"icon_{size}x{size}.png"), "wb").write(render(size))
        open(os.path.join(out, f"icon_{size}x{size}@2x.png"), "wb").write(render(size * 2))
    print(f"icon set -> {out}")


if __name__ == "__main__":
    main()
