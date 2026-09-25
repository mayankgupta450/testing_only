"""Draw laser-cut jali designs, export CNC-ready SVGs, and composite them onto the gate photo.

Usage: python3 make_jali.py SOURCE_PHOTO OUTPUT_DIR
"""
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import substring, unary_union

# Panel openings in the 1086x1448 source photo: (x0, y0, x1, y1)
PANELS = {
    "transom": (250, 236, 829, 348),
    "square_left": (137, 234, 223, 345),
    "square_right": (854, 242, 940, 353),
    "side_left": (46, 436, 182, 978),
    "side_right": (901, 448, 1038, 980),
}
EMBLEM = (1005, 522, 27)  # keep the round gold emblem untouched
DOWNLIGHTS_X = (267, 530, 812)
SS = 4  # supersampling for smooth edges


# ---------- shape helpers ----------

def petal(length, width, r0=0.0, angle=0.0, cx=0.0, cy=0.0, sharp=0.8):
    """Pointed petal lying along +x from r0 to r0+length, rotated by angle (deg) about (cx, cy)."""
    top, bot = [], []
    for i in range(33):
        t = i / 32
        hw = width / 2 * math.sin(math.pi * t) ** sharp
        top.append((r0 + t * length, hw))
        bot.append((r0 + t * length, -hw))
    poly = Polygon(top + bot[::-1])
    poly = affinity.rotate(poly, angle, origin=(0, 0))
    return affinity.translate(poly, cx, cy)


def slot(points, width, cap=1):
    return LineString(points).buffer(width / 2, cap_style=cap, join_style=1)


def rosette(cx, cy, r, n=8, rot=0.0, inner=0.28):
    parts = [petal(r * (1 - inner), r * 0.42, r * inner, rot + k * 360 / n, cx, cy) for k in range(n)]
    parts.append(Point(cx, cy).buffer(r * inner * 0.55))
    return unary_union(parts)


def lotus_bud(cx, cy, s, angle=-90):
    """Three-petal lotus pointing along `angle` with a small base arc."""
    parts = [
        petal(s, s * 0.42, 0, angle, cx, cy),
        petal(s * 0.8, s * 0.34, s * 0.12, angle - 38, cx, cy),
        petal(s * 0.8, s * 0.34, s * 0.12, angle + 38, cx, cy),
    ]
    a = math.radians(angle + 180)
    base = [(cx + s * 0.28 * math.cos(a + d) , cy + s * 0.28 * math.sin(a + d)) for d in np.linspace(-1.1, 1.1, 12)]
    parts.append(slot(base, s * 0.07))
    return unary_union(parts)


def star8(cx, cy, r):
    a = r * math.sqrt(2)
    sq = box(cx - a / 2, cy - a / 2, cx + a / 2, cy + a / 2)
    return unary_union([sq, affinity.rotate(sq, 45, origin=(cx, cy))])


def feather(x0, y0, length, angle, width):
    """Peacock feather: shaft, barbs, and an eye (ring with bridges + centre)."""
    parts = []
    L, W = length, width
    parts.append(slot([(0, 0), (L * 0.62, 0)], W * 0.07))
    for i in range(1, 9):
        x = L * 0.07 * i
        spread = W * 0.18 + W * 0.3 * (i / 9)
        parts.append(slot([(x, W * 0.08), (x + W * 0.28, spread)], W * 0.055))
        parts.append(slot([(x, -W * 0.08), (x + W * 0.28, -spread)], W * 0.055))
    ex, ew = L * 0.8, W * 0.5
    outer = affinity.scale(Point(ex, 0).buffer(1), ew * 0.95, ew * 0.72)
    inner = affinity.scale(Point(ex, 0).buffer(1), ew * 0.72, ew * 0.52)
    ring = outer.difference(inner)
    ring = ring.difference(box(ex - W, -W * 0.045, ex + W, W * 0.045))  # bridges keep centre attached
    parts.append(ring)
    parts.append(petal(ew * 0.9, ew * 0.55, ex - ew * 0.45, 0))
    g = unary_union(parts)
    g = affinity.rotate(g, angle, origin=(0, 0))
    return affinity.translate(g, x0, y0)


def leaf(x, y, size, angle):
    return petal(size, size * 0.45, 0, angle, x, y, sharp=0.7)


