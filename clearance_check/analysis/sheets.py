"""按原理图图纸聚合网络，并把跨页网络区分为「功率侧 / 控制侧」。

数据来源：
  * 每张 SchDoc 上放置的器件（session.sheet_component_map 通过 obj_query 读取）；
  * 原理图编译网表的引脚（component / pin_name / net）。
由「图纸 -> 器件 -> 引脚 -> 网络」还原出「图纸 -> 网络」，同时统计每个网络
出现在哪些图纸上；出现在 2 张及以上图纸的网络即跨页网络，汇总到 common。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from ..model import Board, NetClass, NetClassification, SheetNetIndex

# 功率器件：出现这些器件的网络默认归入「功率侧」
POWER_PREFIXES = {
    "T", "TR", "XFMR", "XFRM", "X", "TX",
    "Q", "M", "IGBT", "MOS",
    "D", "ZD", "TVS", "DB", "BR", "LED",
    "L", "IND", "CHK",
    "J", "CN", "CON", "JACK", "TB", "P",
    "F", "FU", "FUSE", "RV", "MOV", "NTC", "PTC",
}
# 只按「控制脚」看待的引脚名（功率管栅极/基极不算功率通路）
CONTROL_PIN_NAMES = {"G", "GATE", "B", "BASE", "DRV", "DRIVE", "GD", "GS"}
# 名称中的控制/信号特征词
CONTROL_HINTS = (
    "PWM", "GATE", "DRV", "FAULT", "ALERT", "ALARM", "EN", "ENABLE",
    "CS", "FB", "COMP", "OVP", "OCP", "OTP", "SCP", "PG", "PGOOD",
    "SPI", "I2C", "SCL", "SDA", "UART", "TX", "RX", "CAN", "CLK",
    "RESET", "NRST", "BOOT", "SENSE", "TRIP", "ALERT",
)
# 名称中的功率特征词
POWER_HINTS = (
    "GND", "VSS", "PGND", "AGND", "SGND", "MGND", "0V",
    "HV", "VBUS", "DC_BUS", "DCLINK", "AC_L", "AC_N", "PRI", "SEC",
    "SW", "LX", "PHASE", "DRAIN", "VBAT", "VIN", "VOUT",
)

_PREFIX_RE = re.compile(r"^([A-Za-z_]+)")


def _prefix(designator: str) -> str:
    match = _PREFIX_RE.match(designator or "")
    return (match.group(1) if match else "").upper()


def _tokens(net: str) -> set[str]:
    return {token for token in re.split(r"[^A-Za-z0-9]+", (net or "").upper()) if token}


def pins_by_component(pins: Iterable[dict[str, Any]]) -> dict[str, list[tuple[str, str]]]:
    """designator -> [(pin, pin_name), ...]"""
    mapping: dict[str, list[tuple[str, str]]] = {}
    for record in pins or []:
        designator = str(record.get("component") or "").strip()
        if not designator:
            continue
        pin = str(record.get("pin") or "")
        pin_name = str(record.get("pin_name") or record.get("name") or pin).upper()
        mapping.setdefault(designator, []).append((pin, pin_name))
    return mapping


def build_sheet_index(
    pins: Iterable[dict[str, Any]],
    sheet_components: dict[str, list[str]],
    board: Board | None = None,
    sheet_paths: dict[str, str] | None = None,
    instance_sheets: dict[str, str] | None = None,
    top_sheet: str = "",
) -> SheetNetIndex:
    """由「图纸 -> 器件」+ 编译网表还原「图纸 -> 网络」。

    多通道图纸的编译位号带实例后缀（如 ``Q9_U_SR_Cout14``），``instance_sheets``
    给出实例名到图纸的映射，用来把这类网络归到正确图纸。
    """
    exact: dict[str, str] = {}
    for sheet, designators in sheet_components.items():
        for designator in designators:
            exact.setdefault(designator, sheet)

    instances = sorted((instance_sheets or {}).items(), key=lambda item: -len(item[0]))

    def sheet_of(designator: str) -> str | None:
        sheet = exact.get(designator)
        if sheet:
            return sheet
        for instance, name in instances:
            if designator.endswith("_" + instance):
                return name
        return top_sheet or None

    per_sheet: dict[str, list[str]] = {name: [] for name in sheet_components}
    unattributed: set[str] = set()
    for record in pins or []:
        designator = str(record.get("component") or "").strip()
        net = str(record.get("net") or "").strip()
        if not designator or not net:
            continue
        sheet = sheet_of(designator)
        if sheet and sheet in per_sheet:
            per_sheet[sheet].append(net)
        else:
            unattributed.add(net)

    index = SheetNetIndex()
    for sheet in sheet_components:
        index.add_sheet(sheet, per_sheet.get(sheet, []), path=(sheet_paths or {}).get(sheet, ""))

    # 网表里存在、但没有定位到图纸的网络（例如只在 PCB 上存在）归入 common
    assigned = set(index.net_sheets)
    orphans: list[str] = [net for net in sorted(unattributed) if net not in assigned]
    if board is not None:
        orphans += [net for net in board.nets if net and net not in assigned and net not in orphans]
    if orphans:
        index.add_sheet("（未归属图纸）", orphans)

    return index


def net_side(
    net: str,
    board: Board,
    classification: NetClassification | None,
    pins: Iterable[dict[str, Any]] = (),
) -> tuple[str, str]:
    """判定网络属于「功率侧」还是「控制侧」，返回 (power|control, 依据)。"""
    upper = (net or "").upper()
    tokens = _tokens(net)

    # 1. 接地 / 母线类名称
    if any(hint in upper for hint in ("GND", "VSS", "0V")):
        return "power", "接地网络"
    if any(hint in upper for hint in ("HV", "VBUS", "DCLINK", "DC_BUS", "AC_L", "AC_N")):
        return "power", "功率母线标识"

    # 2. 名称中的控制特征词（栅极驱动、反馈、保护、通信）优先于「跳变电平」判断，
    #    否则 GATE / PWM 这类驱动网络会被当成开关节点归到功率侧。
    is_power_node = any(hint in tokens or hint in upper for hint in ("SW_NODE", "DRAIN", "LX", "PHASE"))
    for hint in CONTROL_HINTS:
        if is_power_node:
            break
        if hint in tokens or (len(hint) > 3 and hint in upper):
            return "control", f"名称含控制信号标识（{hint}）"

    # 3. 开关节点（跳变电平）本身就是功率通路
    if classification is not None and classification.net_class is NetClass.PRIMARY_SWITCHING:
        return "power", "原边开关节点（功率通路）"

    # 4. 连接了功率器件（排除只接在栅极/基极上的情况）
    power_devices: list[str] = []
    only_control_pins = True
    for record in pins or []:
        if str(record.get("net") or "") != net:
            continue
        designator = str(record.get("component") or "")
        pin_name = str(record.get("pin_name") or "").upper()
        if _prefix(designator) in POWER_PREFIXES:
            power_devices.append(designator)
            if pin_name not in CONTROL_PIN_NAMES:
                only_control_pins = False
    if power_devices and not only_control_pins:
        return "power", f"连接功率器件 {power_devices[0]}"

    # 5. 名称中的功率特征词
    for hint in POWER_HINTS:
        if hint in tokens or (len(hint) > 3 and hint in upper):
            return "power", f"名称含功率标识（{hint}）"

    # 6. 类别兜底：原边且电压较高视为功率
    if classification is not None and classification.net_class is NetClass.PRIMARY_CONST:
        voltage = classification.voltage or 0.0
        if voltage >= 24.0:
            return "power", f"原边 {voltage:g} V 母线"

    if power_devices:
        return "power", f"连接功率器件 {power_devices[0]}（仅控制脚）"
    return "control", "信号 / 控制网络"


def split_common_nets(
    index: SheetNetIndex,
    board: Board,
    classifications: dict[str, NetClassification],
    pins: Iterable[dict[str, Any]] = (),
) -> dict[str, list[str]]:
    """把跨页网络拆成 {'power': [...], 'control': [...]}。"""
    pins = list(pins or [])
    buckets: dict[str, list[str]] = {"power": [], "control": []}
    for net in index.shared_nets():
        item = classifications.get(net)
        override = (item.sheet_side if item else "").strip().lower()
        if override in ("power", "control"):
            buckets[override].append(net)
            continue
        side, _reason = net_side(net, board, item, pins)
        buckets[side].append(net)
    for key in buckets:
        buckets[key].sort(key=lambda name: (classifications.get(name).net_class.value
                                            if classifications.get(name) else "UNKNOWN", name))
    return buckets


def fill_missing_classifications(
    classifications: dict[str, NetClassification], index: SheetNetIndex | None
) -> int:
    """原理图网表里有、但 PCB 上没有焊盘的网络，补一条分类记录。

    这类网络没有器件连接（board.nets 里没有），但图纸视图会列出来；
    不补的话表格里类别/参考地全是「—」，整页操作也会跳过它们。
    按当前规则默认归副边，参考地由后续 assign_* 补 SGND。
    """
    if index is None:
        return 0
    added = 0
    for net in index.net_sheets:
        if not net or net in classifications:
            continue
        classifications[net] = NetClassification(
            net=net,
            net_class=NetClass.SECONDARY,
            confidence=0.3,
            reasons=["仅在原理图网表中出现（PCB 无焊盘），按副边处理"],
            needs_review=True,
            auto_class=NetClass.SECONDARY,
        )
        added += 1
    return added


def sheet_display_name(path: str) -> str:
    """``Aux_Power.SchDoc`` -> ``Aux_Power``。"""
    name = (path or "").replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in (".SchDoc", ".schdoc"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name.rsplit(".", 1)[0] if "." in name else name
