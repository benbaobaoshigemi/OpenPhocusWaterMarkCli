"""Pure-Python + Pillow super-sampled rasterizer for Android VectorDrawable and SVG paths."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image


_TOKEN_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")
_ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def parse_hex_color(color_str: Optional[str], default: Tuple[int, int, int, int] = (0, 0, 0, 255)) -> Tuple[int, int, int, int]:
    """Parse #RRGGBB or #AARRGGBB into (R, G, B, A)."""
    if not color_str:
        return default
    s = color_str.strip()
    if s in ("none", "transparent"):
        return (0, 0, 0, 0)
    if s == "black":
        return (0, 0, 0, 255)
    if s == "white":
        return (255, 255, 255, 255)
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            r, g, b = (int(c * 2, 16) for c in h)
            return (r, g, b, 255)
        if len(h) == 6:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
        if len(h) == 8:
            a = int(h[0:2], 16)
            return (int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16), a)
    return default


def argb_int_to_rgba(argb: int) -> Tuple[int, int, int, int]:
    """Convert signed/unsigned 32-bit Android ARGB int to (R, G, B, A)."""
    u = argb & 0xFFFFFFFF
    return ((u >> 16) & 0xFF, (u >> 8) & 0xFF, u & 0xFF, (u >> 24) & 0xFF)


def _cubic_point(p0: Tuple[float, float], p1: Tuple[float, float], p2: Tuple[float, float], p3: Tuple[float, float], t: float) -> Tuple[float, float]:
    mt = 1.0 - t
    mt2 = mt * mt
    t2 = t * t
    return (
        mt2 * mt * p0[0] + 3.0 * mt2 * t * p1[0] + 3.0 * mt * t2 * p2[0] + t2 * t * p3[0],
        mt2 * mt * p0[1] + 3.0 * mt2 * t * p1[1] + 3.0 * mt * t2 * p2[1] + t2 * t * p3[1],
    )


def _quad_point(p0: Tuple[float, float], p1: Tuple[float, float], p2: Tuple[float, float], t: float) -> Tuple[float, float]:
    mt = 1.0 - t
    return (
        mt * mt * p0[0] + 2.0 * mt * t * p1[0] + t * t * p2[0],
        mt * mt * p0[1] + 2.0 * mt * t * p1[1] + t * t * p2[1],
    )