def branch(x, y, angle, length, width, depth, parts, leaves, rng):
    if depth == 0 or length < 6:
        leaves.append(leaf(x, y, max(7, length * 1.1), angle))
        return
    pts = []
    for i in range(9):
        t = i / 8
        bend = math.sin(t * math.pi) * 0.12 * (1 if depth % 2 else -1)
        a = math.radians(angle) + bend
        pts.append((x + math.cos(a) * length * t, y + math.sin(a) * length * t))
    parts.append(slot(pts, width))
    ex, ey = pts[-1]
    for da in (-30, 28):
        branch(ex, ey, angle + da + rng.uniform(-6, 6), length * 0.7, width * 0.7, depth - 1, parts, leaves, rng)


def tree(cx, base_y, height, width, spread=1.0):
    rng = np.random.default_rng(8)
    parts, leaves = [], []
    trunk_h = height * 0.32
    parts.append(slot([(cx, base_y), (cx, base_y - trunk_h)], width))
    for ang in (-90 - 48 * spread, -90 - 16 * spread, -90 + 16 * spread, -90 + 48 * spread):
        branch(cx, base_y - trunk_h, ang, height * 0.3, width * 0.7, 3, parts, leaves, rng)
    for dx in (-1, 1):  # roots
        parts.append(slot([(cx, base_y - 2), (cx + dx * height * 0.18, base_y + 1)], width * 0.55))
    trunk = unary_union(parts)
    leaves = [lf.difference(trunk.buffer(1.4)) for lf in leaves]
    return unary_union([trunk] + leaves)


def clip(geom, w, h, margin, keep_out=None):
    region = box(margin, margin, w - margin, h - margin)
    g = geom.intersection(region)
    if keep_out is not None:
        pieces = list(g.geoms) if hasattr(g, "geoms") else [g]
        total = g.area
        kept = [p for p in pieces if not p.intersects(keep_out) or p.area > 0.2 * total]
        g = unary_union(kept).difference(keep_out)
    return g.buffer(0)


# ---------- designs: each returns cut-out geometry for a panel of size (w, h) ----------

def geometric(kind, w, h):
    rows = {"transom": 3, "square": 3, "side": 4}[kind]
    p = (h - 8) / rows if kind != "side" else (w - 8) / rows
    parts = []
    ox, oy = (w % p) / 2, (h % p) / 2
    nx, ny = int(w / p) + 2, int(h / p) + 2
    for i in range(-1, nx):
        for j in range(-1, ny):
            cx, cy = ox + (i + 0.5) * p, oy + (j + 0.5) * p
            parts.append(star8(cx, cy, p * 0.36))
            d = p * 0.2
            qx, qy = cx + p / 2, cy + p / 2
            parts.append(Polygon([(qx - d, qy), (qx, qy - d), (qx + d, qy), (qx, qy + d)]))
    g = unary_union([pp for pp in parts if not pp.is_empty])
    # metal star inside each cut star (bridged by 4 spokes) for a richer jaali look
    inner = []
    for i in range(-1, nx):
        for j in range(-1, ny):
            cx, cy = ox + (i + 0.5) * p, oy + (j + 0.5) * p
            core = star8(cx, cy, p * 0.2)
            spokes = unary_union([
                box(cx - p * 0.03, cy - p * 0.4, cx + p * 0.03, cy + p * 0.4),
                box(cx - p * 0.4, cy - p * 0.03, cx + p * 0.4, cy + p * 0.03),
            ])
            inner.append(core.union(spokes))
    g = g.difference(unary_union(inner))
    return g


