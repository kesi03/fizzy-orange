#!/usr/bin/env python3
"""Draw the FIZZY ORANGE icon as a 960x540 PNG using only the Python stdlib.

Run from the repository root:  python tools/make_icon.py
Writes firmware/fizzy-orange/icon.png
"""

import math
import struct
import zlib
from pathlib import Path

W, H = 960, 540
SS = 2  # supersampling for smooth edges
SW, SH = W * SS, H * SS

BG = (30, 10, 8)
BAND = (62, 19, 12)
DARK = (28, 9, 6)
RIND = (178, 56, 10)
PEEL = (234, 104, 16)
FLESH = (255, 146, 26)
PULP = (255, 188, 58)
PITH = (255, 232, 172)
LEAF = (78, 158, 70)
LEAF_DARK = (41, 99, 46)
CAN = (255, 108, 16)
CAN_SHADE = (206, 70, 12)
FOAM = (255, 215, 142)
HIGHLIGHT = (255, 243, 208)


def blank():
    px = bytearray(SW * SH * 3)
    for y in range(SH):
        t = y / SH
        base = (BG[0] + int(12 * t), BG[1] + int(4 * t), BG[2] + int(3 * t))
        o = y * SW * 3
        px[o:o + SW * 3] = bytes(base) * SW
    return px


def put(px, x, y, color):
    if 0 <= x < SW and 0 <= y < SH:
        o = (y * SW + x) * 3
        px[o:o + 3] = bytes(color)


def rect(px, x0, y0, x1, y1, color):
    x0, x1 = max(0, int(x0 * SS)), min(SW, int(x1 * SS))
    y0, y1 = max(0, int(y0 * SS)), min(SH, int(y1 * SS))
    if x1 <= x0 or y1 <= y0:
        return
    row = bytes(color) * (x1 - x0)
    for y in range(y0, y1):
        o = (y * SW + x0) * 3
        px[o:o + len(row)] = row


def circle(px, cx, cy, r, color):
    ellipse(px, cx, cy, r, r, color)


def ellipse(px, cx, cy, rx, ry, color, angle=0.0):
    cx, cy, rx, ry = cx * SS, cy * SS, rx * SS, ry * SS
    ca, sa = math.cos(angle), math.sin(angle)
    rx2, ry2 = rx * rx, ry * ry
    reach = max(rx, ry) + 2
    for y in range(max(0, int(cy - reach)), min(SH, int(cy + reach + 1))):
        dy = y - cy
        for x in range(max(0, int(cx - reach)), min(SW, int(cx + reach + 1))):
            dx = x - cx
            u = dx * ca + dy * sa
            v = -dx * sa + dy * ca
            if (u / rx2) ** 2 + (v / ry2) ** 2 <= 1.0:
                put(px, x, y, color)


def ring(px, cx, cy, r, thick, color):
    cx, cy, r, thick = cx * SS, cy * SS, r * SS, thick * SS
    lo, hi = (r - thick) ** 2, (r + thick) ** 2
    for y in range(max(0, int(cy - r - thick)), min(SH, int(cy + r + thick + 1))):
        dy = y - cy
        for x in range(max(0, int(cx - r - thick)), min(SW, int(cx + r + thick + 1))):
            dx = x - cx
            d = dx * dx + dy * dy
            if lo <= d <= hi:
                put(px, x, y, color)


def line(px, x0, y0, x1, y1, width, color):
    x0, y0, x1, y1 = x0 * SS, y0 * SS, x1 * SS, y1 * SS
    w = width * SS
    dx, dy = x1 - x0, y1 - y0
    ln2 = dx * dx + dy * dy
    reach = w + 2
    for y in range(max(0, int(min(y0, y1) - reach)), min(SH, int(max(y0, y1) + reach + 1))):
        for x in range(max(0, int(min(x0, x1) - reach)), min(SW, int(max(x0, x1) + reach + 1))):
            t = 0.0 if ln2 == 0 else max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / ln2))
            qx, qy = x0 + t * dx, y0 + t * dy
            if (x - qx) ** 2 + (y - qy) ** 2 <= w * w:
                put(px, x, y, color)