def _arc_to_points(
    x1: float, y1: float, rx: float, ry: float, phi_deg: float, large_arc: int, sweep: int, x2: float, y2: float, steps: int = 24
) -> List[Tuple[float, float]]:
    if (x1 == x2 and y1 == y2) or rx == 0 or ry == 0:
        return [(x2, y2)]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg % 360.0)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx2, dy2 = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_p * dx2 + sin_p * dy2
    y1p = -sin_p * dx2 + cos_p * dy2

    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s

    num = max(0.0, rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p)
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(num / den) if den > 1e-12 else 0.0
    if large_arc == sweep:
        coef = -coef
    cxp = coef * (rx * y1p / ry)
    cyp = coef * (-ry * x1p / rx)
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2.0

    def _ang(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        mag = math.hypot(ux, uy) * math.hypot(vx, vy)
        c = max(-1.0, min(1.0, dot / mag)) if mag > 1e-12 else 1.0
        a = math.acos(c)
        return -a if (ux * vy - uy * vx) < 0 else a

    theta1 = _ang(1.0, 0.0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = _ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if sweep == 0 and dtheta > 0:
        dtheta -= 2.0 * math.pi
    elif sweep == 1 and dtheta < 0:
        dtheta += 2.0 * math.pi

    pts: List[Tuple[float, float]] = []
    for k in range(1, steps + 1):
        t = theta1 + dtheta * (k / steps)
        xr = rx * math.cos(t)
        yr = ry * math.sin(t)
        pts.append((cos_p * xr - sin_p * yr + cx, sin_p * xr + cos_p * yr + cy))
    return pts


def path_to_subpaths(d: str, curve_steps: int = 18) -> List[List[Tuple[float, float]]]:
    """Flatten an SVG/VectorDrawable path string into a list of polyline subpaths."""
    tokens = _TOKEN_RE.findall(d)
    idx = 0
    n = len(tokens)
    cmd = ""
    cx, cy = 0.0, 0.0
    sx, sy = 0.0, 0.0
    last_ctrl: Optional[Tuple[float, float]] = None
    last_cmd = ""
    subpaths: List[List[Tuple[float, float]]] = []
    cur: List[Tuple[float, float]] = []

    def is_cmd(tok: str) -> bool:
        return len(tok) == 1 and tok.isalpha()

    def next_float() -> float:
        nonlocal idx
        v = float(tokens[idx])
        idx += 1
        return v

    while idx < n:
        if is_cmd(tokens[idx]):
            cmd = tokens[idx]
            idx += 1
        if not cmd:
            break

        if cmd in ("M", "m"):
            if cur:
                subpaths.append(cur)
                cur = []
            x, y = next_float(), next_float()
            if cmd == "m":
                x += cx
                y += cy
            cx, cy = x, y
            sx, sy = x, y
            cur.append((cx, cy))
            last_ctrl = None
            last_cmd = cmd
            cmd = "L" if cmd == "M" else "l"
            continue

        if cmd in ("Z", "z"):
            if cur:
                if cur[-1] != (sx, sy):
                    cur.append((sx, sy))
                subpaths.append(cur)
                cur = []
            cx, cy = sx, sy
            last_ctrl = None
            last_cmd = cmd
            continue

        if cmd in ("L", "l"):
            x, y = next_float(), next_float()
            if cmd == "l":
                x += cx
                y += cy
            cx, cy = x, y
            cur.append((cx, cy))
            last_ctrl = None
        elif cmd in ("H", "h"):
            x = next_float()
            if cmd == "h":
                x += cx
            cx = x
            cur.append((cx, cy))
            last_ctrl = None
        elif cmd in ("V", "v"):
            y = next_float()
            if cmd == "v":
                y += cy
            cy = y
            cur.append((cx, cy))
            last_ctrl = None
        elif cmd in ("C", "c"):
            x1, y1, x2, y2, x, y = (next_float() for _ in range(6))
            if cmd == "c":
                x1 += cx; y1 += cy; x2 += cx; y2 += cy; x += cx; y += cy
            p0 = (cx, cy)
            for k in range(1, curve_steps + 1):
                cur.append(_cubic_point(p0, (x1, y1), (x2, y2), (x, y), k / curve_steps))
            last_ctrl = (x2, y2)
            cx, cy = x, y
        elif cmd in ("S", "s"):
            x2, y2, x, y = (next_float() for _ in range(4))
            if cmd == "s":
                x2 += cx; y2 += cy; x += cx; y += cy
            if last_cmd in ("C", "c", "S", "s") and last_ctrl is not None:
                x1, y1 = 2 * cx - last_ctrl[0], 2 * cy - last_ctrl[1]
            else:
                x1, y1 = cx, cy
            p0 = (cx, cy)
            for k in range(1, curve_steps + 1):
                cur.append(_cubic_point(p0, (x1, y1), (x2, y2), (x, y), k / curve_steps))
            last_ctrl = (x2, y2)
            cx, cy = x, y
        elif cmd in ("Q", "q"):
            x1, y1, x, y = (next_float() for _ in range(4))
            if cmd == "q":
                x1 += cx; y1 += cy; x += cx; y += cy
            p0 = (cx, cy)
            for k in range(1, curve_steps + 1):
                cur.append(_quad_point(p0, (x1, y1), (x, y), k / curve_steps))
            last_ctrl = (x1, y1)
            cx, cy = x, y
        elif cmd in ("T", "t"):
            x, y = next_float(), next_float()
            if cmd == "t":
                x += cx; y += cy
            if last_cmd in ("Q", "q", "T", "t") and last_ctrl is not None:
                x1, y1 = 2 * cx - last_ctrl[0], 2 * cy - last_ctrl[1]
            else:
                x1, y1 = cx, cy
            p0 = (cx, cy)
            for k in range(1, curve_steps + 1):
                cur.append(_quad_point(p0, (x1, y1), (x, y), k / curve_steps))
            last_ctrl = (x1, y1)
            cx, cy = x, y
        elif cmd in ("A", "a"):
            rx, ry, rot, laf, sf, x, y = (next_float() for _ in range(7))
            if cmd == "a":
                x += cx; y += cy
            cur.extend(_arc_to_points(cx, cy, rx, ry, rot, int(laf), int(sf), x, y))
            cx, cy = x, y
            last_ctrl = None
        else:
            break
        last_cmd = cmd

    if cur:
        subpaths.append(cur)
    return subpaths


def _fill_compound_subpaths(
    width: int,
    height: int,
    subpaths: Sequence[Sequence[Tuple[float, float]]],
    color: Tuple[int, int, int, int],
    fill_rule: str = "nonzero",
) -> Image.Image:
    """Scanline fill compound polygons with nonzero or evenodd winding (handles inner holes accurately)."""
    if color[3] == 0 or width <= 0 or height <= 0:
        return Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))

    edges: List[Tuple[float, float, float, float, int]] = []
    min_y_all = height
    max_y_all = 0
    for poly in subpaths:
        if len(poly) < 3:
            continue
        pts = list(poly)
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            if y0 == y1:
                continue
            if y0 < y1:
                edges.append((y0, y1, x0, (x1 - x0) / (y1 - y0), 1))
                min_y_all = min(min_y_all, y0)
                max_y_all = max(max_y_all, y1)
            else:
                edges.append((y1, y0, x1, (x0 - x1) / (y0 - y1), -1))
                min_y_all = min(min_y_all, y1)
                max_y_all = max(max_y_all, y0)

    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if not edges:
        return out

    px = out.load()
    y_start = max(0, int(math.floor(min_y_all)))
    y_end = min(height - 1, int(math.ceil(max_y_all)))
    even_odd = fill_rule.lower() in ("evenodd", "even_odd")

    for y in range(y_start, y_end + 1):
        sy = y + 0.5
        hits: List[Tuple[float, int]] = []
        for ey0, ey1, ex0, inv_slope, d in edges:
            if ey0 <= sy < ey1:
                hits.append((ex0 + (sy - ey0) * inv_slope, d))
        if not hits:
            continue
        hits.sort(key=lambda item: item[0])
        if even_odd:
            for k in range(0, len(hits) - 1, 2):
                x_left = max(0, int(math.ceil(hits[k][0] - 0.5)))
                x_right = min(width - 1, int(math.floor(hits[k + 1][0] - 0.5)))
                for x in range(x_left, x_right + 1):
                    px[x, y] = color
        else:
            winding = 0
            span_start: Optional[float] = None
            for x_hit, d in hits:
                prev_w = winding
                winding += d
                if prev_w == 0 and winding != 0:
                    span_start = x_hit
                elif prev_w != 0 and winding == 0 and span_start is not None:
                    x_left = max(0, int(math.ceil(span_start - 0.5)))
                    x_right = min(width - 1, int(math.floor(x_hit - 0.5)))
                    for x in range(x_left, x_right + 1):
                        px[x, y] = color
                    span_start = None
    return out


