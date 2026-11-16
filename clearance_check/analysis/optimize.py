"""矩形选区内的布局优化：为违规器件寻找移动量最小的可行位置。

策略（贪心）：
1. 收集选区内的违规所涉及的器件（跨器件 + 器件-板框两类）；
2. 对每个器件按“位移从小到大”枚举候选偏移（含 8 个方向），
   用局部间距检查判断该器件搬到新位置后是否仍与邻居/板框冲突；
3. 第一个通过检查的偏移即为该器件的最小移动量解；全部候选都冲突则保持不动；
4. 依次处理（后一个器件看到的是前面器件已经移动后的布局）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..geometry import (
    boundary_distance,
    bbox_gap,
    pad_polygon,
    point_in_polygon_any,
    polygon_bbox,
    polygon_distance,
)
from ..model import Board, NetClass, NetClassification
from .rules import ClearanceRules

EPS = 1e-6


@dataclass
class MovePlan:
    """单个器件的优化结果。"""

    designator: str
    x0: float
    y0: float
    rot0: float
    x1: float
    y1: float
    rot1: float
    moved: bool = False
    reason: str = ""

    @property
    def shift_mm(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)

    @property
    def rows(self) -> tuple[str, str, str, str, str]:
        return (
            self.designator,
            f"{self.x0:.2f}, {self.y0:.2f}",
            f"{self.rot0:.0f}°",
            f"{self.x1:.2f}, {self.y1:.2f}",
            f"{self.rot1:.0f}°",
        )


@dataclass
class _Item:
    component: str
    net: str
    polygon: list[tuple[float, float]]
    bbox: tuple[float, float, float, float]
    is_pad: bool = True


def _items_of(board: Board, mounts, parts) -> list[_Item]:
    items: list[_Item] = []
    for pad in board.pads:
        if not pad.net:
            continue
        polygon = pad_polygon(pad)
        items.append(
            _Item(
                component=pad.component,
                net=pad.net,
                polygon=polygon,
                bbox=polygon_bbox(polygon),
            )
        )
    for mount in mounts or []:
        if not getattr(mount, "assigned", False):
            continue
        # 安装孔用外接圆近似
        radius = max(mount.width, mount.height) / 2.0
        polygon = [
            (mount.x + radius * math.cos(math.radians(angle)),
             mount.y + radius * math.sin(math.radians(angle)))
            for angle in range(0, 360, 30)
        ]
        items.append(
            _Item(
                component=mount.name,
                net=f"MNT:{mount.name}",
                polygon=polygon,
                bbox=polygon_bbox(polygon),
                is_pad=False,
            )
        )
    for part in parts or []:
        w, h = getattr(part, "width", 0.0), getattr(part, "height", 0.0)
        if not w or not h:
            continue
        polygon = [
            (part.x - w / 2, part.y - h / 2),
            (part.x + w / 2, part.y - h / 2),
            (part.x + w / 2, part.y + h / 2),
            (part.x - w / 2, part.y + h / 2),
        ]
        items.append(
            _Item(
                component=part.designator,
                net=f"FLT:{part.designator}",
                polygon=polygon,
                bbox=polygon_bbox(polygon),
                is_pad=False,
            )
        )
    return items


def _class_of(net: str, classifications: dict[str, NetClassification]) -> NetClass:
    if net.startswith("MNT:"):
        name = net[4:]
        for item in classifications.values():
            if item.net == name:
                return item.net_class
        return NetClass.SECONDARY
    if net.startswith("FLT:"):
        name = net[3:]
        for item in classifications.values():
            if item.net == name:
                return item.net_class
        return NetClass.SECONDARY
    item = classifications.get(net)
    return item.net_class if item else NetClass.UNKNOWN


def _voltage_of(net: str, classifications: dict[str, NetClassification]) -> float:
    name = net
    if net.startswith(("MNT:", "FLT:")):
        name = net.split(":", 1)[1]
        for item in classifications.values():
            if item.net == name:
                return float(item.voltage or item.voltage_max or 0.0)
        return 0.0
    item = classifications.get(name)
    if not item:
        return 0.0
    return float(item.voltage or item.voltage_max or 0.0)


def _shift(item: _Item, dx: float, dy: float) -> _Item:
    polygon = [(x + dx, y + dy) for x, y in item.polygon]
    return _Item(item.component, item.net, polygon, polygon_bbox(polygon), item.is_pad)


def _inside_board(polygon, outline) -> bool:
    """多边形是否整体落在板框内（顶点向自身中心收缩 2% 以避开边界歧义）。"""
    if len(outline) < 3:
        return True
    if not polygon:
        return True
    cx = sum(point[0] for point in polygon) / len(polygon)
    cy = sum(point[1] for point in polygon) / len(polygon)
    for x, y in polygon:
        probe = (x + (cx - x) * 0.02, y + (cy - y) * 0.02)
        if not point_in_polygon_any(probe, outline):
            return False
    return True


def _body_polygon(comp) -> list[tuple[float, float]] | None:
    """器件本体外形（桥接读到的 bbox），没有就返回 None。"""
    box = getattr(comp, "bbox", None)
    if not box:
        return None
    x1, y1, x2, y2 = box
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def _shift_polygon(polygon, dx: float, dy: float):
    if not polygon:
        return None
    return [(x + dx, y + dy) for x, y in polygon]


def _conflicts(
    moving: list[_Item],
    others: list[_Item],
    board: Board,
    classifications: dict[str, NetClassification],
    rules: ClearanceRules,
    burn_terminal: bool,
    body: list[tuple[float, float]] | None = None,
) -> bool:
    """移动后的器件是否仍存在间距冲突（含出板框）。"""
    if body and not _inside_board(body, board.outline):
        return True
    for item in moving:
        # 任何器件都必须完整留在板框内——烧录端子同样适用
        if not _inside_board(item.polygon, board.outline):
            return True
    for item in moving:
        for other in others:
            if other.component == item.component:
                continue
            gap_allow = max(rules.isolation_gap_mm, rules.component_gap_mm) + 1.0
            if bbox_gap(item.bbox, other.bbox) > gap_allow:
                continue
            required, _rule = rules.required(
                _class_of(item.net, classifications),
                _class_of(other.net, classifications),
                _voltage_of(item.net, classifications),
                _voltage_of(other.net, classifications),
            )
            if rules.component_gap_enabled:
                required = max(required, rules.component_gap_mm)
            if polygon_distance(item.polygon, other.polygon) < required - EPS:
                return True

    if not burn_terminal and rules.board_edge_enabled and len(board.outline) >= 3:
        edge_gap = rules.board_edge_gap_mm
        for item in moving:
            # 只比较大小，传 limit 让几何层能提前判定"离板边足够远"
            if boundary_distance(item.polygon, board.outline, limit=edge_gap) < edge_gap - EPS:
                return True
    return False


def _candidates(step: float, max_shift: float):
    """按位移从小到大给出候选偏移（含 8 个方向）。"""
    result = []
    distance = step
    while distance <= max_shift + EPS:
        for index in range(8):
            angle = math.radians(45 * index)
            result.append((distance * math.cos(angle), distance * math.sin(angle)))
        distance += step
    result.sort(key=lambda pair: pair[0] ** 2 + pair[1] ** 2)
    return result


def optimize_area(
    board: Board,
    classifications: dict[str, NetClassification],
    rules: ClearanceRules,
    rect: tuple[float, float, float, float],
    mounts=(),
    parts=(),
    step: float = 0.1,
    max_shift: float = 1.0,
    max_components: int = 12,
) -> list[MovePlan]:
    """在矩形选区 (x0, y0, x1, y1)（mm）内做最小移动量优化。

    默认位移上限 1 mm（`max_shift`），步长 0.1 mm；找不到既不出框又不违规的
    位置就保持不动。
    """
    x0, y0, x1, y1 = rect
    items = _items_of(board, mounts, parts)

    def inside(item: _Item) -> bool:
        cx = (item.bbox[0] + item.bbox[2]) / 2
        cy = (item.bbox[1] + item.bbox[3]) / 2
        return x0 <= cx <= x1 and y0 <= cy <= y1

    candidates: list[str] = []
    for component in board.components:
        if not board.pads_of_component(component):
            continue
        component_items = [item for item in items if item.component == component]
        if any(inside(item) for item in component_items):
            candidates.append(component)
    candidates = candidates[:max_components]

    plans: list[MovePlan] = []
    steps = _candidates(step, max_shift)
    for component in candidates:
        comp = board.components[component]
        moving = [item for item in items if item.component == component]
        others = [item for item in items if item.component != component]
        burn = getattr(comp, "is_burn_terminal", False)
        body = _body_polygon(comp)

        if not _conflicts(moving, others, board, classifications, rules, burn, body):
            plans.append(MovePlan(component, comp.x, comp.y, comp.rotation,
                                  comp.x, comp.y, comp.rotation, False, "选区外已无违规"))
            continue

        chosen = None
        for dx, dy in steps:
            if not _conflicts(
                [_shift(item, dx, dy) for item in moving],
                others, board, classifications, rules, burn,
                _shift_polygon(body, dx, dy),
            ):
                chosen = (dx, dy)
                break

        if chosen is None:
            plans.append(MovePlan(component, comp.x, comp.y, comp.rotation,
                                  comp.x, comp.y, comp.rotation, False,
                                  f"{max_shift:g} mm 内无可行解，保持不动"))
            continue

        dx, dy = chosen
        pads = board.pads_of_component(component)
        for pad in pads:
            pad.x += dx
            pad.y += dy
        comp.x += dx
        comp.y += dy
        items = _items_of(board, mounts, parts)
        plans.append(MovePlan(
            component, comp.x - dx, comp.y - dy, comp.rotation,
            comp.x, comp.y, comp.rotation, True,
            f"移动 {math.hypot(dx, dy):.2f} mm",
        ))
    return plans