def downsample(px):
    out = bytearray(W * H * 3)
    n = SS * SS
    for y in range(H):
        for x in range(W):
            r = g = b = 0
            for yy in range(SS):
                row = (y * SS + yy) * SW
                for xx in range(SS):
                    o = (row + x * SS + xx) * 3
                    r += px[o]
                    g += px[o + 1]
                    b += px[o + 2]
            o = (y * W + x) * 3
            out[o] = r // n
            out[o + 1] = g // n
            out[o + 2] = b // n
    return out


def png(rows):
    raw = b''.join(b'\x00' + bytes(row) for row in rows)

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))

    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def main():
    px = blank()

    # bold soda-bar bands and a faint fizz halo
    for y0, y1, col in ((0, 20, BAND), (H - 26, H, BAND),
                        (74, 80, CAN_SHADE), (H - 96, H - 90, CAN_SHADE)):
        rect(px, 0, y0, W, y1, col)
    ring(px, 330, 270, 214, 5, (92, 31, 14))
    ring(px, 330, 270, 198, 3, (128, 43, 14))

    # the orange slice: rind, pith, juicy segments, seeds and a leaf
    cx, cy, r = 326, 270, 178
    circle(px, cx, cy, r, RIND)
    circle(px, cx, cy, r - 12, PEEL)
    circle(px, cx, cy, r - 25, PITH)
    circle(px, cx, cy, r - 36, FLESH)
    circle(px, cx, cy, r - 58, PULP)
    for i in range(8):
        a = -math.pi / 2 + i * math.pi / 4
        line(px, cx, cy, cx + (r - 37) * math.cos(a), cy + (r - 37) * math.sin(a), 7, PITH)
    circle(px, cx, cy, 18, PITH)
    for i in range(8):
        a = -math.pi / 2 + (i + 0.5) * math.pi / 4
        sx, sy = cx + (r - 74) * math.cos(a), cy + (r - 74) * math.sin(a)
        ellipse(px, sx, sy, 10, 5, PITH, angle=a)
    ellipse(px, cx + 122, cy - 158, 62, 27, LEAF, angle=-0.55)
    ellipse(px, cx + 118, cy - 152, 42, 15, LEAF_DARK, angle=-0.55)
    line(px, cx + 88, cy - 138, cx + 142, cy - 180, 5, DARK)

    # bubbles behind the can
    for x, y, rad in ((586, 398, 24), (600, 338, 13), (566, 278, 9),
                      (598, 212, 18), (574, 148, 11), (620, 100, 8)):
        ring(px, x, y, rad, 3, FOAM)
        circle(px, x, y, max(3, rad // 3), FOAM)

    # a can of orange soda: flat body, bright label band, little orange badge
    x0, x1, mid = 630, 844, 737
    rect(px, x0, 128, x1, 452, CAN)
    rect(px, x1 - 36, 148, x1 - 14, 440, CAN_SHADE)
    rect(px, x0 + 14, 238, x1 - 14, 302, PITH)
    circle(px, mid, 270, 28, FLESH)
    for i in range(6):
        a = i * math.pi / 3
        line(px, mid, 270, mid + 22 * math.cos(a), 270 + 22 * math.sin(a), 3, PITH)
    circle(px, mid, 270, 5, PITH)
    ellipse(px, mid, 452, (x1 - x0) // 2, 28, CAN_SHADE)
    ellipse(px, mid, 128, (x1 - x0) // 2, 30, HIGHLIGHT)
    ellipse(px, mid, 128, (x1 - x0) // 2 - 14, 21, CAN)
    ellipse(px, mid, 124, 27, 8, DARK)

    # foreground fizz: big bubbles, small bubbles, foam at the base
    for x, y, rad in ((875, 394, 22), (894, 310, 13), (866, 244, 18),
                      (890, 172, 9), (806, 92, 15), (744, 78, 10),
                      (688, 96, 7), (900, 116, 12)):
        ring(px, x, y, rad, 3, FOAM)
        circle(px, x, y, max(3, rad // 3), FOAM)
    for x, y, rad in ((644, 474, 30), (704, 486, 21), (766, 474, 34), (828, 484, 23)):
        circle(px, x, y, rad, FOAM)

    small = downsample(px)
    rows = [small[y * W * 3:(y + 1) * W * 3] for y in range(H)]
    out = Path(__file__).resolve().parent.parent / 'firmware' / 'fizzy-orange' / 'icon.png'
    out.write_bytes(png(rows))
    print(f'wrote {out} ({out.stat().st_size} bytes)')


if __name__ == '__main__':
    main()
