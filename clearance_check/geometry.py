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


def _bbox_of(poly: Polygon) -> tuple[float, float, float, float]:
    """多边形的包围盒（比 polygon_bbox 少两次列表分配）。"""
    xs = [point[0] for point in poly]
    ys = [point[1] for point in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _outline_segments(outline: list[Point]):
    """把闭合轮廓拆成边，附带每条边的包围盒（供剪枝用）。"""
    count = len(outline)
    for index in range(count):
        ax, ay = outline[index]
        bx, by = outline[(index + 1) % count]
        min_x, max_x = (ax, bx) if ax <= bx else (bx, ax)
        min_y, max_y = (ay, by) if ay <= by else (by, ay)
        yield (ax, ay, bx, by, min_x, min_y, max_x, max_y)


class _OutlineGrid:
    """板框边的均匀网格索引。

    板框常有数百段，而绝大多数焊盘离板边很远；把边按包围盒挂到格子上之后，
    只需检查焊盘附近那几格，就能判定"距离一定不小于阈值"。
    """

    __slots__ = ("outline", "cell", "segments", "buckets")

    def __init__(self, outline: list[Point], cell: float):
        self.outline = outline
        self.cell = cell
        self.segments: list[tuple[float, float, float, float, float, float, float, float]] = []
        self.buckets: dict[tuple[int, int], list[int]] = {}
        for ax, ay, bx, by, min_x, min_y, max_x, max_y in _outline_segments(outline):
            slot = len(self.segments)
            self.segments.append((ax, ay, bx, by, min_x, min_y, max_x, max_y))
            gx0, gx1 = math.floor(min_x / cell), math.floor(max_x / cell)
            gy0, gy1 = math.floor(min_y / cell), math.floor(max_y / cell)
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    self.buckets.setdefault((gx, gy), []).append(slot)

    def near(
        self, box: tuple[float, float, float, float], radius: float
    ) -> list[int]:
        """返回包围盒向外扩 radius 后覆盖到的所有边索引。"""
        cell = self.cell
        x0, y0, x1, y1 = box
        found: set[int] = set()
        for gx in range(math.floor((x0 - radius) / cell), math.floor((x1 + radius) / cell) + 1):
            for gy in range(math.floor((y0 - radius) / cell), math.floor((y1 + radius) / cell) + 1):
                bucket = self.buckets.get((gx, gy))
                if bucket:
                    found.update(bucket)
        return list(found)


_GRID_CACHE: dict[int, _OutlineGrid] = {}


def _outline_grid(outline: list[Point]) -> _OutlineGrid:
    """按对象身份缓存最近一块板的索引（板框在计算期间不会变）。"""
    key = id(outline)
    cached = _GRID_CACHE.get(key)
    if cached is not None and cached.outline is outline:
        return cached
    xs = [point[0] for point in outline]
    ys = [point[1] for point in outline]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    grid = _OutlineGrid(outline, max(span / 96.0, 0.5))
    _GRID_CACHE.clear()
    _GRID_CACHE[key] = grid
    return grid


def boundary_distance(
    poly: list[Point], outline: list[Point], limit: float | None = None
) -> float:
    """多边形到闭合轮廓（板框）的最小距离。

    与 ``polygon_distance`` 不同：即使多边形完全落在板框内部，也返回它到
    板框边缘的距离（后者对被包含的情形会返回 0）。

    ``limit`` 用于"只关心是否小于阈值"的调用方：传入后若可确认最小距离
    必然不小于 ``limit``，直接返回 ``limit`` 而不做精确求解（结果只会偏大，
    用于 ``distance < limit`` 之类的比较是安全的）。
    """
    if len(outline) < 2 or len(poly) < 2:
        return 0.0
    px0, py0, px1, py1 = _bbox_of(poly)

    if limit is not None and limit > 0.0:
        grid = _outline_grid(outline)
        nearby = grid.near((px0, py0, px1, py1), limit)
        if not nearby:
            return limit
        segments = [grid.segments[index] for index in nearby]
    else:
        segments = _outline_segments(outline)

    best = float("inf")
    best_sq = best
    npoly = len(poly)
    for ax, ay, bx, by, min_x, min_y, max_x, max_y in segments:
        # 边包围盒到多边形包围盒的下界；不小于当前最优就没有必要精确计算
        dx = px0 - max_x if px0 > max_x else (min_x - px1 if min_x > px1 else 0.0)
        dy = py0 - max_y if py0 > max_y else (min_y - py1 if min_y > py1 else 0.0)
        if dx * dx + dy * dy >= best_sq:
            continue
        for j in range(npoly):
            distance = _seg_distance(
                (ax, ay), (bx, by), poly[j], poly[(j + 1) % npoly]
            )
            if distance < best:
                best = distance
                if best <= _EPS:
                    return 0.0
                best_sq = best * best
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


def point_in_polygon_any(p: Point, poly: Polygon) -> bool:
    """任意多边形（含凹形）内部判定，射线法。边界上视为外部。"""
    if len(poly) < 3:
        return False
    x, y = p
    inside = False
    count = len(poly)
    for index in range(count):
        x1, y1 = poly[index]
        x2, y2 = poly[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


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
    """返回两个多边形最近点对，用于界面高亮。

    板框这类几百段的长轮廓会先按包围盒下界剪枝，只对可能更近的边做精确计算。
    """
    ax0, ay0, ax1, ay1 = _bbox_of(poly_a)
    # 预先把 b 的每条边连包围盒算好，剪枝时只做四次比较
    edges_b: list[tuple[Point, Point, float, float, float, float]] = []
    nb = len(poly_b)
    for j in range(nb):
        b1 = poly_b[j]
        b2 = poly_b[(j + 1) % nb]
        min_x, max_x = (b1[0], b2[0]) if b1[0] <= b2[0] else (b2[0], b1[0])
        min_y, max_y = (b1[1], b2[1]) if b1[1] <= b2[1] else (b2[1], b1[1])
        edges_b.append((b1, b2, min_x, min_y, max_x, max_y))

    best = float("inf")
    best_sq = best
    pa, pb = poly_a[0], poly_b[0]
    na = len(poly_a)
    for i in range(na):
        a1 = poly_a[i]
        a2 = poly_a[(i + 1) % na]
        for b1, b2, min_x, min_y, max_x, max_y in edges_b:
            dx = ax0 - max_x if ax0 > max_x else (min_x - ax1 if min_x > ax1 else 0.0)
            dy = ay0 - max_y if ay0 > max_y else (min_y - ay1 if min_y > ay1 else 0.0)
            if dx * dx + dy * dy >= best_sq:
                continue
            distance = _seg_distance(a1, a2, b1, b2)
            if distance < best:
                best = distance
                best_sq = best * best
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