def lotus(kind, w, h):
    parts = []
    if kind == "transom":
        cx, cy, R = w / 2, h / 2, h / 2 - 6
        parts.append(rosette(cx, cy, R * 0.5, 8, 22.5))
        for k in range(16):
            parts.append(petal(R * 0.36, R * 0.2, R * 0.58, k * 22.5, cx, cy))
        for k in range(32):
            a = math.radians(k * 11.25 + 5.6)
            parts.append(Point(cx + math.cos(a) * R * 0.99, cy + math.sin(a) * R * 0.99).buffer(R * 0.035))
        n = 4
        for side in (-1, 1):
            for i in range(n):
                x = cx + side * (R + 38 + i * ((w / 2 - R - 50) / (n - 0.4)))
                parts.append(lotus_bud(x, cy + 14, 34, -90))
                parts.append(Point(x, cy + 30).buffer(2.4))
            # petal-lattice bands along top and bottom
            x = cx + side * (R + 14)
            while 10 < x < w - 10:
                parts.append(petal(10, 5, 0, 0, x - 5, 9))
                parts.append(petal(10, 5, 0, 0, x - 5, h - 9))
                x += side * 16
    elif kind == "square":
        parts.append(rosette(w / 2, h / 2, min(w, h) * 0.34, 8))
        for k in range(8):
            a = math.radians(k * 45 + 22.5)
            parts.append(Point(w / 2 + math.cos(a) * w * 0.4, h / 2 + math.sin(a) * w * 0.4).buffer(2.5))
        for yy in (10, h - 10):
            parts.append(lotus_bud(w / 2, yy + (6 if yy < h / 2 else -6), 12, -90 if yy > h / 2 else 90))
    else:
        cx = w / 2
        n = 4
        top = 118  # leave the top clear for the emblem on the right panel
        parts.append(lotus_bud(cx, 70, 30, -90))
        step = (h - 20 - top) / n
        for i in range(n):
            y = top + step * (i + 0.5)
            parts.append(rosette(cx, y, w * 0.26, 8, 22.5 * (i % 2)))
            if i < n - 1:
                parts.append(lotus_bud(cx, y + step / 2 + 14, 30, -90))
        for yy in range(14, int(h) - 8, 18):
            parts.append(petal(10, 4.5, 0, 90, 10, yy))
            parts.append(petal(10, 4.5, 0, 90, w - 10, yy))
    return unary_union(parts)


def minimal(kind, w, h):
    parts = []
    if kind == "transom":
        lines = 6
        for i in range(lines):
            y0 = 14 + i * (h - 28) / (lines - 1)
            pts = [(x, y0 + 7 * math.sin(2 * math.pi * x / w * 1.5 + i * 0.5)) for x in np.linspace(14, w - 14, 120)]
            ls = LineString(pts)
            # break each wave into long slots with staggered gaps so the metal stays rigid
            L = ls.length
            s = (i * 37) % 60
            segs, pos = [], s
            while pos < L - 20:
                seg_len = min(150 if i % 2 else 110, L - pos - 6)
                segs.append(substring(ls, pos, pos + seg_len).buffer(2.4, cap_style=1))
                pos += seg_len + 14
            parts.extend(segs)
    elif kind == "square":
        for i in range(5):
            x = w * (0.2 + 0.15 * i)
            L = h * (0.78 - 0.12 * abs(i - 2))
            parts.append(slot([(x, (h - L) / 2), (x, (h + L) / 2)], 4.8))
    else:
        cols = 5
        for i in range(cols):
            x = w * (0.18 + 0.16 * i)
            y = 16
            pattern = [(0.26, 0.1), (0.14, 0.5), (0.36, 0.3), (0.2, 0.7), (0.3, 0.9)]
            phase = i % 2
            lens = [0.3, 0.16, 0.42, 0.12] if phase else [0.14, 0.4, 0.2, 0.26]
            for ln in lens:
                L = (h - 32) * ln - 12
                pts = [(x + 3 * math.sin((yy / h) * math.pi * 3 + i), yy) for yy in np.linspace(y, y + L, 30)]
                parts.append(slot(pts, 4.8))
                y += L + 14
    return unary_union(parts)


def tree_of_life(kind, w, h):
    parts = []
    if kind == "transom":
        cx = w / 2
        parts.append(tree(cx, h - 10, h - 16, 5.5, spread=1.4))
        for side in (-1, 1):
            for y in (h * 0.3, h * 0.7):
                parts.append(feather(cx + side * 92, y, 150, 90 - side * 90, 30))
            vine = [(cx + side * (254 + 8 * math.sin(y / h * math.pi * 2)), y) for y in np.linspace(10, h - 10, 40)]
            stem = slot(vine, 2.6)
            parts.append(stem)
            for k, y in enumerate(np.linspace(20, h - 20, 6)):
                vx = cx + side * (254 + 8 * math.sin(y / h * math.pi * 2))
                sd = 1 if k % 2 else -1
                parts.append(leaf(vx + sd * 2.5, y, 14, (0 if sd > 0 else 180) - 35 * sd).difference(stem.buffer(1.3)))
    elif kind == "square":
        parts.append(feather(w / 2, h - 8, h - 18, -90, w * 0.72))
    else:
        cx = w / 2
        pts = [(cx + w * 0.22 * math.sin(y / h * math.pi * 4), y) for y in np.linspace(h - 12, 14, 200)]
        stem = slot(pts, 4.2)
        parts.append(stem)
        for k, y in enumerate(np.linspace(h - 34, 34, 20)):
            x = cx + w * 0.22 * math.sin(y / h * math.pi * 4)
            side = 1 if k % 2 else -1
            parts.append(leaf(x + side * 3, y, 26, (0 if side > 0 else 180) - 35 * side).difference(stem.buffer(1.6)))
            if k % 4 == 1:
                parts.append(Point(x - side * 16, y - 6).buffer(3.2))
    return unary_union(parts)


