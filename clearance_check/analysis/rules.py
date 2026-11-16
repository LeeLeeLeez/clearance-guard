"""间距规则模型：类别矩阵 + 隔离带 + 电压差增量规则。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..model import NET_CLASS_SHORT_LABELS, NetClass, ORDERED_NET_CLASSES


def _key(a: NetClass, b: NetClass) -> tuple[NetClass, NetClass]:
    order = {net_class: i for i, net_class in enumerate(ORDERED_NET_CLASSES)}
    # 「未定」已不作为可选项，但旧规则文件里可能还有，排序时排到最后
    rank = order.get(a, len(order))
    other = order.get(b, len(order))
    return (a, b) if rank <= other else (b, a)


def default_matrix() -> dict[tuple[NetClass, NetClass], float]:
    pc, ps = NetClass.PRIMARY_CONST, NetClass.PRIMARY_SWITCHING
    sec, unk = NetClass.SECONDARY, NetClass.UNKNOWN
    matrix: dict[tuple[NetClass, NetClass], float] = {
        _key(pc, pc): 0.2,
        _key(pc, ps): 0.5,
        _key(ps, ps): 0.5,
        _key(sec, sec): 0.2,
        _key(pc, sec): 8.0,
        _key(ps, sec): 8.0,
        _key(unk, pc): 0.5,
        _key(unk, ps): 0.5,
        _key(unk, sec): 8.0,
        _key(unk, unk): 0.5,
    }
    return matrix


@dataclass
class ClearanceRules:
    """用户可编辑的间距规则。"""

    matrix: dict[tuple[NetClass, NetClass], float] = field(default_factory=default_matrix)
    #: 隔离带爬电距离（沿面，默认 8 mm）——没有避距槽时的要求
    isolation_gap_mm: float = 8.0
    #: 隔离带电气间隙（跨避距槽时的直线空气距离，默认 6 mm）
    isolation_clearance_mm: float = 6.0
    voltage_step_enabled: bool = True
    voltage_step_v: float = 100.0
    voltage_step_mm: float = 0.5
    check_unknown: bool = True
    include_tracks: bool = False
    cross_class_only: bool = False
    check_same_component: bool = False
    #: 器件焊盘到板框外形的最小距离（mm），0 表示不检查
    board_edge_gap_mm: float = 0.4
    board_edge_enabled: bool = True
    #: 不同器件焊盘之间的最小间距（与网络、电压无关），0 表示不检查
    component_gap_mm: float = 0.3
    component_gap_enabled: bool = True
    #: 器件 3D 本体之间的最小间距（按桥接读到的本体外形投影判定）
    body_gap_mm: float = 0.3
    body_gap_enabled: bool = True

    # ---- 查询 -----------------------------------------------------
    def base_gap(self, a: NetClass, b: NetClass) -> float:
        return float(self.matrix.get(_key(a, b), 1.0))

    def is_isolation_pair(self, a: NetClass, b: NetClass) -> bool:
        pair = {a, b}
        has_primary = bool(pair & {NetClass.PRIMARY_CONST, NetClass.PRIMARY_SWITCHING})
        return has_primary and NetClass.SECONDARY in pair

    def required(
        self,
        a: NetClass,
        b: NetClass,
        va: float = 0.0,
        vb: float = 0.0,
        slot_bridge: bool = False,
    ) -> tuple[float, str]:
        """返回 (最小间距 mm, 起决定作用的规则名)。

        ``slot_bridge=True``：两点之间被避距槽隔开且绕槽沿面长度已达标，
        隔离带按**电气间隙**（直线）判；否则按**爬电距离**（沿面）判。
        """
        gap = self.base_gap(a, b)
        rule = (
            f"类别矩阵：{NET_CLASS_SHORT_LABELS[a]} / {NET_CLASS_SHORT_LABELS[b]}"
        )

        if self.is_isolation_pair(a, b):
            # 原副边只认专用的隔离规则：没有槽时按爬电（8 mm），
            # 被避距槽隔开且绕槽沿面达标时按电气间隙（6 mm）。
            gap = (
                self.isolation_clearance_mm if slot_bridge else self.isolation_gap_mm
            )
            rule = (
                f"隔离带电气间隙（跨避距槽）{gap:g} mm"
                if slot_bridge
                else f"隔离带爬电距离（沿面）{gap:g} mm"
            )

        if self.voltage_step_enabled and self.voltage_step_v > 0:
            delta = abs((va or 0.0) - (vb or 0.0))
            steps = int(delta // self.voltage_step_v)
            extra = steps * self.voltage_step_mm
            if extra > gap + 1e-9:
                gap = extra
                rule = (
                    f"电压差 {delta:.0f} V"
                    f"（每 {self.voltage_step_v:g} V +{self.voltage_step_mm:g} mm）"
                )

        return round(gap, 3), rule

    # ---- 存取 -----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "matrix": {
                f"{a.value}|{b.value}": round(float(value), 3)
                for (a, b), value in self.matrix.items()
            },
            "isolation_gap_mm": self.isolation_gap_mm,
            "isolation_clearance_mm": self.isolation_clearance_mm,
            "voltage_step_enabled": self.voltage_step_enabled,
            "voltage_step_v": self.voltage_step_v,
            "voltage_step_mm": self.voltage_step_mm,
            "check_unknown": self.check_unknown,
            "include_tracks": self.include_tracks,
            "cross_class_only": self.cross_class_only,
            "check_same_component": self.check_same_component,
            "board_edge_gap_mm": self.board_edge_gap_mm,
            "board_edge_enabled": self.board_edge_enabled,
            "component_gap_mm": self.component_gap_mm,
            "body_gap_mm": self.body_gap_mm,
            "body_gap_enabled": self.body_gap_enabled,
            "component_gap_enabled": self.component_gap_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ClearanceRules":
        rules = cls()
        matrix: dict[tuple[NetClass, NetClass], float] = {}
        for key, value in (data.get("matrix") or {}).items():
            try:
                left, right = key.split("|")
                matrix[(NetClass(left), NetClass(right))] = float(value)
            except (ValueError, KeyError):
                continue
        if matrix:
            rules.matrix = matrix
        rules.isolation_gap_mm = float(data.get("isolation_gap_mm", rules.isolation_gap_mm))
        rules.isolation_clearance_mm = float(
            data.get("isolation_clearance_mm", rules.isolation_clearance_mm)
        )
        rules.voltage_step_enabled = bool(data.get("voltage_step_enabled", rules.voltage_step_enabled))
        rules.voltage_step_v = float(data.get("voltage_step_v", rules.voltage_step_v))
        rules.voltage_step_mm = float(data.get("voltage_step_mm", rules.voltage_step_mm))
        rules.check_unknown = bool(data.get("check_unknown", rules.check_unknown))
        rules.include_tracks = bool(data.get("include_tracks", rules.include_tracks))
        rules.cross_class_only = bool(data.get("cross_class_only", rules.cross_class_only))
        rules.check_same_component = bool(data.get("check_same_component", rules.check_same_component))
        rules.board_edge_gap_mm = float(
            data.get("board_edge_gap_mm", rules.board_edge_gap_mm)
        )
        rules.board_edge_enabled = bool(
            data.get("board_edge_enabled", rules.board_edge_enabled)
        )
        rules.component_gap_mm = float(data.get("component_gap_mm", rules.component_gap_mm))
        rules.body_gap_mm = float(data.get("body_gap_mm", rules.body_gap_mm))
        rules.body_gap_enabled = bool(data.get("body_gap_enabled", rules.body_gap_enabled))
        rules.component_gap_enabled = bool(
            data.get("component_gap_enabled", rules.component_gap_enabled)
        )
        return rules

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "ClearanceRules":
        from ..plainfile import read_text

        data = json.loads(read_text(path))
        return cls.from_dict(data)


def default_rules() -> ClearanceRules:
    return ClearanceRules()
