"""避距（clearance）计算：焊盘/走线两两比较，输出缺口距离。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from ..geometry import (
    boundary_distance,
    nearest_points,
    pad_polygon,
    polygon_bbox,
    polygon_distance,
    track_polygon,
    bbox_gap,
)
from ..model import (
    Board,
    ComponentViolation,
    NetClass,
    NetClassification,
    Pad,
    Track,
    Violation,
)
from .rules import ClearanceRules
from .slots import SlotRect, best_slot_path

TRACK_LABEL = "走线"

#: 缺口小于这个值（mm）不报违规——测量/圆弧离散带来的千分位误差，报了也没意义
MIN_DEFICIT_MM = 0.01


@dataclass
class ClearanceItem:
    net: str
    component: str
    label: str
    kind: str                      # pad / track
    polygon: list[tuple[float, float]]
    bbox: tuple[float, float, float, float]
    center: tuple[float, float]
    layers: set[str] = field(default_factory=set)

    @property
    def designator(self) -> str:
        return self.component or TRACK_LABEL


def _pad_layers(pad: Pad) -> set[str]:
    layer = (pad.layer or "TOP").upper()
    if layer.startswith("MULTI") or pad.hole > 0:
        return {"TOP", "BOTTOM"}
    if layer.startswith("BOTTOM"):
        return {"BOTTOM"}
    return {"TOP"}


def mount_polygon(mount, steps: int = 20) -> list[tuple[float, float]]:
    """安装焊盘的铜箔外形（近似为圆/矩形）。"""
    pad = Pad(
        name=mount.name,
        net="",
        component=mount.name,
        x=mount.x,
        y=mount.y,
        width=max(mount.width, 0.1),
        height=max(mount.height, 0.1),
        shape="round" if abs(mount.width - mount.height) < 0.2 else "rect",
    )
    return pad_polygon(pad)


def floating_polygon(part, steps: int = 20) -> list[tuple[float, float]]:
    """无网络器件（无焊盘）的外形：按给定长宽画一个矩形/圆。"""
    pad = Pad(
        name=part.designator,
        net="",
        component=part.designator,
        x=part.x,
        y=part.y,
        width=max(part.width or 0.1, 0.1),
        height=max(part.height or 0.1, 0.1),
        shape="rect",
    )
    return pad_polygon(pad)


def build_items(
    board: Board,
    include_tracks: bool = False,
    mounts: Iterable = (),
    floating_map: dict[str, str] | None = None,
    floating_parts: Iterable = (),
) -> list[ClearanceItem]:
    items: list[ClearanceItem] = []
    for pad in board.pads:
        if not pad.net:
            continue
        polygon = pad_polygon(pad)
        items.append(
            ClearanceItem(
                net=pad.net or (floating_map or {}).get(pad.component, ""),
                component=pad.component,
                label=f"{pad.component}.{pad.name}" if pad.component else f"焊盘{pad.name}",
                kind="pad",
                polygon=polygon,
                bbox=polygon_bbox(polygon),
                center=(pad.x, pad.y),
                layers=_pad_layers(pad),
            )
        )
    for mount in mounts:
        if not getattr(mount, "assigned", False):
            continue
        polygon = mount_polygon(mount)
        items.append(
            ClearanceItem(
                net=f"MNT:{mount.name}",
                component=mount.name,
                label=f"{mount.name}（安装焊盘）",
                kind="mount",
                polygon=polygon,
                bbox=polygon_bbox(polygon),
                center=(mount.x, mount.y),
                layers={"TOP", "BOTTOM"},
            )
        )
    for part in floating_parts:
        if not getattr(part, "assigned", False) or getattr(part, "pad_count", 0):
            continue
        if not getattr(part, "width", 0) or not getattr(part, "height", 0):
            continue  # 没有从桥接读到外形，无法参与距离计算
        polygon = floating_polygon(part)
        items.append(
            ClearanceItem(
                net=f"FLT:{part.designator}",
                component=part.designator,
                label=f"{part.designator}（无网络器件）",
                kind="part",
                polygon=polygon,
                bbox=polygon_bbox(polygon),
                center=(part.x, part.y),
                layers={"TOP", "BOTTOM"},
            )
        )
    if include_tracks:
        for track in board.tracks:
            if not track.net:
                continue
            polygon = track_polygon(track)
            items.append(
                ClearanceItem(
                    net=track.net,
                    component=track.component,
                    label=f"走线 {track.net}",
                    kind="track",
                    polygon=polygon,
                    bbox=polygon_bbox(polygon),
                    center=((track.x1 + track.x2) / 2, (track.y1 + track.y2) / 2),
                    layers={"BOTTOM"} if str(track.layer).upper().startswith("BOTTOM") else {"TOP"},
                )
            )
    return items


def _candidate_pairs(items: list[ClearanceItem], radius: float) -> set[tuple[int, int]]:
    """按包围盒栅格化，只返回可能小于 radius 的物体对。"""
    if not items:
        return set()
    cell = max(radius, 0.5)
    grid: dict[tuple[int, int], list[int]] = {}
    # 包围盒向外扩张 radius：间隙小于 radius 的两个物体必然落在同一格子里
    for index, item in enumerate(items):
        x0, y0, x1, y1 = item.bbox
        x0, y0, x1, y1 = x0 - cell, y0 - cell, x1 + cell, y1 + cell
        gx0, gy0 = int(math.floor(x0 / cell)), int(math.floor(y0 / cell))
        gx1, gy1 = int(math.floor(x1 / cell)), int(math.floor(y1 / cell))
        # 超大物体限制格子数量，避免 O(n^2) 爆炸
        if (gx1 - gx0 + 1) * (gy1 - gy0 + 1) > 4096:
            gx1, gy1 = gx0, gy0
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                grid.setdefault((gx, gy), []).append(index)

    pairs: set[tuple[int, int]] = set()
    for bucket in grid.values():
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            a = bucket[i]
            for j in range(i + 1, len(bucket)):
                b = bucket[j]
                pairs.add((a, b) if a < b else (b, a))
    return pairs


def find_violations(
    board: Board,
    classifications: dict[str, NetClassification],
    rules: ClearanceRules,
    include_tracks: bool | None = None,
    max_violations: int = 50000,
    mounts: Iterable = (),
    parts: Iterable = (),
) -> list[Violation]:
    """返回所有避距违规（按缺口距离从大到小排序）。

    ``mounts`` 中已指定电位的安装焊盘会作为额外检查对象参与避距计算。
    """
    tracks = rules.include_tracks if include_tracks is None else include_tracks
    mount_list = list(mounts or ())
    part_list = list(parts or ())
    floating_map = {
        part.designator: f"FLT:{part.designator}"
        for part in part_list
        if getattr(part, "assigned", False) and getattr(part, "designator", "")
    }
    items = build_items(
        board,
        include_tracks=tracks,
        mounts=mount_list,
        floating_map=floating_map,
        floating_parts=part_list,
    )
    if not items:
        return []

    merged: dict[str, NetClassification] = dict(classifications)
    for mount in mount_list:
        if not getattr(mount, "assigned", False):
            continue
        merged[f"MNT:{mount.name}"] = NetClassification(
            net=f"MNT:{mount.name}",
            net_class=mount.net_class,
            voltage=mount.voltage if mount.voltage is not None else mount.voltage_max,
            voltage_min=mount.voltage_min,
            voltage_max=mount.voltage_max if mount.voltage_max is not None else mount.voltage,
        )

    for part in part_list:
        if not getattr(part, "assigned", False):
            continue
        merged[f"FLT:{part.designator}"] = NetClassification(
            net=f"FLT:{part.designator}",
            net_class=part.net_class,
            voltage=part.voltage if part.voltage is not None else part.voltage_max,
            voltage_min=part.voltage_min,
            voltage_max=part.voltage_max if part.voltage_max is not None else part.voltage,
        )

    slots: list[SlotRect] = []
    for cutout in getattr(board, "cutouts", []) or []:
        if getattr(cutout, "is_circle", True):
            continue
        role = getattr(cutout, "role", "") or "creepage"
        if role != "creepage":
            continue  # 板框外形 / 器件封装挖空不参与爬电判定
        x1, y1, x2, y2 = cutout.bbox
        slots.append(SlotRect(x1, y1, x2, y2))

    max_gap = max(
        [
            rules.isolation_gap_mm,
            rules.component_gap_mm if rules.component_gap_enabled else 0.0,
            *[float(v) for v in rules.matrix.values()],
            0.5,
        ]
    )
    if rules.voltage_step_enabled and rules.voltage_step_mm > 0:
        max_gap = max(max_gap, rules.voltage_step_mm * 4)

    def net_class(net: str) -> NetClass:
        item = merged.get(net)
        return item.net_class if item else NetClass.UNKNOWN

    def net_voltage(net: str) -> float:
        item = merged.get(net)
        return item.peak_voltage() if item else 0.0

    violations: list[Violation] = []
    for i, j in _candidate_pairs(items, max_gap):
        a, b = items[i], items[j]
        if a.layers and b.layers and not (a.layers & b.layers):
            continue  # 不同层不构成间距问题
        same_component = bool(a.component) and a.component == b.component
        same_net = a.net == b.net
        # 不同器件之间始终按「器件间最小间距」检查（与网络无关）
        floor_applies = rules.component_gap_enabled and not same_component
        if same_net and not floor_applies:
            continue
        class_a, class_b = net_class(a.net), net_class(b.net)
        if not rules.check_unknown and NetClass.UNKNOWN in (class_a, class_b):
            continue
        if rules.cross_class_only and class_a is class_b and not floor_applies:
            continue
        # 同一器件内部焊盘默认不参与器件间避距，除非是原边↔副边的隔离检查
        if (not rules.check_same_component and same_component
                and not rules.is_isolation_pair(class_a, class_b)):
            continue

        required, rule_name = rules.required(
            class_a, class_b, net_voltage(a.net), net_voltage(b.net)
        )
        if floor_applies and rules.component_gap_mm > required + 1e-9:
            required = rules.component_gap_mm
            rule_name = f"器件间最小间距 {rules.component_gap_mm:g} mm（与网络无关）"
        if bbox_gap(a.bbox, b.bbox) > required:
            continue

        distance = polygon_distance(a.polygon, b.polygon)
        if distance >= required - 1e-6:
            continue

        point_a, point_b = nearest_points(a.polygon, b.polygon)

        # 原副边之间被避距槽隔开时：绕槽沿面足够长 → 按电气间隙判（6 mm）
        if slots and rules.is_isolation_pair(class_a, class_b):
            bridge = best_slot_path(
                point_a[0], point_a[1], point_b[0], point_b[1], slots
            )
            if bridge is not None:
                creepage, _rect = bridge
                if creepage >= rules.isolation_gap_mm - 1e-9:
                    required, rule_name = rules.required(
                        class_a,
                        class_b,
                        net_voltage(a.net),
                        net_voltage(b.net),
                        slot_bridge=True,
                    )
                    if floor_applies and rules.component_gap_mm > required + 1e-9:
                        required = rules.component_gap_mm
                else:
                    required = max(required, rules.isolation_gap_mm)
                    rule_name = (
                        f"隔离带爬电距离 {rules.isolation_gap_mm:g} mm"
                        f"（跨槽沿面仅 {creepage:.1f} mm）"
                    )
                if distance >= required - 1e-6:
                    continue

        # 缺口过小（< 0.01 mm）不报，避免出现 0.00x mm 的噪声行
        if required - distance < MIN_DEFICIT_MM:
            continue
        # 安装焊盘的伪网络名不带到界面里，直接显示 MH1 这样的名称
        def _display(net: str) -> str:
            for prefix in ("MNT:", "FLT:"):
                if net.startswith(prefix):
                    return net[len(prefix):]
            return net

        display_a = _display(a.net)
        display_b = _display(b.net)
        violations.append(
            Violation(
                net_a=display_a,
                net_b=display_b,
                item_a=a.designator,
                item_b=b.designator,
                pad_a=a.label,
                pad_b=b.label,
                class_a=class_a,
                class_b=class_b,
                x=(point_a[0] + point_b[0]) / 2,
                y=(point_a[1] + point_b[1]) / 2,
                actual_mm=round(distance, 3),
                required_mm=round(required, 3),
                rule_name=rule_name,
                ax=point_a[0],
                ay=point_a[1],
                bx=point_b[0],
                by=point_b[1],
                severity="warning" if same_component else "error",
            )
        )
        if len(violations) >= max_violations:
            break

    # 器件焊盘到板框外形的最小距离
    outline = list(getattr(board, "outline", []) or [])
    if rules.board_edge_enabled and rules.board_edge_gap_mm > 0 and len(outline) >= 3:
        limit = rules.board_edge_gap_mm
        for item in items:
            if item.kind != "pad":
                continue
            # 烧录端子（半孔）本身就压在板边，不判板框间距
            component = board.components.get(item.component)
            if component is not None and getattr(component, "is_burn_terminal", False):
                continue
            distance = boundary_distance(item.polygon, outline)
            if limit - distance < MIN_DEFICIT_MM:
                continue
            point_pad, point_edge = nearest_points(item.polygon, outline)
            class_a = net_class(item.net)
            violations.append(
                Violation(
                    net_a=item.net[4:] if item.net.startswith("MNT:") else item.net,
                    net_b="板框",
                    item_a=item.designator,
                    item_b="板框",
                    pad_a=item.label,
                    pad_b="",
                    class_a=class_a,
                    class_b=NetClass.UNKNOWN,
                    x=(point_pad[0] + point_edge[0]) / 2,
                    y=(point_pad[1] + point_edge[1]) / 2,
                    actual_mm=round(distance, 3),
                    required_mm=round(limit, 3),
                    rule_name=f"焊盘距板框 {limit:g} mm",
                    ax=point_pad[0],
                    ay=point_pad[1],
                    bx=point_edge[0],
                    by=point_edge[1],
                    severity="error",
                )
            )

    # 器件 3D 本体之间的干涉 / 最小间距
    if rules.body_gap_enabled and rules.body_gap_mm > 0:
        limit = rules.body_gap_mm
        def side(comp) -> str:
            layer = str(getattr(comp, "layer", "") or "TOP").upper()
            return "BOTTOM" if "BOTTOM" in layer else "TOP"

        def pad_polys(comp):
            """器件每个焊盘的真实铜箔多边形。

            之前用 max(宽,高) 当边长拼正方形，长条形焊盘会被放大成方块，
            0.46 mm 的真实间隙会被算成 0.09 mm，误判成“本体几乎重叠”。
            """
            return [pad_polygon(pad) for pad in board.pads_of_component(comp.designator)]

        bodies = []
        for designator, comp in board.components.items():
            pad_list = pad_polys(comp)
            body = getattr(comp, "body_bbox", None)  # 3D 本体（机械层投影）
            box = body or getattr(comp, "bbox", None)
            if not box and pad_list:
                xs = [point[0] for poly in pad_list for point in poly]
                ys = [point[1] for poly in pad_list for point in poly]
                box = (min(xs), min(ys), max(xs), max(ys))
            if box:
                bodies.append((designator, comp, box, pad_list, body))
        for index, (name_a, comp_a, box_a, pads_a, body_a) in enumerate(bodies):
            for name_b, comp_b, box_b, pads_b, body_b in bodies[index + 1:]:
                # 正反面的器件在 XY 上重叠是正常的，不判干涉
                if side(comp_a) != side(comp_b):
                    continue
                if bbox_gap(box_a, box_b) > limit + 0.5:
                    continue  # 粗筛，省掉大量逐焊盘计算
                if body_a and body_b:
                    # 两个器件都有 3D 本体 → 直接按本体投影外框判
                    gap = bbox_gap(body_a, body_b)
                elif body_a or body_b:
                    # 只有一方有 3D 本体：拿它跟另一方的占位外框（bbox）比，
                    # 否则会拿焊盘去比，像 U9 与 C106 这种贴着的会被漏掉。
                    gap = bbox_gap(body_a or box_a, body_b or box_b)
                elif pads_a and pads_b:
                    gap = min(
                        polygon_distance(poly_a, poly_b)
                        for poly_a in pads_a
                        for poly_b in pads_b
                    )
                else:
                    gap = bbox_gap(box_a, box_b)
                if gap >= limit - MIN_DEFICIT_MM:
                    continue
                x = (max(box_a[0], box_b[0]) + min(box_a[2], box_b[2])) / 2
                y = (max(box_a[1], box_b[1]) + min(box_a[3], box_b[3])) / 2
                violations.append(
                    Violation(
                        net_a="",
                        net_b="",
                        item_a=name_a,
                        item_b=name_b,
                        pad_a=f"{name_a}（本体）",
                        pad_b=f"{name_b}（本体）",
                        class_a=NetClass.UNKNOWN,
                        class_b=NetClass.UNKNOWN,
                        x=x,
                        y=y,
                        actual_mm=round(gap, 3),
                        required_mm=round(limit, 3),
                        rule_name=f"器件本体最小间距 {limit:g} mm",
                        ax=(box_a[0] + box_a[2]) / 2,
                        ay=(box_a[1] + box_a[3]) / 2,
                        bx=(box_b[0] + box_b[2]) / 2,
                        by=(box_b[1] + box_b[3]) / 2,
                        severity="error",
                    )
                )

    violations.sort(key=lambda v: v.deficit_mm, reverse=True)
    return violations


def aggregate_by_component(violations: Iterable[Violation]) -> list[ComponentViolation]:
    """把焊盘级违规汇总成器件对级别，保留每对最严重的一条。"""
    grouped: dict[tuple[str, str], ComponentViolation] = {}
    counts: dict[tuple[str, str], int] = {}

    for violation in violations:
        a = violation.item_a or TRACK_LABEL
        b = violation.item_b or TRACK_LABEL
        key = (a, b) if a <= b else (b, a)
        counts[key] = counts.get(key, 0) + 1
        current = grouped.get(key)
        if current is None or violation.deficit_mm > current.deficit_mm:
            grouped[key] = ComponentViolation(
                designator_a=a,
                designator_b=b,
                net_a=violation.net_a,
                net_b=violation.net_b,
                class_a=violation.class_a,
                class_b=violation.class_b,
                actual_mm=violation.actual_mm,
                required_mm=violation.required_mm,
                x=violation.x,
                y=violation.y,
                rule_name=violation.rule_name,
                severity=violation.severity,
            )

    for key, item in grouped.items():
        item.count = counts.get(key, 1)
    return sorted(grouped.values(), key=lambda v: v.deficit_mm, reverse=True)


def near_miss_nets(
    violations: Iterable[Violation],
    classifications: dict[str, NetClassification],
    ratio: float = 1.35,
) -> list[str]:
    """挑出需要精确核对焊盘外形的网络（间隙接近要求的）。"""
    nets: set[str] = set()
    for violation in violations:
        if violation.actual_mm <= 0:
            nets.update({violation.net_a, violation.net_b})
            continue
        if violation.required_mm / max(violation.actual_mm, 1e-6) <= ratio:
            nets.update({violation.net_a, violation.net_b})
    return sorted(n for n in nets if n in classifications)