# ---------- lighter, simpler designs ----------

def broken_frame(w, h, inset, width, gap):
    """Thin frame line broken at the corners so the centre stays attached."""
    a, b = inset, inset
    return [
        slot([(a + gap, b), (w - a - gap, b)], width),
        slot([(a + gap, h - b), (w - a - gap, h - b)], width),
        slot([(a, b + gap), (a, h - b - gap)], width),
        slot([(w - a, b + gap), (w - a, h - b - gap)], width),
    ]


def diamond(cx, cy, rx, ry):
    return Polygon([(cx - rx, cy), (cx, cy - ry), (cx + rx, cy), (cx, cy + ry)])


def frame_diamond(kind, w, h):
    parts = broken_frame(w, h, 10, 2.2, 9)
    if kind == "transom":
        cx, cy = w / 2, h / 2
        parts.append(diamond(cx, cy, 11, 16))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            parts.append(slot([(cx + dx * 20 + dy * 0, cy + dy * 26), (cx + dx * 34, cy + dy * 38)], 2.0))
        for fx in (0.25, 0.75):
            parts.append(diamond(w * fx, cy, 5, 8))
        for fx in (0.16, 0.34, 0.66, 0.84):
            parts.append(Point(w * fx, cy).buffer(2.2))
    elif kind == "square":
        parts.append(diamond(w / 2, h / 2, 7, 11))
    else:
        cx, cy = w / 2, h * 0.55
        parts.append(diamond(cx, cy, 10, 16))
        for dy in (-34, 34):
            parts.append(diamond(cx, cy + dy, 5, 8))
        for dy in (-58, 58):
            parts.append(Point(cx, cy + dy).buffer(2.2))
    return unary_union(parts)


def thin_lines(kind, w, h):
    parts = []
    if kind == "transom":
        cx, cy = w / 2, h / 2
        for i in range(5):
            y = 18 + i * (h - 36) / 4
            parts.append(slot([(16, y), (cx - 30, y)], 1.8))
            parts.append(slot([(cx + 30, y), (w - 16, y)], 1.8))
        parts.append(Point(cx, cy).buffer(9).difference(Point(cx, cy).buffer(6.5)).difference(box(cx - 1.5, 0, cx + 1.5, h)))
        parts.append(Point(cx, cy).buffer(2.5))
    elif kind == "square":
        for fx in (0.35, 0.5, 0.65):
            parts.append(slot([(w * fx, 14), (w * fx, h - 14)], 1.8))
    else:
        for fx in (0.3, 0.7):
            parts.append(slot([(w * fx, 16), (w * fx, h - 16)], 1.8))
        cx, cy = w / 2, h * 0.55
        parts.append(slot([(cx, 16), (cx, cy - 40)], 1.8))
        parts.append(slot([(cx, cy + 40), (cx, h - 16)], 1.8))
        for dy in (-22, 0, 22):
            parts.append(Point(cx, cy + dy).buffer(2.6))
    return unary_union(parts)


def dot_screen(kind, w, h):
    parts = []
    step = 11
    cx, cy = w / 2, h / 2
    for j, y in enumerate(np.arange(12, h - 8, step * 0.87)):
        off = step / 2 if j % 2 else 0
        for x in np.arange(12 + off, w - 8, step):
            if kind == "transom":
                f = 1 - abs(x - cx) / cx  # dots grow toward the centre
            elif kind == "square":
                f = 0.45
            else:
                if abs(x - cx) > w * 0.2:
                    continue
                f = 1 - abs(y - h * 0.55) / (h * 0.5)
            r = 0.8 + 2.2 * max(0.0, f) ** 1.2
            if r > 1.0:
                parts.append(Point(x, y).buffer(r))
    return unary_union(parts)


