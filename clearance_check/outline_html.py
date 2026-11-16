"""从 HTML 记录里读取板框外形（替代 eda-agent 的板框接口）。

HTML 里通常是下面这种 SVG 路径（单位 mm，line + arc）：

    <path d="M 0.00 0.00 L 239.50 0.00 A 0.498 0.498 0 0 1 240.00 0.50 ... Z">

这里把它解析成闭合多边形：L/H/V 直接取点，A 按 SVG 椭圆弧参数离散成折线。
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from .plainfile import read_text

_CMD = re.compile(r"([MLHVAZmlhvaz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")


def _numbers(tokens: list[str]) -> list[float]:
    return [float(token) for token in tokens]


def _arc_points(
    x0: float,
    y0: float,
    rx: float,
    ry: float,
    large_arc: bool,
    sweep: bool,
    x1: float,
    y1: float,
    step_deg: float,
    phi_deg: float = 0.0,
) -> list[tuple[float, float]]:
    """SVG 端点参数圆弧 → 折线（按 SVG 规范 F.6.5 的端点转圆心公式）。

    之前那版在算完圆心后又“事后修正” delta，遇到大半径圆角（R8）会多绕半个圆，
    看起来像是板框上鼓出一个大圆弧。这里严格按规范计算。
    """
    rx, ry = abs(rx), abs(ry)
    if rx <= 1e-9 or ry <= 1e-9:
        return [(x1, y1)]

    phi = math.radians(phi_deg)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    dx2, dy2 = (x0 - x1) / 2.0, (y0 - y1) / 2.0
    x1p = cos_phi * dx2 + sin_phi * dy2
    y1p = -sin_phi * dx2 + cos_phi * dy2

    # 半径不足时按规范等比放大
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale

    numerator = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    if denominator <= 1e-12:
        return [(x1, y1)]
    factor = math.sqrt(max(0.0, numerator / denominator))
    if large_arc == sweep:
        factor = -factor

    cxp = factor * rx * y1p / ry
    cyp = -factor * ry * x1p / rx
    cx = cos_phi * cxp - sin_phi * cyp + (x0 + x1) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y0 + y1) / 2.0

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    theta = angle(1.0, 0.0, ux, uy)
    delta = angle(ux, uy, vx, vy)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi

    steps = max(1, int(round(abs(math.degrees(delta)) / max(step_deg, 0.5))))
    points = []
    for index in range(1, steps + 1):
        current = theta + delta * index / steps
        ex = cx + rx * math.cos(current) * cos_phi - ry * math.sin(current) * sin_phi
        ey = cy + rx * math.cos(current) * sin_phi + ry * math.sin(current) * cos_phi
        points.append((ex, ey))
    return points


def parse_svg_path(data: str, arc_step_deg: float = 6.0) -> list[tuple[float, float]]:
    """解析 SVG path 的 d 属性，返回闭合折线（mm）。"""
    tokens = [match.group(0) for match in _CMD.finditer(data)]
    points: list[tuple[float, float]] = []
    x = y = 0.0
    start_x = start_y = 0.0
    command = ""
    index = 0

    def take(count: int) -> list[float] | None:
        nonlocal index
        chunk = tokens[index:index + count]
        if len(chunk) < count or any(token.isalpha() for token in chunk):
            return None
        index += count
        return [float(token) for token in chunk]

    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command in "Zz":
                if points and (abs(points[0][0] - x) > 1e-9 or abs(points[0][1] - y) > 1e-9):
                    points.append((start_x, start_y))
                    x, y = start_x, start_y
            continue

        upper = command.upper()
        relative = command.islower()

        if upper in ("M", "L"):
            values = take(2)
            if values is None:
                break
            nx, ny = values
            if relative:
                nx, ny = x + nx, y + ny
            if upper == "M":
                start_x, start_y = nx, ny
                command = "l" if relative else "L"  # 后续坐标按 L 处理
            x, y = nx, ny
            points.append((x, y))
            continue

        if upper in ("H", "V"):
            values = take(1)
            if values is None:
                break
            value = values[0]
            if upper == "H":
                x = x + value if relative else value
            else:
                y = y + value if relative else value
            points.append((x, y))
            continue

        if upper == "A":
            values = take(7)
            if values is None:
                break
            rx, ry, rot, large, sweep, nx, ny = values
            if relative:
                nx, ny = x + nx, y + ny
            points.extend(
                _arc_points(x, y, rx, ry, bool(round(large)), bool(round(sweep)),
                            nx, ny, arc_step_deg, rot)
            )
            x, y = nx, ny
            continue

        index += 1  # 未知 token，跳过避免死循环

    return points


def _bbox(points) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(a, b) -> float:
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    return max(0.0, right - left) * max(0.0, bottom - top)


def _translate_to(points, target_bbox) -> list[tuple[float, float]]:
    """把多边形平移到目标外框的中心。HTML 的坐标是从 0,0 起画的，
    与 Altium 的绝对坐标差一个整体偏移，这里按外框中心对齐。"""
    box = _bbox(points)
    dx = (target_bbox[0] + target_bbox[2]) / 2 - (box[0] + box[2]) / 2
    dy = (target_bbox[1] + target_bbox[3]) / 2 - (box[1] + box[3]) / 2
    return [(x + dx, y + dy) for x, y in points]


def _score(points, pad_points) -> float:
    """落在外形内的焊盘比例（没有焊盘样本就退回 0）。"""
    from .geometry import point_in_polygon_any

    if not pad_points:
        return 0.0
    inside = sum(1 for x, y in pad_points if point_in_polygon_any((x, y), points))
    return inside / len(pad_points)


def orient_to_pads(
    outline: list[tuple[float, float]],
    pad_bbox: tuple[float, float, float, float] | None,
    pad_points: list[tuple[float, float]] | None = None,
) -> list[tuple[float, float]]:
    """把 HTML 板框摆到与 PCB 焊盘一致的位置与朝向。

    1. HTML 里 y 轴朝下、Altium 朝上 → 需要时上下翻转；
    2. HTML 坐标从 0,0 起画 → 平移到焊盘外框中心；
    3. 用「能罩住多少焊盘」来挑朝向，比单纯比外框重合度可靠。
    """
    if not outline or not pad_bbox:
        return outline

    box = _bbox(outline)
    flipped = [(x, box[1] + box[3] - y) for x, y in outline]
    candidates = [_translate_to(outline, pad_bbox), _translate_to(flipped, pad_bbox)]

    if pad_points:
        scored = [(_score(points, pad_points), points) for points in candidates]
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    overlaps = [(_overlap(_bbox(points), pad_bbox), points) for points in candidates]
    overlaps.sort(key=lambda item: item[0], reverse=True)
    return overlaps[0][1]


def load_outline_html(
    path: str | Path,
    pad_bbox: tuple[float, float, float, float] | None = None,
    pad_points: list[tuple[float, float]] | None = None,
    arc_step_deg: float = 6.0,
) -> list[tuple[float, float]]:
    """读取 HTML 文件里的第一条 SVG 路径，返回板框多边形（mm）。"""
    # 打包成 exe 后 DLP 会把文件读成密文（%TSD 头），走兼容层拿明文
    text = read_text(path).replace("\r\n", "\n")
    candidates = re.findall(r"<path[^>]*\sd=\"([^\"]+)\"", text)
    if not candidates:
        raise ValueError(
            "HTML 里没有找到 <path d=\"…\"> 板框路径"
            f"（文件 {len(text)} 字符，开头：{text[:40]!r}）"
        )

    # HTML 里还有网格图案之类的小路径，取包围盒最大的那条当板框
    best: list[tuple[float, float]] = []
    best_area = -1.0
    for data in candidates:
        parsed = parse_svg_path(data, arc_step_deg)
        if len(parsed) < 3:
            continue
        box = _bbox(parsed)
        area = (box[2] - box[0]) * (box[3] - box[1])
        if area > best_area:
            best, best_area = parsed, area
    if len(best) < 3:
        raise ValueError("板框路径解析出的点不足 3 个")
    points = best
    if points[0] == points[-1]:
        points = points[:-1]
    return orient_to_pads(points, pad_bbox, pad_points)