def rasterize_vector_drawable(
    xml_path: str,
    width: int,
    height: int,
    override_color: Optional[Tuple[int, int, int, int]] = None,
    supersample: int = 3,
) -> Image.Image:
    """Rasterize an Android VectorDrawable XML exactly to (width, height) RGBA."""
    width = max(1, int(width))
    height = max(1, int(height))
    root = ET.parse(xml_path).getroot()
    vw = float(root.attrib.get(f"{_ANDROID_NS}viewportWidth", str(width)))
    vh = float(root.attrib.get(f"{_ANDROID_NS}viewportHeight", str(height)))

    ss = max(1, min(supersample, 4 if width * height <= 2_000_000 else 2))
    rw, rh = width * ss, height * ss
    sx, sy = rw / vw, rh / vh

    canvas = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))

    def walk(node: ET.Element) -> None:
        tag = node.tag.split("}")[-1]
        if tag == "path":
            d = node.attrib.get(f"{_ANDROID_NS}pathData")
            fill_str = node.attrib.get(f"{_ANDROID_NS}fillColor")
            fill_type = node.attrib.get(f"{_ANDROID_NS}fillType", "nonZero")
            if d:
                col = override_color if override_color is not None else parse_hex_color(fill_str)
                subs = [
                    [(pt[0] * sx, pt[1] * sy) for pt in sp]
                    for sp in path_to_subpaths(d, curve_steps=20)
                ]
                layer = _fill_compound_subpaths(rw, rh, subs, col, fill_rule=fill_type)
                canvas.alpha_composite(layer)
        for child in node:
            walk(child)

    walk(root)
    if ss > 1:
        return canvas.resize((width, height), Image.Resampling.LANCZOS)
    return canvas