def sprig(kind, w, h):
    parts = []
    def twig(pts, n_leaves, size, width=2.0):
        stem = slot(pts, width)
        ls = LineString(pts)
        out = [stem]
        for k in range(n_leaves):
            p = ls.interpolate((k + 0.7) / (n_leaves + 0.4), normalized=True)
            q = ls.interpolate(min(1, (k + 0.75) / (n_leaves + 0.4)), normalized=True)
            ang = math.degrees(math.atan2(q.y - p.y, q.x - p.x)) + (40 if k % 2 else -40)
            out.append(leaf(p.x, p.y, size, ang).difference(stem.buffer(1.2)))
        return unary_union(out)
    if kind == "transom":
        cx, cy = w / 2, h / 2
        for side in (-1, 1):
            pts = [(cx + side * t, cy + 10 * math.sin(t / 70 * math.pi)) for t in np.linspace(8, 170, 40)]
            parts.append(twig(pts, 7, 13))
        parts.append(Point(cx, cy).buffer(4))
    elif kind == "square":
        pts = [(w / 2 + 6 * math.sin(t / 30 * math.pi), h - 12 - t) for t in np.linspace(0, h - 30, 30)]
        parts.append(twig(pts, 4, 11))
    else:
        pts = [(30 + 16 * math.sin(t / 150 * math.pi), h - 14 - t) for t in np.linspace(0, h * 0.55, 50)]
        parts.append(twig(pts, 9, 17, 2.4))
    return unary_union(parts)


DESIGNS = {
    "1-geometric-jaali": geometric,
    "2-lotus-mandala": lotus,
    "3-modern-minimal": minimal,
    "4-tree-of-life-peacock": tree_of_life,
    "5-simple-frame-diamond": frame_diamond,
    "6-simple-thin-lines": thin_lines,
    "7-simple-dot-screen": dot_screen,
    "8-simple-leaf-sprig": sprig,
}


def panel_geometry(design, name):
    x0, y0, x1, y1 = PANELS[name]
    w, h = x1 - x0, y1 - y0
    kind = "transom" if name == "transom" else "square" if name.startswith("square") else "side"
    g = design(kind, w, h)
    if name == "square_right" or (name == "side_right" and design is not tree_of_life):
        g = affinity.scale(g, -1, 1, origin=(w / 2, h / 2))
    keep_out = None
    if name == "side_right":
        ex, ey, er = EMBLEM
        keep_out = Point(ex - x0, ey - y0).buffer(er + 7)
    return clip(g, w, h, 5 if kind != "transom" else 4, keep_out), w, h


def islands(geom):
    """Count metal pieces that would fall out (holes inside cut regions)."""
    polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    return sum(len(p.interiors) for p in polys if isinstance(p, Polygon))


# ---------- SVG export ----------

def to_svg(geom, w, h, path, scale=10):
    d = []
    polys = geom.geoms if hasattr(geom, "geoms") else [geom]
    for p in polys:
        if not isinstance(p, Polygon) or p.is_empty:
            continue
        for ring in [p.exterior, *p.interiors]:
            c = list(ring.coords)
            d.append("M" + " L".join(f"{x*scale:.2f},{y*scale:.2f}" for x, y in c) + " Z")
    with open(path, "w") as f:
        f.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w*scale} {h*scale}" '
            f'width="{w*scale}" height="{h*scale}">\n'
            f'<rect x="0" y="0" width="{w*scale}" height="{h*scale}" fill="none" stroke="#000" stroke-width="2"/>\n'
            f'<path fill="#000" fill-rule="evenodd" d="{" ".join(d)}"/>\n</svg>\n'
        )


# ---------- photo compositing ----------

def rasterize(geom, w, h):
    img = Image.new("L", (w * SS, h * SS), 0)
    dr = ImageDraw.Draw(img)
    polys = geom.geoms if hasattr(geom, "geoms") else [geom]
    for p in polys:
        if not isinstance(p, Polygon) or p.is_empty:
            continue
        dr.polygon([(x * SS, y * SS) for x, y in p.exterior.coords], fill=255)
        for r in p.interiors:
            dr.polygon([(x * SS, y * SS) for x, y in r.coords], fill=0)
    return img.resize((w, h), Image.LANCZOS)


