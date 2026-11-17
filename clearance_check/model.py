"""核心数据模型：工程 -> 板 -> 网络 / 器件 / 焊盘 / 走线。

坐标单位统一为毫米 (mm)，板坐标系与 Altium 一致（Y 轴向上）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable
from enum import Enum


class NetClass(str, Enum):
    """网络的电压类别。"""

    PRIMARY_CONST = "PRIMARY_CONST"
    PRIMARY_SWITCHING = "PRIMARY_SWITCHING"
    SECONDARY = "SECONDARY"
    UNKNOWN = "UNKNOWN"


NET_CLASS_LABELS: dict[NetClass, str] = {
    NetClass.PRIMARY_CONST: "原边 · 恒定电平",
    NetClass.PRIMARY_SWITCHING: "原边 · 跳变电平",
    NetClass.SECONDARY: "副边",
    NetClass.UNKNOWN: "未定",
}

NET_CLASS_SHORT_LABELS: dict[NetClass, str] = {
    NetClass.PRIMARY_CONST: "原边",
    NetClass.PRIMARY_SWITCHING: "原边跳变",
    NetClass.SECONDARY: "副边",
    NetClass.UNKNOWN: "未定",
}

#: 界面上可选的类别（「未定」只作为兼容旧数据的内部值，不再作为可选分类）
ORDERED_NET_CLASSES: list[NetClass] = [
    NetClass.PRIMARY_CONST,
    NetClass.PRIMARY_SWITCHING,
    NetClass.SECONDARY,
]


def net_class_label(net_class: NetClass) -> str:
    return NET_CLASS_LABELS.get(net_class, str(net_class))


def net_class_from_label(text: str) -> NetClass:
    """把界面上的中文标签或枚举值还原成 NetClass。"""
    text = (text or "").strip()
    for net_class, label in NET_CLASS_LABELS.items():
        if text == label or text == net_class.value:
            return net_class
    for net_class, label in {**NET_CLASS_LABELS, **NET_CLASS_SHORT_LABELS}.items():
        if text == label:
            return net_class
    return NetClass.UNKNOWN


@dataclass
class Pad:
    """焊盘。"""

    index: int = 0
    name: str = ""                      # 焊盘编号，如 "1" / "A1"
    net: str = ""                       # 所属网络名
    component: str = ""                 # 所属器件位号，自由焊盘为空
    x: float = 0.0
    y: float = 0.0
    width: float = 1.0                  # X 方向尺寸 (mm)
    height: float = 1.0                 # Y 方向尺寸 (mm)
    rotation: float = 0.0               # 逆时针角度 (deg)
    shape: str = "rect"                 # rect / round / octagon
    layer: str = "TOP"                  # TOP / BOTTOM / MULTI
    hole: float = 0.0
    plated: bool = True

    @property
    def is_smd(self) -> bool:
        return self.hole <= 0.0


@dataclass
class Track:
    """走线（线段 + 线宽，可视为胶囊体）。"""

    net: str = ""
    component: str = ""
    layer: str = "TOP"
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    width: float = 0.2


@dataclass
class Component:
    """器件。"""

    designator: str = ""
    comment: str = ""
    footprint: str = ""
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    layer: str = "TOP"
    height: float = 0.0
    pad_count: int = 0
    #: 器件外形包围盒 (x1, y1, x2, y2)，单位 mm，来自 PCB 桥接读取
    bbox: tuple[float, float, float, float] | None = None
    #: 类别：""=普通器件，"burn_terminal"=烧录端子（半孔，靠近板框不算违规）
    category: str = ""
    #: 3D 本体（ComponentBody）投影外框 (x1, y1, x2, y2)，mm；没有 3D 体则为 None
    body_bbox: tuple[float, float, float, float] | None = None

    @property
    def is_burn_terminal(self) -> bool:
        return self.category == "burn_terminal"

    @property
    def bbox_size(self) -> tuple[float, float] | None:
        if not self.bbox:
            return None
        x1, y1, x2, y2 = self.bbox
        return (abs(x2 - x1), abs(y2 - y1))

    @property
    def bbox_center(self) -> tuple[float, float] | None:
        if not self.bbox:
            return None
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @property
    def prefix(self) -> str:
        m = re.match(r"^([A-Za-z_]+)", self.designator or "")
        return (m.group(1) if m else "").upper()


@dataclass
class NetStats:
    """网络的连接统计，供电压判定使用。"""

    name: str
    pads: list[Pad] = field(default_factory=list)
    components: set[str] = field(default_factory=set)
    tracks: int = 0

    @property
    def pad_count(self) -> int:
        return len(self.pads)


@dataclass
class MountPoint:
    """安装焊盘 / 安装孔：连接到机械结构，电位由用户指定。"""

    name: str = ""
    x: float = 0.0
    y: float = 0.0
    width: float = 4.0
    height: float = 4.0
    hole: float = 0.0
    net: str = "SGND"        # 指派网络：默认为信号地 SGND（副边）
    layer: str = "MULTI"
    net_class: NetClass = NetClass.SECONDARY   # 机械结构默认按副边处理
    voltage: float | None = None
    voltage_min: float | None = None
    voltage_max: float | None = None
    enabled: bool = True
    note: str = ""

    @property
    def label(self) -> str:
        return self.name or f"安装焊盘({self.x:.1f}, {self.y:.1f})"

    @property
    def assigned(self) -> bool:
        # 「已指派」要同时满足：启用、网络非空、电位不是「未定」
        return (
            self.enabled
            and bool((self.net or "").strip())
            and self.net_class is not NetClass.UNKNOWN
        )


@dataclass
class BoardCutout:
    """板级挖空：圆形（安装孔）或多边形（避距槽）。"""

    kind: str = "circle"          # circle / slot
    x: float = 0.0                # 中心
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    name: str = ""
    #: 归属：frame=板框外形 / footprint=器件封装 / creepage=爬电隔离槽
    role: str = ""
    #: 闭合轮廓顶点（mm），点击「读取轮廓坐标」后从 PCB 读取
    points: list[tuple[float, float]] = field(default_factory=list)

    @property
    def diameter(self) -> float:
        return max(self.width, self.height)

    @property
    def is_circle(self) -> bool:
        return self.kind == "circle"

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        half_w, half_h = self.width / 2, self.height / 2
        return (self.x - half_w, self.y - half_h, self.x + half_w, self.y + half_h)


@dataclass
class FloatingPart:
    """无网络器件：器件上没有任何网络（磁芯 / 结构件 / 未连线的焊盘）。

    它们的电位不由网络决定，需要人工指定后才能参与避距检查。
    """

    designator: str = ""
    comment: str = ""
    footprint: str = ""
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0     # 由 PCB 桥接读取的包围盒尺寸（mm）
    height: float = 0.0
    layer: str = "TOP"
    pad_count: int = 0
    net_class: NetClass = NetClass.UNKNOWN
    voltage: float | None = None
    voltage_min: float | None = None
    voltage_max: float | None = None
    enabled: bool = True
    note: str = ""

    @property
    def label(self) -> str:
        return self.designator or f"({self.x:.1f}, {self.y:.1f})"

    @property
    def assigned(self) -> bool:
        return self.enabled and self.net_class is not NetClass.UNKNOWN


@dataclass
class Board:
    """一块 PCB 的抽取结果。"""

    name: str = "PCB"
    width: float = 0.0
    height: float = 0.0
    nets: list[str] = field(default_factory=list)
    components: dict[str, Component] = field(default_factory=dict)
    pads: list[Pad] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    mounts: list["MountPoint"] = field(default_factory=list)
    floating_parts: list["FloatingPart"] = field(default_factory=list)
    cutouts: list["BoardCutout"] = field(default_factory=list)
    #: 板框外形多边形（mm），来自 PCB 板框对象 / HTML 记录，圆弧已离散
    outline: list[tuple[float, float]] = field(default_factory=list)
    #: 板框解析版本：0/1 = 旧算法（弧向有误），2 = 按 SVG 规范解析
    outline_version: int = 0
    warnings: list[str] = field(default_factory=list)
    source_path: str = ""
    source_format: str = ""

    # ---- 基础操作 -------------------------------------------------
    def ensure_net(self, name: str) -> str:
        name = (name or "").strip()
        if name and name not in self.nets:
            self.nets.append(name)
        return name

    def add_pad(self, pad: Pad) -> Pad:
        pad.index = len(self.pads)
        if pad.net:
            self.ensure_net(pad.net)
        if pad.component and pad.component not in self.components:
            self.components[pad.component] = Component(designator=pad.component)
        self.pads.append(pad)
        return pad

    def add_component(self, comp: Component) -> Component:
        self.components[comp.designator] = comp
        return comp

    # ---- 统计 -----------------------------------------------------
    def net_stats(self) -> dict[str, NetStats]:
        stats: dict[str, NetStats] = {name: NetStats(name) for name in self.nets}
        for pad in self.pads:
            if not pad.net:
                continue
            st = stats.setdefault(pad.net, NetStats(pad.net))
            st.pads.append(pad)
            if pad.component:
                st.components.add(pad.component)
        for track in self.tracks:
            if not track.net:
                continue
            stats.setdefault(track.net, NetStats(track.net)).tracks += 1
        return stats

    def nets_of_component(self, designator: str) -> set[str]:
        if not designator:
            return set()
        return {p.net for p in self.pads if p.component == designator and p.net}

    def pads_of_component(self, designator: str) -> list[Pad]:
        return [p for p in self.pads if p.component == designator]

    def pads_of_net(self, net: str) -> list[Pad]:
        return [p for p in self.pads if p.net == net]

    def component_bbox(self, designator: str) -> tuple[float, float, float, float] | None:
        pads = self.pads_of_component(designator)
        if pads:
            xs, ys = [], []
            for pad in pads:
                r = max(pad.width, pad.height) / 2.0
                xs += [pad.x - r, pad.x + r]
                ys += [pad.y - r, pad.y + r]
            return min(xs), min(ys), max(xs), max(ys)
        comp = self.components.get(designator)
        if comp:
            # 优先用桥接读到的器件外框（磁芯等无焊盘器件也能拿到真实外形）
            if comp.bbox:
                return comp.bbox
            return comp.x - 1.0, comp.y - 1.0, comp.x + 1.0, comp.y + 1.0
        return None

    def board_bbox(self) -> tuple[float, float, float, float]:
        xs: list[float] = []
        ys: list[float] = []
        for point_x, point_y in self.outline:
            xs.append(point_x)
            ys.append(point_y)
        if self.width > 0 and self.height > 0:
            xs += [0.0, self.width]
            ys += [0.0, self.height]
        for pad in self.pads:
            r = max(pad.width, pad.height) / 2.0
            xs += [pad.x - r, pad.x + r]
            ys += [pad.y - r, pad.y + r]
        if not xs:
            return -50.0, -50.0, 50.0, 50.0
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def outline_size(self) -> tuple[float, float] | None:
        if len(self.outline) < 3:
            return None
        x0, y0, x1, y1 = self.board_bbox()
        return (x1 - x0, y1 - y0)


@dataclass
class NetClassification:
    """单个网络的电压类别判定结果。"""

    net: str
    net_class: NetClass = NetClass.UNKNOWN
    voltage: float | None = None        # 标称电压 (V)
    voltage_min: float | None = None    # 电压下限，跳变网络为谷值
    voltage_max: float | None = None    # 电压上限，跳变网络为峰值
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    confirmed: bool = False
    needs_review: bool = False              # 置信度低 / 电压不明，需要人工指定
    auto_class: NetClass = NetClass.UNKNOWN  # 自动判定结果，便于对比人工修改
    is_drive: bool = False                  # 驱动网络（栅极驱动 / 驱动供电）
    ref_ground: str = ""                    # 驱动网络的参考地网络名
    ref_ground_reason: str = ""
    local_voltage_min: float | None = None  # 相对参考地的本地电平（下限）
    local_voltage_max: float | None = None  # 相对参考地的本地电平（上限）
    reference_applied: bool = False         # voltage_min/max 是否已折算为绝对电位
    reference_base_class: NetClass = NetClass.UNKNOWN  # 折算前的类别，用于回退
    user_edited: bool = False               # 用户手工改过（置信度显示绿色对勾）
    sheet_side: str = ""                    # 人工指定在 common 中的侧别：power / control
    follow_net: str = ""                    # 人工指定跟随的网络（电压与参考地完全跟随）

    @property
    def is_switching(self) -> bool:
        return self.net_class is NetClass.PRIMARY_SWITCHING

    def normalized_range(self) -> tuple[float | None, float | None]:
        """返回 (下限, 上限)。人工填写可能上下颠倒，这里统一纠正。"""
        lo, hi = self.voltage_min, self.voltage_max
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        return lo, hi

    @property
    def range_text(self) -> str:
        lo, hi = self.normalized_range()
        if lo is None and hi is None:
            return "—"
        if lo is None or hi is None or abs(hi - lo) < 1e-6:
            return f"{(hi if hi is not None else lo):.1f} V"
        return f"{lo:.1f} ~ {hi:.1f} V"

    def peak_voltage(self) -> float:
        _lo, hi = self.normalized_range()
        if hi is not None:
            return float(hi)
        if self.voltage is not None:
            return float(self.voltage)
        return 0.0


#: Altium 自动生成的网络名（NetD10_1 / NetJP3_2 / NetP1_15 …）
AUTO_NET_RE = re.compile(r"^Net[A-Za-z]{1,4}\d+(?:_\d+)?$", re.I)


def is_auto_named(net: str) -> bool:
    """网络名是否是 Altium 自动生成的（没有人工命名含义）。

    除 ``NetD10_1`` 这类，还包含多通道实例化产生的 ``NetXXX_U_SR_Cout13_1``。
    """
    name = (net or "").strip()
    if not name:
        return False
    if "_U_" in name.upper():
        return True
    return bool(AUTO_NET_RE.match(name))


def named_net_candidates(names: "Iterable[str]") -> list[str]:
    """可作为「跟随对象」的网络：只保留有人工命名的网络。"""
    return sorted({name for name in names if name and not is_auto_named(name)})


def default_secondary_ground(names: "Iterable[str]") -> str:
    """副边网络默认参考地：优先名字就是 SGND，其次含 SGND 的网络。"""
    candidates = [name for name in names if name]
    for name in candidates:
        if name.upper() == "SGND":
            return name
    for name in candidates:
        if "SGND" in name.upper():
            return name
    return ""


def default_primary_ground(names: "Iterable[str]") -> str:
    """原边网络默认参考地：优先名字就是 PGND，其次含 PGND 的网络。"""
    candidates = [name for name in names if name]
    for name in candidates:
        if name.upper() == "PGND":
            return name
    for name in candidates:
        if "PGND" in name.upper():
            return name
    return ""


def editable_voltage_columns(net_class: NetClass) -> set[int]:
    """哪些电压列可编辑：0=电压等级，1=下限，2=上限。

    恒定电平只填电压等级；跳变电平只填上下限；未定三个都能填。
    """
    if net_class is NetClass.PRIMARY_SWITCHING:
        return {1, 2}
    if net_class is NetClass.UNKNOWN:
        return {0, 1, 2}
    return {0}


def normalize_voltage_fields(item: "NetClassification") -> None:
    """按类别把电压字段整理成一致的形态。

    恒定电平：上下限 = 电压等级；跳变电平：电压等级 = 上限（峰值）。
    """
    if item.net_class is NetClass.UNKNOWN:
        return
    if item.net_class is NetClass.PRIMARY_SWITCHING:
        if item.voltage_min is None and item.voltage_max is None:
            peak = item.voltage
            item.voltage_min = 0.0 if peak is not None else None
            item.voltage_max = peak
        elif item.voltage_max is None:
            item.voltage_max = item.voltage_min
        elif item.voltage_min is None:
            item.voltage_min = 0.0
        item.voltage = item.voltage_max
        return

    if item.voltage is None:
        item.voltage = (item.voltage_max
                        if item.voltage_max is not None else item.voltage_min)
    if item.voltage is not None:
        item.voltage_min = item.voltage
        item.voltage_max = item.voltage


def sync_local_voltage(item: "NetClassification",
                       classifications: dict[str, "NetClassification"]) -> None:
    """用户改完显示值后，把本地电平缓存改成与新值一致，避免折算时被旧缓存覆盖。"""
    ref = classifications.get(item.ref_ground) if item.ref_ground else None
    if ref is None or not item.reference_applied:
        item.local_voltage_min = item.voltage_min
        item.local_voltage_max = item.voltage_max
        return

    lo, hi = ref.normalized_range()
    if lo is None and hi is None:
        base = ref.voltage if ref.voltage is not None else 0.0
        lo = hi = base
    elif lo is None:
        lo = hi
    elif hi is None:
        hi = lo

    if item.voltage_min is not None:
        item.local_voltage_min = round(item.voltage_min - lo, 3)
    if item.voltage_max is not None:
        item.local_voltage_max = round(item.voltage_max - hi, 3)


def sync_peak_voltage(item: "NetClassification") -> None:
    """跳变电平改过上下限后，把电压等级跟着峰值更新。"""
    if item.net_class is NetClass.PRIMARY_SWITCHING and item.voltage_max is not None:
        item.voltage = item.voltage_max


def ref_ground_voltage_text(
    ref_net: str, classifications: dict[str, "NetClassification"]
) -> str:
    """参考地网络的电平描述，例如 ``400 ~ 800 V（跳变）``。"""
    if not ref_net:
        return "—"
    item = classifications.get(ref_net)
    if item is None:
        return "—"
    lo, hi = item.normalized_range()
    if lo is not None and hi is not None and hi - lo > 0.01:
        return f"{lo:g} ~ {hi:g} V（跳变）"
    if hi is not None or lo is not None:
        return f"{(hi if hi is not None else lo):g} V"
    if item.voltage is not None:
        return f"{item.voltage:g} V"
    if item.net_class is NetClass.PRIMARY_SWITCHING:
        return "跳变（范围待确认）"
    return "—"


@dataclass
class SheetNetIndex:
    """原理图图纸与网络的对应关系。

    ``sheets`` 保持工程文档顺序；``net_sheets`` 记录每个网络出现在哪些图纸上，
    出现在 2 张及以上图纸的网络即视为跨页网络，统一汇总到 common。
    """

    sheets: dict[str, list[str]] = field(default_factory=dict)
    net_sheets: dict[str, set[str]] = field(default_factory=dict)
    sheet_paths: dict[str, str] = field(default_factory=dict)

    def add_sheet(self, name: str, nets: Iterable[str], path: str = "") -> None:
        ordered: list[str] = []
        seen: set[str] = set()
        for net in nets:
            net = (net or "").strip()
            if not net or net in seen:
                continue
            seen.add(net)
            ordered.append(net)
            self.net_sheets.setdefault(net, set()).add(name)
        self.sheets[name] = ordered
        if path:
            self.sheet_paths[name] = path

    def shared_nets(self, minimum: int = 2) -> list[str]:
        return sorted(
            net for net, sheets in self.net_sheets.items() if len(sheets) >= minimum
        )

    def sheets_of(self, net: str) -> set[str]:
        return self.net_sheets.get(net, set())

    def is_shared(self, net: str) -> bool:
        return len(self.net_sheets.get(net, ())) >= 2

    @property
    def net_count(self) -> int:
        return len(self.net_sheets)

    def summary(self) -> str:
        return f"{len(self.sheets)} 张图纸 · {self.net_count} 个网络 · {len(self.shared_nets())} 个跨页"


@dataclass
class Violation:
    """焊盘/走线级别的避距违规。"""

    net_a: str
    net_b: str
    item_a: str            # 位号或 "走线"
    item_b: str
    pad_a: str = ""
    pad_b: str = ""
    class_a: NetClass = NetClass.UNKNOWN
    class_b: NetClass = NetClass.UNKNOWN
    x: float = 0.0
    y: float = 0.0
    actual_mm: float = 0.0
    required_mm: float = 0.0
    rule_name: str = ""
    ax: float = 0.0          # 最近点 A（用于布局视图连线）
    ay: float = 0.0
    bx: float = 0.0          # 最近点 B
    by: float = 0.0
    severity: str = "error"  # error=不同器件之间；warning=同一器件内部

    @property
    def deficit_mm(self) -> float:
        return max(0.0, self.required_mm - self.actual_mm)

    @property
    def is_warning(self) -> bool:
        return self.severity == "warning"


@dataclass
class ComponentViolation:
    """器件级别的避距违规汇总（同一对器件只保留最严重的一条）。"""

    designator_a: str
    designator_b: str
    net_a: str
    net_b: str
    class_a: NetClass
    class_b: NetClass
    actual_mm: float
    required_mm: float
    x: float
    y: float
    rule_name: str
    count: int = 1
    severity: str = "error"

    @property
    def deficit_mm(self) -> float:
        return max(0.0, self.required_mm - self.actual_mm)

    @property
    def is_warning(self) -> bool:
        return self.severity == "warning"

    @property
    def pair_label(self) -> str:
        return f"{self.designator_a or '走线'} ↔ {self.designator_b or '走线'}"