def _parse_svg_length(val: Optional[str]) -> Optional[float]:
    if not val:
        return None
    m = re.match(r"^\s*([-+]?(?:\d*\.\d+|\d+))", val)
    return float(m.group(1)) if m else None


def rasterize_svg_signature(
    svg_text: str,
    target_w: int,
    target_h: int,
    rotation_deg: float = 0.0,
    pen_color_argb: Optional[int] = None,
) -> Image.Image:
    """Rasterize an SVG signature matching s7.D.e(svg, target_w, target_h, rotation)."""
    from PIL import ImageDraw

    root = ET.fromstring(svg_text)
    vb = root.attrib.get("viewBox")
    vb_x, vb_y = 0.0, 0.0
    if vb:
        parts = [float(x) for x in re.split(r"[\s,]+", vb.strip()) if x]
        if len(parts) == 4:
            vb_x, vb_y, vw, vh = parts
        else:
            vw, vh = float(target_w), float(target_h)
    else:
        vw = _parse_svg_length(root.attrib.get("width")) or float(target_w)
        vh = _parse_svg_length(root.attrib.get("height")) or float(target_h)

    # Match D.e lines 777-783:
    # Caller passes (target_w=rectK.width, target_h=rectK.height).
    # D.e initializes (w=i11, h=i10), swaps them when rotation == 0 -> (target_w, target_h),
    # and keeps (target_h, target_w) when rotation != 0 so rotating by -rotation produces (target_w, target_h).
    if rotation_deg == 0.0:
        draw_w, draw_h = target_w, target_h
    else:
        draw_w, draw_h = target_h, target_w
    draw_w = max(1, int(draw_w))
    draw_h = max(1, int(draw_h))

    sx = draw_w / vw if vw > 0 else 1.0
    sy = draw_h / vh if vh > 0 else 1.0
    scale_stroke = (sx + sy) * 0.5

    img = Image.new("RGBA", (draw_w, draw_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def map_pts(pts: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
        return [((x - vb_x) * sx, (y - vb_y) * sy) for x, y in pts]

    def render_node(node: ET.Element, inherited: Dict[str, str]) -> None:
        style = dict(inherited)
        for k in ("fill", "stroke", "stroke-width", "fill-rule", "stroke-linecap"):
            if k in node.attrib:
                style[k] = node.attrib[k]
        if "style" in node.attrib:
            for item in node.attrib["style"].split(";"):
                if ":" in item:
                    k, v = item.split(":", 1)
                    style[k.strip()] = v.strip()

        tag = node.tag.split("}")[-1]
        fill_val = style.get("fill", "#000000")
        stroke_val = style.get("stroke", "none")
        sw = max(1, int(round((_parse_svg_length(style.get("stroke-width")) or 1.0) * scale_stroke)))

        if tag == "path" and "d" in node.attrib:
            subs = [map_pts(sp) for sp in path_to_subpaths(node.attrib["d"])]
            if fill_val.lower() not in ("none", "transparent"):
                col = parse_hex_color(fill_val)
                img.alpha_composite(_fill_compound_subpaths(draw_w, draw_h, subs, col, style.get("fill-rule", "nonzero")))
            if stroke_val.lower() not in ("none", "transparent"):
                scol = parse_hex_color(stroke_val)
                for sp in subs:
                    if len(sp) >= 2:
                        draw.line(sp, fill=scol, width=sw, joint="curve")
        elif tag in ("polyline", "polygon") and "points" in node.attrib:
            nums = [float(x) for x in re.split(r"[\s,]+", node.attrib["points"].strip()) if x]
            pts = map_pts([(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)])
            if tag == "polygon" and fill_val.lower() not in ("none", "transparent"):
                col = parse_hex_color(fill_val)
                img.alpha_composite(_fill_compound_subpaths(draw_w, draw_h, [pts], col))
            if stroke_val.lower() not in ("none", "transparent") and len(pts) >= 2:
                draw.line(pts, fill=parse_hex_color(stroke_val), width=sw, joint="curve")
        elif tag == "line":
            x1 = float(node.attrib.get("x1", 0))
            y1 = float(node.attrib.get("y1", 0))
            x2 = float(node.attrib.get("x2", 0))
            y2 = float(node.attrib.get("y2", 0))
            scol = parse_hex_color(stroke_val if stroke_val != "none" else "#000000")
            draw.line(map_pts([(x1, y1), (x2, y2)]), fill=scol, width=sw)
        elif tag == "rect":
            rx0 = float(node.attrib.get("x", 0))
            ry0 = float(node.attrib.get("y", 0))
            rw_r = float(node.attrib.get("width", 0))
            rh_r = float(node.attrib.get("height", 0))
            if rw_r > 0 and rh_r > 0:
                pts = map_pts([(rx0, ry0), (rx0 + rw_r, ry0), (rx0 + rw_r, ry0 + rh_r), (rx0, ry0 + rh_r)])
                if fill_val.lower() not in ("none", "transparent"):
                    img.alpha_composite(_fill_compound_subpaths(draw_w, draw_h, [pts], parse_hex_color(fill_val)))
                if stroke_val.lower() not in ("none", "transparent"):
                    draw.line(pts + [pts[0]], fill=parse_hex_color(stroke_val), width=sw)
        elif tag in ("circle", "ellipse"):
            cx_e = float(node.attrib.get("cx", 0))
            cy_e = float(node.attrib.get("cy", 0))
            if tag == "circle":
                rx_e = ry_e = float(node.attrib.get("r", 0))
            else:
                rx_e = float(node.attrib.get("rx", 0))
                ry_e = float(node.attrib.get("ry", 0))
            if rx_e > 0 and ry_e > 0:
                pts = map_pts([
                    (cx_e + rx_e * math.cos(2.0 * math.pi * k / 48.0), cy_e + ry_e * math.sin(2.0 * math.pi * k / 48.0))
                    for k in range(48)
                ])
                if fill_val.lower() not in ("none", "transparent"):
                    img.alpha_composite(_fill_compound_subpaths(draw_w, draw_h, [pts], parse_hex_color(fill_val)))
                if stroke_val.lower() not in ("none", "transparent"):
                    draw.line(pts + [pts[0]], fill=parse_hex_color(stroke_val), width=sw)

        for ch in node:
            render_node(ch, style)

    render_node(root, {})

    # Aspect-fit scaling as in D.e L791-800
    if target_w > 0 and target_h > 0:
        cur_ratio = img.width / img.height
        tgt_w_f = float(draw_w)
        tgt_h_f = float(draw_h)
        if tgt_h_f / img.height > tgt_w_f / img.width:
            nw, nh = draw_w, max(1, int(tgt_w_f / cur_ratio))
        else:
            nw, nh = max(1, int(tgt_h_f * cur_ratio)), draw_h
        if (nw, nh) != img.size:
            img = img.resize((nw, nh), Image.Resampling.LANCZOS)

    # Rotate by -rotation_deg (BitmapUtils.C(bmp, -f9)); PIL rotate uses CCW positive, Android Matrix.postRotate uses CW positive -> +rotation_deg in PIL!
    if rotation_deg != 0.0:
        img = img.rotate(rotation_deg, resample=Image.Resampling.BICUBIC, expand=True)

    # Tint via BitmapUtils.c when pen_color_argb is specified and != -1
    if pen_color_argb is not None and pen_color_argb != -1:
        r, g, b, _ = argb_int_to_rgba(pen_color_argb)
        px = img.load()
        for y in range(img.height):
            for x in range(img.width):
                _, _, _, a = px[x, y]
                if a > 0:
                    px[x, y] = (r, g, b, a)
    return img
