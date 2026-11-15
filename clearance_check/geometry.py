"""几何计算：把焊盘 / 走线转成凸多边形，并求两个多边形之间的最小距离。

说明：
  * 焊盘按外形生成多边形（矩形 4 点、圆形/椭圆 16 点采样、八边形 8 点）。
  * 走线按"胶囊体"生成多边形（线段两侧偏移半线宽 + 两端半圆采样）。
  * 距离计算为凸多边形最小距离，相交视为 0。
"""

from __future__ import annotations

import math

from .model import Pad, Track

Point = tuple[float, float]
Polygon = list[Point]

_EPS = 1e-9


# --------------------------------------------------------------------------
# 基础变换
# --------------------------------------------------------------------------
def rotate_point(x: float, y: float, deg: float) -> Point:
    if abs(deg) < _EPS:
        return x, y
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return x * c - y * s, x * s + y * c


def _arc(cx: float, cy: float, radius: float, a0: float, a1: float, steps: int) -> list[Point]:
    pts: list[Point] = []
    for i in range(steps + 1):
        a = a0 + (a1 - a0) * i / steps
        pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return pts


# --------------------------------------------------------------------------
# 焊盘 / 走线 -> 多边形
# --------------------------------------------------------------------------
def pad_polygon(pad: Pad) -> Polygon:
    hw = max(pad.width, 0.001) / 2.0
    hh = max(pad.height, 0.001) / 2.0
    shape = (pad.shape or "rect").strip().lower()

    local: list[Point]
    if shape in ("round", "circle", "ellipse") or shape.startswith("round"):
        steps = 16
        local = [
            (hw * math.cos(2 * math.pi * i / steps), hh * math.sin(2 * math.pi * i / steps))
            for i in range(steps)
        ]
    elif shape.startswith("oct"):
        r = max(hw, hh)
        local = [
            (r * math.cos(math.pi / 8 + 2 * math.pi * i / 8), r * math.sin(math.pi / 8 + 2 * math.pi * i / 8))
            for i in range(8)
        ]
    else:  # rect 及其他按矩形处理（最保守的近似）
        local = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]

    return [(pad.x + rx, pad.y + ry) for rx, ry in (rotate_point(px, py, pad.rotation) for px, py in local)]


def capsule_polygon(x1: float, y1: float, x2: float, y2: float, width: float) -> Polygon:
    radius = max(width, 0.001) / 2.0
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < _EPS:
        return _arc(x1, y1, radius, 0.0, 2 * math.pi, 16)[:-1]
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    base = math.atan2(ny, nx)

    pts: list[Point] = [
        (x1 + nx * radius, y1 + ny * radius),
        (x2 + nx * radius, y2 + ny * radius),
    ]
    pts += _arc(x2, y2, radius, base, base - math.pi, 6)[1:]
    pts.append((x1 - nx * radius, y1 - ny * radius))
    pts += _arc(x1, y1, radius, base + math.pi, base + 2 * math.pi, 6)[1:-1]
    return pts


def track_polygon(track: Track) -> Polygon:
    return capsule_polygon(track.x1, track.y1, track.x2, track.y2, track.width)


# --------------------------------------------------------------------------
# 多边形工具
# --------------------------------------------------------------------------
def polygon_bbox(poly: Polygon) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_gap(box_a: tuple[float, float, float, float],
             box_b: tuple[float, float, float, float]) -> float:
    """两个包围盒之间的距离（相交为 0）。"""
    ax0, ay0, ax1, ay1 = box_a
    bx0, by0, bx1, by1 = box_b
    dx = max(0.0, max(ax0 - bx1, bx0 - ax1))
    dy = max(0.0, max(ay0 - by1, by0 - ay1))
    if dx <= 0.0 and dy <= 0.0:
        return 0.0
    return math.hypot(dx, dy)


def _seg_distance(p1: Point, p2: Point, p3: Point, p4: Point) -> float:
    """线段到线段的距离；相交返回 0。"""
    d1 = _cross(p3, p4, p1)
    d2 = _cross(p3, p4, p2)
    d3 = _cross(p1, p2, p3)
    d4 = _cross(p1, p2, p4)

    if ((d1 > _EPS and d2 < -_EPS) or (d1 < -_EPS and d2 > _EPS)) and \
       ((d3 > _EPS and d4 < -_EPS) or (d3 < -_EPS and d4 > _EPS)):
        return 0.0

    best = min(
        _point_seg_distance(p1, p3, p4),
        _point_seg_distance(p2, p3, p4),
        _point_seg_distance(p3, p1, p2),
        _point_seg_distance(p4, p1, p2),
    )
    return best


