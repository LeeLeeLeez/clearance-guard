"""板级挖空（避距槽）对爬电距离的影响。

PCB 上铣出的槽会把沿面路径“绕远”，因此：

* 直线穿过槽的两点之间，**电气间隙**按直线距离判（可放宽到 6 mm）；
* **爬电距离**则按绕过槽口的路径长度判（要求 8 mm）。

这里用槽的轴对齐外框近似（Altium 的槽基本都是矩形/圆角矩形），
提供“线段是否穿槽”和“绕槽沿面路径长度”两个基础能力。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SlotRect:
    """轴对齐的避距槽（mm）。"""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return abs(self.x2 - self.x1)

    @property
    def height(self) -> float:
        return abs(self.y2 - self.y1)


def _clip_segment(
    ax: float, ay: float, bx: float, by: float, rect: SlotRect
) -> tuple[float, float] | None:
    """Liang–Barsky：返回线段在矩形内的参数区间 (t0, t1)，无交返回 None。"""
    xmin, xmax = min(rect.x1, rect.x2), max(rect.x1, rect.x2)
    ymin, ymax = min(rect.y1, rect.y2), max(rect.y1, rect.y2)
    dx = bx - ax
    dy = by - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - xmin), (dx, xmax - ax), (-dy, ay - ymin), (dy, ymax - ay)):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return None
            t0 = max(t0, r)
        else:
            if r < t0:
                return None
            t1 = min(t1, r)
    if t1 - t0 <= 1e-9:
        return None
    return t0, t1


def _perimeter_position(rect: SlotRect, x: float, y: float) -> float:
    """点在矩形边界上的位置（从左上角顺时针的弧长，mm）。"""
    xmin, xmax = min(rect.x1, rect.x2), max(rect.x1, rect.x2)
    ymin, ymax = min(rect.y1, rect.y2), max(rect.y1, rect.y2)
    w = xmax - xmin
    h = ymax - ymin
    eps = 1e-6
    if abs(y - ymax) < eps:            # 上边，从左到右
        return min(max(x - xmin, 0.0), w)
    if abs(x - xmax) < eps:            # 右边，从上到下
        return w + min(max(ymax - y, 0.0), h)
    if abs(y - ymin) < eps:            # 下边，从右到左
        return w + h + min(max(xmax - x, 0.0), w)
    return 2 * w + h + min(max(y - ymin, 0.0), h)   # 左边，从下到上


def slot_path(
    ax: float, ay: float, bx: float, by: float, rect: SlotRect
) -> float | None:
    """若线段 a→b 穿过槽，返回绕槽的沿面路径长度（mm）；不穿槽返回 None。"""
    span = _clip_segment(ax, ay, bx, by, rect)
    if span is None:
        return None
    t0, t1 = span
    entry = (ax + (bx - ax) * t0, ay + (by - ay) * t0)
    exit_ = (ax + (bx - ax) * t1, ay + (by - ay) * t1)

    straight_in = math.hypot(entry[0] - ax, entry[1] - ay)
    straight_out = math.hypot(bx - exit_[0], by - exit_[1])

    w = abs(rect.x2 - rect.x1)
    h = abs(rect.y2 - rect.y1)
    perimeter = 2 * (w + h)
    s1 = _perimeter_position(rect, *entry)
    s2 = _perimeter_position(rect, *exit_)
    delta = abs(s1 - s2)
    around = min(delta, perimeter - delta)
    return straight_in + around + straight_out


def best_slot_path(
    ax: float, ay: float, bx: float, by: float, slots: list[SlotRect]
) -> tuple[float, SlotRect] | None:
    """返回最长的绕槽路径（穿过多个槽时取最明显的一个）。"""
    best: tuple[float, SlotRect] | None = None
    for rect in slots:
        path = slot_path(ax, ay, bx, by, rect)
        if path is None:
            continue
        if best is None or path > best[0]:
            best = (path, rect)
    return best
