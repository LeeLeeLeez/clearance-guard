"""根据网络名与网络拓扑自动判定电压类别。

判定三类信息：
  1. 位于隔离变压器/光耦的哪一侧（原边 / 副边）；
  2. 网络电压（名称中的显式电压，或经无源器件继承，或同侧母线电压）；
  3. 该网络是否为跳变电平（开关节点），并给出电压跳变区间。

所有推断都会写入 ``reasons``，置信度低的网络会被标记为"未定"，等待人工确认。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..model import (
    Board,
    NetClass,
    NetClassification,
    is_auto_named,
    default_primary_ground,
    default_secondary_ground,
)

# --------------------------------------------------------------------------
# 词表
# --------------------------------------------------------------------------
CLASSIFIER_VERSION = 2

GROUND_HINTS = ("GND", "VSS", "0V", "EARTH", "PE", "AGND", "DGND", "PGND", "SGND", "MGND")

PRIMARY_HINTS = ("PRI", "HV", "HVDC", "PFC", "BULK", "DCLINK", "DC_LINK", "PRIMARY")
SECONDARY_HINTS = ("SEC", "ISO", "SECOND")

SWITCHING_HINTS = ("SW", "LX", "PHASE", "SWNODE", "DRAIN", "BOOT", "HB", "HS", "NODE")
GATE_HINTS = ("GATE", "DRV", "DRIVE", "GS", "GD")

TRANSFORMER_PREFIXES = {"T", "TR", "XFMR", "XFRM", "X", "TX"}
OPTO_PREFIXES = {"OC", "OPT"}
OPTO_HINTS = ("OPTO", "PC817", "EL817", "LTV", "TLP", "HCPL", "SFH", "6N137", "ACPL", "VO6")
SWITCH_PREFIXES = {"Q", "M"}
PASSIVE_PREFIXES = {"C", "L", "FB", "BEAD", "F", "FU", "FL", "IND"}
DIODE_PREFIXES = {"D", "ZD", "TVS", "LED"}

VOLTAGE_PATTERNS = [
    re.compile(r"(?<![0-9A-Za-z])([+-]?\d+)V(\d)(?![0-9])", re.I),      # 3V3 / 12V5
    re.compile(r"([+-]?\d+(?:\.\d+)?)\s*V([pPnNsS])(?![0-9A-Za-z])", re.I),  # 18Vp / 12Vs
    re.compile(r"([+-]?\d+(?:\.\d+)?)\s*V(?![0-9A-Za-z])", re.I),       # 24V / 12.5V
    re.compile(r"VCC_?([+-]?\d+(?:\.\d+)?)", re.I),                     # VCC12
]

#: 驱动网络：栅极驱动供电（18Vp / 12Vs）或栅极控制信号
DRIVE_NAME_RE = re.compile(r"\d+(?:\.\d+)?\s*V[pPnNsS](?![0-9A-Za-z])|GATE|DRV|VGS|VGE|IGBT_G", re.I)
DRIVE_PIN_NAMES = {"G", "GATE", "GD", "GS", "DRV", "HO", "LO", "GH", "GL"}
GROUND_PIN_NAMES = {"GND", "GNDISO", "GND_ISO", "VSS", "COM", "VEE", "PGND", "SGND", "0V"}
#: 形如 H1 / L2 / U3 的「域」标记，用于把 18Vp-H1 与 H1-GND 配对
DOMAIN_TOKEN_RE = re.compile(r"^[A-Z]{1,3}\d{1,2}$")


# --------------------------------------------------------------------------
# 并查集（带侧别标签）
# --------------------------------------------------------------------------
class _UnionFind:
    def __init__(self, items: Iterable[str]):
        self.parent: dict[str, str] = {item: item for item in items}
        self.label: dict[str, str | None] = {item: None for item in items}

    def find(self, item: str) -> str:
        parent = self.parent.get(item, item)
        if parent != item:
            root = self.find(parent)
            self.parent[item] = root
            return root
        return item

    def root_label(self, item: str) -> str | None:
        return self.label.get(self.find(item))

    def set_label(self, item: str, side: str) -> None:
        self.label[self.find(item)] = side

    def try_union(self, a: str, b: str) -> bool:
        """合并两个集合；两侧标签冲突则放弃合并并返回 False。"""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        la, lb = self.label.get(ra), self.label.get(rb)
        if la and lb and la != lb:
            return False
        self.parent[rb] = ra
        if la is None and lb is not None:
            self.label[ra] = lb
        return True


def _side_from_name(name: str) -> str | None:
    upper = (name or "").upper()
    for hint in SECONDARY_HINTS:
        if hint in upper:
            return "SECONDARY"
    for hint in PRIMARY_HINTS:
        if hint in upper:
            return "PRIMARY"
    return None


def _prefix(designator: str) -> str:
    match = re.match(r"^([A-Za-z_]+)", designator or "")
    return (match.group(1) if match else "").upper()


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^A-Za-z0-9]+", (name or "").upper()) if t]


def extract_voltages(name: str) -> list[float]:
    """从网络名里抽取显式电压（V），例如 +24V / 3V3 / VCC12。"""
    values: list[float] = []
    for pattern in VOLTAGE_PATTERNS:
        for match in pattern.finditer(name or ""):
            groups = match.groups()
            try:
                second = groups[1] if len(groups) >= 2 else None
                if second and str(second).isdigit():
                    value = float(f"{groups[0]}.{second}")
                else:
                    value = float(groups[0])
            except (TypeError, ValueError):
                continue
            if 0 < abs(value) <= 1500:
                values.append(abs(value))
    return sorted(set(values))


def _is_ground(name: str) -> bool:
    upper = (name or "").upper()
    return any(hint in upper for hint in GROUND_HINTS)


def _struct_isolation(designator: str, comment: str, pad_count: int) -> str:
    """结构上可判定为隔离器件（变压器 / 光耦）时返回原因。"""
    prefix = _prefix(designator)
    upper_comment = (comment or "").upper()
    if prefix in TRANSFORMER_PREFIXES and pad_count >= 4:
        return "隔离变压器"
    if prefix in OPTO_PREFIXES:
        return "光耦"
    if prefix == "U" and pad_count >= 4 and any(h in upper_comment for h in OPTO_HINTS):
        return "光耦"
    if any(h in upper_comment for h in ("XFMR", "TRANSFORMER", "OPTO")):
        return "隔离器件"
    return ""


def _natural_key(text: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text or "")]


def _propagate_sides(board: Board, union: _UnionFind, facts: dict[str, _NetFacts],
                     skip: set[str]) -> None:
    """沿非隔离器件把侧别标签扩散到同一侧的相邻网络。"""
    for designator in board.components:
        if designator in skip:
            continue
        comp_nets = [n for n in board.nets_of_component(designator) if n in facts]
        if len(comp_nets) < 2:
            continue
        base = comp_nets[0]
        for other in comp_nets[1:]:
            union.try_union(base, other)


def _assign_sides_by_ground(union: _UnionFind, facts: dict[str, _NetFacts]) -> None:
    """按接地域判定侧别：信号地（SGND 等）一族算副边，功率地（PGND/MGND）一族算原边。

    隔离电源里原副边在 PCB 上是分开的，接地域往往比网络名更可靠：
    例如 SGND 域（控制侧）与 PGND/MGND 域（功率侧）之间没有任何直连。
    """
    secondary_hints = ("SGND", "AGND", "DGND", "SIGNAL")
    primary_hints = ("PGND", "MGND", "PWRGND")

    votes: dict[str, dict[str, int]] = {}
    for name, fact in facts.items():
        if not fact.is_ground or fact.side:
            continue
        upper = name.upper()
        label = ""
        if any(hint in upper for hint in secondary_hints):
            label = "SECONDARY"
        elif any(hint in upper for hint in primary_hints):
            label = "PRIMARY"
        if not label:
            continue
        root = union.find(name)
        bucket = votes.setdefault(root, {})
        bucket[label] = bucket.get(label, 0) + 1

    for root, bucket in votes.items():
        if len(bucket) != 1:
            continue
        label = next(iter(bucket))
        for name in facts:
            if union.find(name) == root and not union.root_label(name):
                union.set_label(name, label)


def _assign_winding_sides(board: Board, union: _UnionFind, barriers: dict[str, str]) -> None:
    """把变压器/光耦两侧绕组按焊盘顺序对半划分，给未定的一侧补上相反侧别。"""
    for designator, why in barriers.items():
        if "变压器" not in why and "光耦" not in why:
            continue
        pads = [p for p in board.pads_of_component(designator) if p.net]
        if len(pads) < 4 or len(pads) % 2:
            continue
        pads.sort(key=lambda p: _natural_key(p.name))
        half = len(pads) // 2
        groups = [pads[:half], pads[half:]]
        group_sides: list[str | None] = []
        for group in groups:
            labels = {union.root_label(p.net) for p in group if union.root_label(p.net)}
            group_sides.append(labels.pop() if len(labels) == 1 else None)

        if group_sides[0] and not group_sides[1]:
            unknown_index, known = 1, group_sides[0]
        elif group_sides[1] and not group_sides[0]:
            unknown_index, known = 0, group_sides[1]
        else:
            continue
        target = "SECONDARY" if known == "PRIMARY" else "PRIMARY"
        for pad in groups[unknown_index]:
            if pad.net in union.parent and union.root_label(pad.net) is None:
                union.set_label(pad.net, target)


@dataclass
class _NetFacts:
    name: str
    stats_pad_count: int = 0
    components: set[str] = field(default_factory=set)
    pin_names: set[str] = field(default_factory=set)
    explicit_voltages: list[float] = field(default_factory=list)
    side: str | None = None
    side_reason: str = ""
    switching: bool = False
    switching_reason: str = ""
    voltage: float | None = None
    voltage_reason: str = ""
    inherited: bool = False
    is_ground: bool = False


def classify_board(
    board: Board,
    pins: list[dict[str, Any]] | None = None,
    only_nets: set[str] | None = None,
) -> dict[str, NetClassification]:
    """对板上网络做电压类别判定。

    ``only_nets`` 不为空时只判这些网络（有配置基线时只需算新增网络），
    其余网络直接从待判集合里剔除，避免做无用的拓扑推断。
    """
    net_stats = board.net_stats()

    # 原理图引脚名（如果有）：component/pin -> pin_name
    pin_records: list[tuple[str, str, str, str]] = []
    for record in pins or []:
        component = str(record.get("component") or "").strip()
        net = str(record.get("net") or "").strip()
        if not component or not net:
            continue
        pin_records.append(
            (
                component,
                str(record.get("pin") or "").strip(),
                str(record.get("pin_name") or record.get("name") or "").strip(),
                net,
            )
        )

    facts: dict[str, _NetFacts] = {}
    for name in board.nets:
        if not name:
            continue
        if only_nets is not None and name not in only_nets:
            continue
        stats = net_stats.get(name)
        facts[name] = _NetFacts(
            name=name,
            stats_pad_count=stats.pad_count if stats else 0,
            components=set(stats.components) if stats else set(),
            explicit_voltages=extract_voltages(name),
            is_ground=_is_ground(name),
        )

    net_pins: dict[str, list[tuple[str, str]]] = {}
    component_nets: dict[str, set[str]] = {}
    component_pins: dict[str, dict[str, str]] = {}
    for component, _pin, pin_name, net in pin_records:
        if net in facts and pin_name:
            facts[net].pin_names.add(pin_name.upper())
        net_pins.setdefault(net, []).append((component, pin_name.upper()))
        component_nets.setdefault(component, set()).add(net)
        component_pins.setdefault(component, {})[pin_name.upper()] = net

    # 开关管拓扑：栅极 / Kelvin 源极 / 功率源极 / 漏极（用于判定参考点是否跳变）
    switches = _switch_map(component_pins)
    kelvin_states = _kelvin_states(switches)
    gate_reference: dict[str, tuple[str, str]] = {}
    for designator, info in switches.items():
        for gate_net in info["gate"]:
            kelvin = next(iter(info["kelvin"]), "")
            if gate_net and kelvin:
                gate_reference.setdefault(gate_net, (kelvin, designator))

    # ---- 1. 侧别判定（并查集 + 隔离器件划分） ---------------------
    union = _UnionFind(facts.keys())
    for name, fact in facts.items():
        side = _side_from_name(name)
        if side:
            union.set_label(name, side)
            fact.side_reason = f"网络名含{'原边' if side == 'PRIMARY' else '副边'}标识"

    barriers: dict[str, str] = {}
    for designator, comp in board.components.items():
        why = _struct_isolation(designator, comp.comment, len(board.pads_of_component(designator)))
        if why:
            barriers[designator] = why

    _propagate_sides(board, union, facts, skip=set(barriers))
    _assign_sides_by_ground(union, facts)
    _assign_winding_sides(board, union, barriers)

    # 没有任何侧别标识、也没有隔离器件时，整板按单域处理
    if not any(union.root_label(name) for name in facts):
        for name in facts:
            union.set_label(name, "PRIMARY")

    for name, fact in facts.items():
        fact.side = union.root_label(name)
        if fact.side and not fact.side_reason:
            fact.side_reason = "经同侧器件连通性推断所在侧"

    # ---- 2. 名称中的显式电压，之后沿无源通路继承 ------------------
    for fact in facts.values():
        if fact.is_ground:
            fact.voltage = 0.0
            fact.voltage_reason = "接地网络"
        elif fact.explicit_voltages:
            fact.voltage = max(fact.explicit_voltages)
            fact.voltage_reason = f"网络名含电压 {fact.voltage:g} V"

    for _ in range(3):
        changed = False
        for designator in board.components:
            prefix = _prefix(designator)
            if prefix not in PASSIVE_PREFIXES:
                continue
            comp_nets = [n for n in board.nets_of_component(designator) if n in facts]
            if len(comp_nets) != 2:
                continue
            fa, fb = facts[comp_nets[0]], facts[comp_nets[1]]
            for src, dst in ((fa, fb), (fb, fa)):
                if (src.voltage is not None and src.voltage > 0
                        and dst.voltage is None and not dst.is_ground):
                    dst.voltage = src.voltage
                    dst.voltage_reason = f"经 {designator} 继承 {src.name} 的电压"
                    dst.inherited = True
                    changed = True
        if not changed:
            break

    # ---- 3. 开关节点判定 ------------------------------------------
    for name, fact in facts.items():
        if fact.is_ground:
            continue
        upper = name.upper()
        tokens = set(_tokens(name))
        hint_hit = [h for h in SWITCHING_HINTS if h in tokens or (len(h) > 3 and h in upper)]
        gate_hit = [h for h in GATE_HINTS if h in tokens or (len(h) > 3 and h in upper)]

        pin_hit = fact.pin_names & {"D", "DRAIN", "C", "COLLECTOR", "SW", "LX", "PHASE", "SWITCH"}
        gate_pin = fact.pin_names & {"G", "GATE"}
        device_hit = [
            designator
            for designator in fact.components
            if _prefix(designator) in SWITCH_PREFIXES and pin_hit
        ]

        if gate_hit or gate_pin:
            fact.switching = True
            fact.switching_reason = "驱动/栅极信号，属跳变电平"
        elif hint_hit:
            fact.switching = True
            fact.switching_reason = f"网络名含开关节点标识（{hint_hit[0]}）"
        elif pin_hit or device_hit:
            fact.switching = True
            fact.switching_reason = "连接开关管/变压器绕组，属开关节点"

    # 同侧母线电压，用于估计跳变区间
    rail_by_side: dict[str, float] = {}
    for fact in facts.values():
        if fact.side and fact.voltage is not None and not fact.switching and not fact.is_ground:
            rail_by_side[fact.side] = max(rail_by_side.get(fact.side, 0.0), fact.voltage)

    # ---- 4. 汇总分类结果 ------------------------------------------
    results: dict[str, NetClassification] = {}
    for name, fact in facts.items():
        reasons: list[str] = []
        confidence = 0.35
        low = high = None
        voltage = fact.voltage

        if fact.side_reason:
            reasons.append(fact.side_reason)
            confidence += 0.25
        elif fact.side:
            reasons.append("经同侧器件连通性推断所在侧")
            confidence += 0.1
        else:
            reasons.append("无法确定位于隔离带哪一侧")

        if fact.voltage_reason:
            reasons.append(fact.voltage_reason)
            confidence += 0.25 if not fact.inherited else 0.1

        # 驱动参考点：优先按开关管 Kelvin 源极（pin DS/KS）判定是否随开关节点跳变
        kelvin = kelvin_states.get(name)
        ambiguous_kelvin = kelvin is not None and kelvin[0] is None
        isolated_ref = bool(fact.is_ground and (fact.pin_names & {"GNDISO", "GND_ISO"}))
        if ambiguous_kelvin:
            kelvin = None  # 交给下面的 GNDISO 保守处理（按跳变、需人工确认）
        if kelvin is not None:
            swings, designator, upper_rail, lower_rail = kelvin
            if swings:
                fact.switching = True
                fact.voltage = None
                voltage = None
                fact.voltage_reason = ""
                fact.switching_reason = f"{designator} 的 Kelvin 源极随开关节点跳变"
                upper = (facts.get(upper_rail).voltage if upper_rail in facts else None)
                lower = (facts.get(lower_rail).voltage if lower_rail in facts else None)
                if upper is not None and lower is not None:
                    low, high = min(upper, lower), max(upper, lower)
            else:
                fact.switching = False
                fact.switching_reason = ""
                rail = facts.get(upper_rail).voltage if upper_rail in facts else None
                if rail is not None and not fact.is_ground:
                    fact.voltage = rail
                    fact.voltage_reason = f"{designator} 的 Kelvin 源极接固定电位 {rail:g} V"
        elif isolated_ref:
            fact.voltage = None
            voltage = None
            fact.voltage_reason = ""
            fact.switching = True
            fact.switching_reason = "隔离驱动参考地（GNDISO），电位随开关节点跳变"

        if kelvin is not None:
            designator = kelvin[1]
            if kelvin[0]:
                reasons.append(fact.switching_reason)
                if low is not None and high is not None:
                    reasons.append(f"跳变区间按母线 {low:g} ~ {high:g} V 推导")
                else:
                    reasons.append("跳变区间需按两级母线电平确认")
                confidence = max(confidence, 0.7)
            else:
                reasons.append(f"{designator} 的 Kelvin 源极接固定电位（低边，不跳变）")
                confidence = max(confidence, 0.7)
        elif isolated_ref:
            reasons.append(fact.switching_reason)
            if ambiguous_kelvin:
                reasons.append("该参考点在不同桥臂配对里角色不一致（母线共享），请确认电平")
            else:
                reasons.append("跳变区间由所连开关节点决定，需人工确认")
            confidence = max(confidence, 0.55)
        elif fact.switching:
            reasons.append(fact.switching_reason)
            rail = rail_by_side.get(fact.side or "", 0.0)
            base = max(voltage or 0.0, rail)
            gate = any(h in name.upper() for h in GATE_HINTS) or bool(fact.pin_names & {"G", "GATE"})
            if gate:
                high = min(base, 20.0) if base else 20.0
                reasons.append("栅极电平上限按 20 V 估计")
            else:
                high = base if base else (voltage or None)
                if high:
                    reasons.append(f"跳变区间上限按同侧最高母线 {high:g} V 估计")
            low = 0.0 if high is not None else None
            confidence += 0.05

        if fact.is_ground:
            reasons.append("识别为接地网络")
            confidence = max(confidence, 0.8)

        if fact.side == "PRIMARY" and fact.switching:
            net_class = NetClass.PRIMARY_SWITCHING
        elif fact.side == "PRIMARY":
            net_class = NetClass.PRIMARY_CONST
        elif fact.side == "SECONDARY":
            net_class = NetClass.SECONDARY
        else:
            # 「未定」分类已取消：无法判明侧别的一律按副边处理
            net_class = NetClass.SECONDARY
            confidence = min(confidence, 0.35)
            reasons.append("未判明原/副边，按副边处理")

        if net_class is not NetClass.UNKNOWN and not fact.switching:
            if low is None and voltage is not None:
                low = high = voltage
        if net_class is NetClass.SECONDARY and voltage is None and high is not None:
            voltage = high

        final_voltage = voltage if voltage is not None else (high if high is not None else None)
        result = NetClassification(
            net=name,
            net_class=net_class,
            voltage=final_voltage,
            voltage_min=low,
            voltage_max=high,
            confidence=round(min(confidence, 0.95), 2),
            reasons=reasons,
            confirmed=False,
            needs_review=(net_class is NetClass.UNKNOWN or confidence < 0.6 or final_voltage is None),
            auto_class=net_class,
            is_drive=_is_drive_net(name, net_pins),
        )
        results[name] = result

    _apply_zero_ohm_bonds(results, _zero_ohm_bonds(board))
    _resolve_reference_grounds(facts, results, net_pins, component_nets, gate_reference)
    assign_secondary_references(results)
    assign_primary_references(results)
    apply_follow_target(results)
    normalize_unknown_to_secondary(results)
    _refine_isolated_grounds(facts, results, net_pins, component_nets)
    apply_reference_potential(results)

    if barriers:
        board.warnings.append(
            "识别到隔离器件: "
            + "、".join(f"{name}（{why}）" for name, why in list(barriers.items())[:8])
        )

    return results


SWITCH_GATE_PINS = {"G", "GATE", "GD", "GS"}
SWITCH_KELVIN_PINS = {"DS", "KS", "KELVIN", "K"}
SWITCH_SOURCE_PINS = {"S", "SOURCE", "E", "EMITTER", "PS", "PS1", "PS2"}
SWITCH_DRAIN_PINS = {"D", "DRAIN", "C", "COLLECTOR"}


ZERO_OHM_PREFIXES = {"R", "L", "FB", "BEAD", "F", "JP", "NT", "J"}

#: 阻值不超过该值的电阻按「等电位直连」处理（用户口径：10Ω 及以下与 0Ω 相同）
LOW_OHM_LIMIT = 10.0

_OHM_MULTIPLIERS = (("MEG", 1e6), ("K", 1e3), ("M", 1e6), ("G", 1e9))


_VALUE_R_RE = re.compile(r"^(\d*)[R](\d*)$")
_VALUE_PLAIN_RE = re.compile(r"^(\d+(?:\.\d+)?)([KMG]?)$")
_VALUE_HENRY_RE = re.compile(r"^(\d+(?:\.\d+)?)([PNUM]?)H$")
_VALUE_OHM_RE = re.compile(r"^(\d+(?:\.\d+)?)R?$")


def _component_value(comment: str) -> tuple[str, float] | None:
    """从器件值里解析 (类型, 数值)：``R`` 电阻(Ω) / ``H`` 电感(H) / ``Z`` 磁珠阻抗。

    器件注释格式不统一，例如：
    ``10R 1/16W 0402``、``4R7``、``0R``、``10K``、
    ``LQM21PN1R0NGRD 1uH/700mA``、``ACM2520-601-2P-T002 600Ω@100MHz/0.3A``。
    因此逐个 token 找「像数值」的那一段，跳过型号。
    """
    text = (comment or "").strip()
    if not text:
        return None
    for raw_token in text.split():
        token = raw_token.split("/")[0].split("@")[0].strip().upper()
        token = token.replace("Ω", "R").replace("OHM", "R").replace(" ", "")
        if not token:
            continue

        match = _VALUE_HENRY_RE.match(token)
        if match:
            factor = {"P": 1e-12, "N": 1e-9, "U": 1e-6, "M": 1e-3, "": 1.0}[match.group(2)]
            try:
                return "H", float(match.group(1)) * factor
            except ValueError:
                continue

        match = _VALUE_R_RE.match(token)
        if match:
            left, right = match.group(1), match.group(2)
            if not left and not right:
                continue
            try:
                return "R", float(f"{left or '0'}.{right or '0'}")
            except ValueError:
                continue

        match = _VALUE_PLAIN_RE.match(token)
        if match:
            factor = {"K": 1e3, "M": 1e6, "G": 1e9, "": 1.0}[match.group(2)]
            try:
                return "R", float(match.group(1)) * factor
            except ValueError:
                continue
    return None


def _resistance_value(comment: str) -> float | None:
    """解析阻值（Ω）；不是电阻值返回 None。"""
    parsed = _component_value(comment)
    if parsed is None or parsed[0] != "R":
        return None
    return parsed[1]


def _is_zero_ohm(comment: str) -> bool:
    """电阻值是否属于「等电位直连」：0Ω ~ 10Ω（含 5mΩ 采样电阻）。"""
    value = _resistance_value(comment)
    return value is not None and 0.0 <= value <= LOW_OHM_LIMIT


#: 直流直通器件：电感 / 磁珠 / 保险丝 / 跳线 / 网络连接件
#: 注意不含 J（接插件端子两端是不同网络，不是直连）
DC_LINK_PREFIXES = {"L", "FB", "BEAD", "F", "JP", "NT"}

#: 二极管：只在「一端有人工命名、一端是自动命名」时让自动端跟随命名端
DIODE_PREFIXES = {"D", "DZ", "ZD", "TVS", "LED"}


def _is_dc_link(designator: str, comment: str) -> str:
    """判断器件是否把两端在直流上连通：返回 ``res`` / ``dc`` / 空串。"""
    prefix = _prefix(designator)
    if prefix == "R":
        return "res" if _is_zero_ohm(comment) else ""
    if prefix in DC_LINK_PREFIXES:
        return "dc"
    if prefix in DIODE_PREFIXES:
        return "diode"
    return ""


def _zero_ohm_bonds(board) -> list[tuple[str, str, str, str]]:
    """列出直流直通器件连接的两端网络：``[(位号, net_a, net_b, 类型), ...]``。"""
    bonds: list[tuple[str, str, str, str]] = []
    for designator, comp in board.components.items():
        kind = _is_dc_link(designator, comp.comment)
        if not kind:
            continue
        nets = sorted({net for net in board.nets_of_component(designator) if net})
        if len(nets) != 2:
            continue
        bonds.append((designator, nets[0], nets[1], kind))
    return bonds


def _voltage_rank(item: NetClassification) -> tuple[int, int, float]:
    return (
        1 if item.voltage is not None or item.voltage_min is not None else 0,
        1 if item.net_class is not NetClass.UNKNOWN else 0,
        item.confidence,
    )


def _apply_zero_ohm_bonds(
    results: dict[str, NetClassification],
    bonds: list[tuple[str, str, str, str]],
) -> int:
    """直流直通器件两端等电位：把信息更全一侧的类别与电压复制到另一侧。

    ``kind == "res"`` 是 0~10Ω 电阻；``kind == "dc"`` 是电感 / 磁珠 / 保险丝等，
    后者不跨越开关节点（电感两端开关波形不同，不能当成同一电位）。
    """
    merged = 0
    for designator, net_a, net_b, kind in bonds:
        item_a, item_b = results.get(net_a), results.get(net_b)
        if item_a is None or item_b is None:
            continue
        if kind == "dc" and (
            item_a.net_class is NetClass.PRIMARY_SWITCHING
            or item_b.net_class is NetClass.PRIMARY_SWITCHING
        ):
            # 电感串在开关节点与输出之间，两端不是同一电位
            continue
        if kind == "diode":
            # 二极管只在「一端命名、一端自动命名」时建立跟随，
            # 两端都命名（如整流管 SEC_GND↔Vout）不做合并
            named_a = not is_auto_named(net_a)
            named_b = not is_auto_named(net_b)
            if named_a == named_b:
                continue
        named_a = not is_auto_named(net_a)
        named_b = not is_auto_named(net_b)
        if named_a != named_b:
            # 一侧有人工命名：命名网络只能当源，自动命名的跟随它
            source, target = (item_a, item_b) if named_a else (item_b, item_a)
            if _voltage_rank(source)[0] == 0:
                # 命名侧还没有电压：只建立跟随关系（不复制数值），
                # 也绝不把自动命名的值反向灌进命名网络
                if target.follow_net != source.net:
                    target.follow_net = source.net
                    note = f"经 {designator} 跟随 {source.net}"
                    if note not in target.reasons:
                        target.reasons.append(note)
                    merged += 1
                continue
        else:
            source, target = ((item_a, item_b) if _voltage_rank(item_a) >= _voltage_rank(item_b)
                              else (item_b, item_a))
            if _voltage_rank(source)[0] == 0:
                continue

        target.net_class = source.net_class
        target.voltage = source.voltage
        target.voltage_min = source.voltage_min
        target.voltage_max = source.voltage_max
        target.auto_class = source.net_class
        target.confidence = max(target.confidence, source.confidence)
        target.needs_review = source.needs_review and target.voltage is None
        if source.is_drive and not target.is_drive:
            target.is_drive = True
        if source.ref_ground and not target.ref_ground:
            target.ref_ground = source.ref_ground
            target.ref_ground_reason = f"经 {designator}（0Ω）跟随 {source.net}"
        # 同时记为「跟随」，界面的跟随列会显示，后续改动也会自动同步；
        # 但绝不允许自动命名的网络锁住有命名的网络
        if not (is_auto_named(source.net) and not is_auto_named(target.net)):
            if target.follow_net != source.net and source.follow_net != target.net:
                target.follow_net = source.net
        note = f"经 {designator}（0Ω）与 {source.net} 等电位"
        if note not in target.reasons:
            target.reasons.append(note)
        merged += 1
    return merged


def _switch_map(component_pins: dict[str, dict[str, str]]) -> dict[str, dict[str, set[str]]]:
    """识别开关管的栅极 / Kelvin 源极 / 功率源极 / 漏极网络。"""
    switches: dict[str, dict[str, set[str]]] = {}
    for designator, pins in component_pins.items():
        if _prefix(designator) not in SWITCH_PREFIXES:
            continue
        gate = {net for pin, net in pins.items() if pin in SWITCH_GATE_PINS and net}
        kelvin = {net for pin, net in pins.items() if pin in SWITCH_KELVIN_PINS and net}
        source = {net for pin, net in pins.items() if pin in SWITCH_SOURCE_PINS and net}
        drain = {net for pin, net in pins.items() if pin in SWITCH_DRAIN_PINS and net}
        if not (gate or kelvin or source or drain):
            continue
        switches[designator] = {
            "gate": gate,
            "kelvin": kelvin or source or drain,
            "source": source,
            "drain": drain,
        }
    return switches


def _kelvin_states(
    switches: dict[str, dict[str, set[str]]]
) -> dict[str, tuple[bool, str, str, str]]:
    """Kelvin 源极网络是否随开关节点跳变。

    半桥里「功率源极接中点」的那只管子是高边管，它的 Kelvin 源极跟着中点跳；
    同一桥臂另一只（漏极接中点）是低边管，参考点恒定。
    返回 ``{net: (swings, designator, upper_rail, lower_rail)}``；``swings`` 为
    ``None`` 表示同一只管子在不同桥臂配对里既像高边又像低边（母线共享时会出现），
    此时交给人工确认，不做猜测。
    """
    candidates: dict[str, list[tuple[bool, str, str, str]]] = {}
    designators = list(switches)
    for high in designators:
        info = switches[high]
        for low in designators:
            if low == high:
                continue
            other = switches[low]
            shared = info["source"] & other["drain"]
            if not shared:
                continue
            midpoint = next(iter(shared))
            upper = next((n for n in info["drain"] if n != midpoint), "")
            lower = next((n for n in other["source"] if n != midpoint), "")
            for net in info["kelvin"]:
                if net in info["source"]:
                    continue  # 该管没有独立 Kelvin 引脚时不动功率网络
                candidates.setdefault(net, []).append((True, high, upper, lower))
            for net in other["kelvin"]:
                if net in other["source"]:
                    continue
                candidates.setdefault(net, []).append((False, low, upper, lower))

    states: dict[str, tuple[bool | None, str, str, str]] = {}
    for net, votes in candidates.items():
        flags = {vote[0] for vote in votes}
        chosen = votes[0]
        if len(flags) > 1:
            # 同一只开关管在不同配对里角色冲突（例如母线同时是另一桥臂的中点）
            states[net] = (None, chosen[1], chosen[2], chosen[3])
        else:
            states[net] = chosen
    return states


def _is_drive_net(net: str, net_pins: dict[str, list[tuple[str, str]]]) -> bool:
    """驱动网络：栅极驱动供电（18Vp / 12Vs）或栅极控制信号。"""
    if DRIVE_NAME_RE.search(net or ""):
        return True
    return any(pin in DRIVE_PIN_NAMES for _comp, pin in net_pins.get(net, ()))


def _find_reference_ground(
    net: str,
    facts: dict[str, _NetFacts],
    item: NetClassification,
    net_pins: dict[str, list[tuple[str, str]]],
    comp_nets: dict[str, set[str]],
    gate_reference: dict[str, tuple[str, str]] | None = None,
) -> tuple[str, str]:
    """找出驱动网络对应的参考地网络名。"""
    upper = (net or "").upper()

    # 0) 栅极信号：直接取被驱动开关管的 Kelvin 源极（最可靠）
    if gate_reference and net in gate_reference:
        kelvin, designator = gate_reference[net]
        if kelvin and kelvin != net:
            return kelvin, f"{designator} 的 Kelvin 源极"

    # 1) 同名域：18Vp-H1 -> H1-GND / 18Vp-L2 -> L2-GND
    for token in re.split(r"[^A-Za-z0-9]+", upper):
        if not token or not DOMAIN_TOKEN_RE.fullmatch(token):
            continue
        for cand, fact in facts.items():
            if cand == net or not fact.is_ground:
                continue
            if token in cand.upper():
                return cand, f"与 {token} 同域"

    # 2) 同器件参考：驱动脚 / 驱动供电脚 + 地脚
    best_score = 0
    best: tuple[str, str] = ("", "")
    for comp, pin_name in net_pins.get(net, ()):
        for gnet in comp_nets.get(comp, ()):
            if gnet == net:
                continue
            fact = facts.get(gnet)
            if not fact or not fact.is_ground:
                continue
            score = 1
            if pin_name in DRIVE_PIN_NAMES:
                score += 3
            if any(
                pin in GROUND_PIN_NAMES
                for other, pin in net_pins.get(gnet, ())
                if other == comp
            ):
                score += 2
            if _prefix(comp) in {"C", "DZ", "D", "TVS", "R", "L"}:
                score += 1
            if score > best_score:
                best_score = score
                best = (gnet, f"经 {comp}（{pin_name or '—'}）参考")
    if best_score >= 2:
        return best

    # 3) 兜底：同侧主地
    side = facts[net].side if net in facts else None
    candidates = [
        (fact.stats_pad_count, name)
        for name, fact in facts.items()
        if fact.is_ground and fact.side == side and name != net
    ]
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1], "同侧主地（建议确认）"

    return "", ""


def _resolve_reference_grounds(
    facts: dict[str, _NetFacts],
    results: dict[str, NetClassification],
    net_pins: dict[str, list[tuple[str, str]]],
    comp_nets: dict[str, set[str]],
    gate_reference: dict[str, tuple[str, str]] | None = None,
) -> None:
    for net, item in results.items():
        if not item.is_drive:
            continue
        ref, reason = _find_reference_ground(
            net, facts, item, net_pins, comp_nets, gate_reference
        )
        if ref:
            item.ref_ground = ref
            item.ref_ground_reason = reason
            item.reasons.append(f"参考地 {ref}（{reason}）")
        else:
            item.needs_review = True
            item.reasons.append("驱动网络未找到参考地，需要人工指定")


def assign_primary_references(results: dict[str, NetClassification]) -> None:
    """原边恒定电平的网络没有更具体参考地时，默认参考 PGND。"""
    for item in results.values():
        if "GND" in item.net.upper() and item.ref_ground:
            item.ref_ground = ""
            item.ref_ground_reason = ""

    ground = default_primary_ground(results.keys())
    if not ground or ground not in results:
        return
    for item in results.values():
        if item.net_class is not NetClass.PRIMARY_CONST:
            continue
        if item.ref_ground or item.net == ground or "GND" in item.net.upper():
            continue
        item.ref_ground = ground
        item.ref_ground_reason = "原边恒定网络默认参考 PGND"


_FOLLOW_FIELDS = (
    "net_class",
    "voltage",
    "voltage_min",
    "voltage_max",
    "ref_ground",
    "ref_ground_reason",
    "is_drive",
    "local_voltage_min",
    "local_voltage_max",
    "reference_applied",
    "auto_class",
)


def apply_follow_target(results: dict[str, NetClassification]) -> int:
    """把「跟随网络」的类别、电压、参考地复制过来。

    只处理 follow_net 指向存在网络的条目；目标不存在时清空跟随关系。
    """
    applied = 0
    for item in results.values():
        target = (item.follow_net or "").strip()
        if not target:
            continue
        if not is_auto_named(item.net) and is_auto_named(target):
            # 有命名的网络不允许被自动命名的网络锁住（旧数据里可能残留）
            item.follow_net = ""
            continue
        source = results.get(target)
        if source is None or source.net == item.net:
            item.follow_net = ""
            continue
        for field in _FOLLOW_FIELDS:
            setattr(item, field, getattr(source, field))
        note = f"跟随 {target}（电压与参考地完全一致）"
        if note not in item.reasons:
            item.reasons.append(note)
        item.needs_review = source.needs_review
        applied += 1
    return applied


def normalize_unknown_to_secondary(results: dict[str, NetClassification]) -> int:
    """把旧数据里残留的「未定」网络统一划归副边，返回改动数量。"""
    count = 0
    for item in results.values():
        if item.net_class is not NetClass.UNKNOWN:
            continue
        item.net_class = NetClass.SECONDARY
        item.auto_class = NetClass.SECONDARY
        if "按副边处理" not in "；".join(item.reasons):
            item.reasons.append("原「未定」网络按副边处理")
        count += 1
    return count


def assign_secondary_references(results: dict[str, NetClassification]) -> None:
    """副边网络没有更具体的参考地时，默认参考 SGND。"""
    for item in results.values():
        if "GND" in item.net.upper() and item.ref_ground:
            # 地网络自身就是参考点，不应再指向别的地（旧数据里可能被误设）
            item.ref_ground = ""
            item.ref_ground_reason = ""

    ground = default_secondary_ground(results.keys())
    if not ground:
        return
    for item in results.values():
        if "GND" in item.net.upper():
            continue  # 地网络本身不再指向另一个地
        if (item.net_class is NetClass.SECONDARY and not item.ref_ground
                and item.net != ground and ground in results):
            item.ref_ground = ground
            item.ref_ground_reason = "副边网络默认参考 SGND"


def _refine_isolated_grounds(
    facts: dict[str, _NetFacts],
    results: dict[str, NetClassification],
    net_pins: dict[str, list[tuple[str, str]]],
    comp_nets: dict[str, set[str]],
) -> None:
    """隔离驱动的参考地：跳变区间取所连开关节点的区间。"""
    for net, item in results.items():
        fact = facts.get(net)
        if fact is None or not fact.is_ground:
            continue
        if not (fact.pin_names & {"GNDISO", "GND_ISO"}):
            continue
        if item.voltage_max is not None:
            continue
        highs: list[float] = []
        for comp, _pin in net_pins.get(net, ()):
            for other in comp_nets.get(comp, ()):
                other_item = results.get(other)
                if other_item is None:
                    continue
                if (other_item.net_class is NetClass.PRIMARY_SWITCHING
                        and other_item.voltage_max):
                    highs.append(other_item.voltage_max)
        # 只有当所连开关节点本身是明确的高压母线时才采用该区间；
        # 18 V 驱动电源之类的低压节点不能用来描述高压侧浮动地。
        highs = [value for value in highs if value >= 50.0]
        if highs:
            top = max(highs)
            item.voltage_min = 0.0
            item.voltage_max = top
            item.voltage = top
            item.reasons.append(f"参考地随开关节点跳变，区间按 {top:g} V 估计")
        else:
            item.reasons.append("参考地电位随开关节点浮动，跳变区间需要人工确认（如 400~800 V）")
        item.needs_review = True


def apply_reference_potential(results: dict[str, NetClassification]) -> None:
    """参考地跳变时，驱动网络本身的绝对电位也跟着跳变。

    例如 18Vp-H1 比参考地高 18 V，参考地是 400~800 V 跳变，那么它自己的绝对
    电位就是 418~818 V，属于跳变电平；栅极信号（相对参考地 0~18 V）则是
    400~818 V。参考地恒定（如 0 V、400 V 母线）时不做改动。
    """
    for net, item in results.items():
        if not item.ref_ground:
            continue
        ref = results.get(item.ref_ground)
        if ref is None or ref.net == net:
            continue

        # 记下相对参考地的本地电平（只记一次），之后一直用它重算，保证可重复调用
        if item.local_voltage_min is None and item.local_voltage_max is None:
            local_min, local_max = item.voltage_min, item.voltage_max
            if local_min is None and local_max is None and item.voltage is not None:
                local_min = local_max = item.voltage
            item.local_voltage_min = local_min
            item.local_voltage_max = local_max

        if not ref.is_switching:
            # 参考地是恒定电平：类别与电平都回到折算前
            if item.reference_base_class is not NetClass.UNKNOWN:
                item.net_class = item.reference_base_class
                item.auto_class = item.reference_base_class
                item.reference_base_class = NetClass.UNKNOWN
            # 参考地恒定：绝对电位 = 参考地电压 + 本地电平（参考地未知时按 0 处理）
            offset = ref.voltage if ref.voltage is not None else 0.0
            local_lo = (item.local_voltage_min
                        if item.local_voltage_min is not None else item.voltage)
            local_hi = (item.local_voltage_max
                        if item.local_voltage_max is not None else local_lo)
            if local_lo is not None:
                item.voltage_min = round(offset + local_lo, 3)
                item.voltage_max = round(offset + (local_hi if local_hi is not None else local_lo), 3)
                item.voltage = max(item.voltage_min, item.voltage_max)
                item.reference_applied = offset != 0.0
            continue

        ref_lo, ref_hi = ref.normalized_range()
        if ref_lo is None and ref_hi is None and ref.voltage is not None:
            # 只填了电压（没填上下限）的跳变参考地：按 0 ~ 峰值 处理
            ref_lo, ref_hi = 0.0, ref.voltage
        if ref_lo is None or ref_hi is None:
            item.needs_review = True
            if not item.reference_applied:
                item.reasons.append(
                    f"参考地 {ref.net} 为跳变电平但区间未知，{net} 的绝对电位待确认"
                )
            if item.net_class is NetClass.PRIMARY_CONST:
                item.reference_base_class = item.net_class
                item.net_class = NetClass.PRIMARY_SWITCHING
                item.auto_class = NetClass.PRIMARY_SWITCHING
            # 本地电平已知、绝对区间未知：只显示本地值，上下限留空等人工确认
            if item.local_voltage_max is not None:
                item.voltage = item.local_voltage_max
            item.voltage_min = None
            item.voltage_max = None
            continue

        local_lo = item.local_voltage_min if item.local_voltage_min is not None else 0.0
        local_hi = (item.local_voltage_max
                    if item.local_voltage_max is not None
                    else (item.local_voltage_min if item.local_voltage_min is not None else 0.0))
        absolute_lo = ref_lo + local_lo
        absolute_hi = ref_hi + local_hi
        if absolute_hi < absolute_lo:
            absolute_lo, absolute_hi = absolute_hi, absolute_lo

        item.voltage_min = round(absolute_lo, 3)
        item.voltage_max = round(absolute_hi, 3)
        item.voltage = round(absolute_hi, 3)
        if item.net_class is NetClass.PRIMARY_CONST:
            item.reference_base_class = item.net_class
            item.net_class = NetClass.PRIMARY_SWITCHING
            item.auto_class = NetClass.PRIMARY_SWITCHING
        if not item.reference_applied:
            item.reasons.append(
                f"参考地 {ref.net} 为 {ref_lo:g} ~ {ref_hi:g} V 跳变，"
                f"绝对电位 {absolute_lo:g} ~ {absolute_hi:g} V"
            )
        item.reference_applied = True
        item.needs_review = False


def summarize(results: dict[str, NetClassification]) -> dict[str, int]:
    """按类别统计数量。"""
    summary = {c.value: 0 for c in NetClass}
    for item in results.values():
        summary[item.net_class.value] += 1
    return summary