def _cross(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_seg_distance(p: Point, a: Point, b: Point) -> float:
    abx, aby = b[0] - a[0], b[1] - a[1]
    apx, apy = p[0] - a[0], p[1] - a[1]
    denom = abx * abx + aby * aby
    if denom < _EPS:
        return math.hypot(apx, apy)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / denom))
    return math.hypot(apx - t * abx, apy - t * aby)


def boundary_distance(poly: list[Point], outline: list[Point]) -> float:
    """多边形到闭合轮廓（板框）的最小距离。

    与 ``polygon_distance`` 不同：即使多边形完全落在板框内部，也返回它到
    板框边缘的距离（后者对被包含的情形会返回 0）。
    """
    if len(outline) < 2 or len(poly) < 2:
        return 0.0
    best = float("inf")
    count = len(outline)
    for index in range(count):
        a = outline[index]
        b = outline[(index + 1) % count]
        for j in range(len(poly)):
            c = poly[j]
            d = poly[(j + 1) % len(poly)]
            p1, p2 = _closest_pair_on_segments(a, b, c, d)
            best = min(best, math.hypot(p1[0] - p2[0], p1[1] - p2[1]))
    return best


def point_in_polygon(p: Point, poly: Polygon) -> bool:
    """凸多边形内部判定（支持任意绕向）。"""
    n = len(poly)
    if n < 3:
        return False
    pos = neg = False
    for i in range(n):
        c = _cross(poly[i], poly[(i + 1) % n], p)
        if c > _EPS:
            pos = True
        elif c < -_EPS:
            neg = True
        if pos and neg:
            return False
    return True


def polygon_distance(poly_a: Polygon, poly_b: Polygon) -> float:
    """两个多边形之间的最小距离，相交或包含返回 0。"""
    if not poly_a or not poly_b:
        return float("inf")

    if point_in_polygon(poly_a[0], poly_b) or point_in_polygon(poly_b[0], poly_a):
        return 0.0

    best = float("inf")
    na, nb = len(poly_a), len(poly_b)
    for i in range(na):
        a1 = poly_a[i]
        a2 = poly_a[(i + 1) % na]
        for j in range(nb):
            b1 = poly_b[j]
            b2 = poly_b[(j + 1) % nb]
            d = _seg_distance(a1, a2, b1, b2)
            if d < best:
                best = d
                if best <= _EPS:
                    return 0.0
    return best


def nearest_points(poly_a: Polygon, poly_b: Polygon) -> tuple[Point, Point]:
    """返回两个多边形最近点对，用于界面高亮。"""
    best = float("inf")
    pa, pb = poly_a[0], poly_b[0]
    na, nb = len(poly_a), len(poly_b)
    for i in range(na):
        a1, a2 = poly_a[i], poly_a[(i + 1) % na]
        for j in range(nb):
            b1, b2 = poly_b[j], poly_b[(j + 1) % nb]
            d = _seg_distance(a1, a2, b1, b2)
            if d < best:
                best = d
                pa, pb = _closest_pair_on_segments(a1, a2, b1, b2)
    return pa, pb


def _closest_pair_on_segments(a1: Point, a2: Point, b1: Point, b2: Point) -> tuple[Point, Point]:
    candidates: list[tuple[float, Point, Point]] = []
    for p, (s1, s2) in ((a1, (b1, b2)), (a2, (b1, b2)), (b1, (a1, a2)), (b2, (a1, a2))):
        q = _closest_point_on_seg(p, s1, s2)
        candidates.append((math.hypot(p[0] - q[0], p[1] - q[1]), p, q))
    best = min(candidates, key=lambda c: c[0])
    return best[1], best[2]


def _closest_point_on_seg(p: Point, a: Point, b: Point) -> Point:
    abx, aby = b[0] - a[0], b[1] - a[1]
    denom = abx * abx + aby * aby
    if denom < _EPS:
        return a
    t = max(0.0, min(1.0, ((p[0] - a[0]) * abx + (p[1] - a[1]) * aby) / denom))
    return a[0] + t * abx, a[1] + t * aby