def wood_base(w, h, color, vertical, seed):
    rng = np.random.default_rng(seed)
    n = rng.normal(0, 1, (h, w) if vertical else (w, h))
    img = Image.fromarray(((n - n.min()) / (np.ptp(n) + 1e-9) * 255).astype(np.uint8))
    img = img.resize((w, max(2, h // 30)) if vertical else (h, max(2, w // 30)), Image.BILINEAR)
    img = img.resize((w, h) if vertical else (h, w), Image.BICUBIC)
    g = np.asarray(img, dtype=np.float32) / 255 - 0.5
    if not vertical:
        g = g.T
    fine = rng.normal(0, 1, (h, w)).astype(np.float32)
    fine = np.asarray(Image.fromarray(((fine + 4) * 30).clip(0, 255).astype(np.uint8)).filter(
        ImageFilter.BoxBlur(1)), dtype=np.float32) / 255 - 0.5
    shade = 1 + g[..., None] * 0.22 + fine[..., None] * 0.08
    return np.clip(np.array(color, np.float32)[None, None] * shade, 0, 255)


def composite(photo, design):
    out = np.asarray(photo, dtype=np.float32).copy()
    for name, (x0, y0, x1, y1) in PANELS.items():
        geom, w, h = panel_geometry(design, name)
        a = np.asarray(rasterize(geom, w, h), dtype=np.float32) / 255
        region = out[y0:y1, x0:x1]
        lit = name in ("transom", "square_left", "square_right")
        dark = region.mean(axis=2) < np.percentile(region.mean(axis=2), 35)
        base_color = np.median(region[dark], axis=0) * (1.0 if lit else 1.08)
        base = wood_base(w, h, base_color, vertical=not lit and name != "transom", seed=hash(name) % 1000)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        if lit:
            glow = np.zeros((h, w), np.float32)
            for lx in DOWNLIGHTS_X:
                glow += np.exp(-(((xx + x0 - lx) / 70) ** 2 + ((yy + y0 - 212) / 45) ** 2))
            base = base * (1 + glow[..., None] * np.array([0.9, 0.6, 0.25]))
            gold_c, gold_e = np.array([255, 226, 150]), np.array([236, 164, 70])
        else:
            vert = 1 + 0.05 * (1 - yy / h)
            base = base * vert[..., None]
            gold_c, gold_e = np.array([232, 186, 104]), np.array([196, 138, 58])
        am = Image.fromarray((a * 255).astype(np.uint8))
        core = np.asarray(am.filter(ImageFilter.GaussianBlur(2.2)), np.float32) / 255
        t = np.clip((core - 0.35) / 0.65, 0, 1)[..., None]
        gold = gold_e + (gold_c - gold_e) * t
        # sheet thickness: the upper-left inside edge of each cut is in shadow
        sh = np.asarray(am.transform(am.size, Image.AFFINE, (1, 0, -2, 0, 1, -2)), np.float32) / 255
        rim = np.clip(a - sh, 0, 1)[..., None]
        gold = gold * (1 - rim * 0.55) + np.array([90, 50, 22]) * rim * 0.55
        halo = np.asarray(am.filter(ImageFilter.GaussianBlur(5 if lit else 2)), np.float32)[..., None] / 255
        base = base + halo * (np.array([70, 42, 12]) if lit else np.array([18, 10, 2]))
        new = base * (1 - a[..., None]) + gold * a[..., None]
        noise = np.random.default_rng(1).normal(0, 2.2, new.shape)
        out[y0:y1, x0:x1] = np.clip(new + noise, 0, 255)
    img = Image.fromarray(out.astype(np.uint8))
    soft = img.filter(ImageFilter.GaussianBlur(0.45))
    mask = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mask)
    for (x0, y0, x1, y1) in PANELS.values():
        md.rectangle((x0, y0, x1 - 1, y1 - 1), fill=255)
    img = Image.composite(soft, photo, mask)
    # restore the emblem pixels exactly
    ex, ey, er = EMBLEM
    em = Image.new("L", img.size, 0)
    ImageDraw.Draw(em).ellipse((ex - er, ey - er, ex + er, ey + er), fill=255)
    return Image.composite(photo, img, em.filter(ImageFilter.GaussianBlur(1)))


def main(src, outdir):
    photo = Image.open(src).convert("RGB")
    os.makedirs(outdir, exist_ok=True)
    for key, design in DESIGNS.items():
        d = os.path.join(outdir, key)
        os.makedirs(d, exist_ok=True)
        for name in PANELS:
            geom, w, h = panel_geometry(design, name)
            to_svg(geom, w, h, os.path.join(d, f"{name}.svg"))
            print(key, name, "loose metal pieces:", islands(geom))
        composite(photo, design).save(os.path.join(d, "mockup.jpg"), quality=93)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
