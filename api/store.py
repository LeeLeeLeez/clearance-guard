"""路径、全局状态与序列化。

界面重写后唯一的"状态"集中在这里：一次扫描得到的 Board / 分类 / 引脚 / 图纸索引 /
规则 / 违规结果，全部由本模块持有，服务函数只读写它。
"""

from __future__ import annotations

import dataclasses
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 路径
_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent


def web_dist() -> Path:
    return ROOT / "web" / "dist"


def state_dir() -> Path:
    base = Path(os.getenv("APPDATA", str(Path.home()))) / "ClearanceGuard"
    base.mkdir(parents=True, exist_ok=True)
    return base


def rules_path() -> Path:
    return state_dir() / "clearance_rules.json"


def last_scan_path() -> Path:
    return state_dir() / "last_scan.json"


def backup_dir() -> Path:
    return state_dir() / "backup"


def snapshot_dir() -> Path:
    d = ROOT / "json"
    return d if d.is_dir() else state_dir()


# ---------------------------------------------------------------- 状态
@dataclass
class TaskState:
    running: bool = False
    kind: str = ""
    stage: str = ""
    detail: str = ""
    error: str = ""
    finished: bool = False
    cancelled: bool = False
    started_at: float = 0.0
    finished_at: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        start = self.started_at or now
        end = self.finished_at or now
        return {
            "running": self.running,
            "kind": self.kind,
            "stage": self.stage,
            "detail": self.detail,
            "error": self.error,
            "finished": self.finished,
            "cancelled": self.cancelled,
            "elapsed": round(max(0.0, end - start), 1),
        }


@dataclass
class AppState:
    lock: threading.RLock = field(default_factory=threading.RLock)

    session: Any = None
    connected: bool = False
    connection_error: str = ""

    board: Any = None
    classifications: dict = field(default_factory=dict)
    pins: list = field(default_factory=list)
    sheet_index: Any = None
    sheet_components: dict = field(default_factory=dict)
    sheet_paths: dict = field(default_factory=dict)
    instance_sheets: dict = field(default_factory=dict)
    top_sheet: str = ""

    rules: Any = None
    violations: list = field(default_factory=list)
    aggregated: list = field(default_factory=list)

    #: 配置基线：上次扫描 / 导入快照的分类与安装焊盘电位。
    #: 重新扫描时优先沿用（只对新增网络自动分类），避免人工确认结果被覆盖。
    baseline: dict = field(default_factory=dict)
    baseline_mounts: list = field(default_factory=list)
    baseline_name: str = ""

    project_path: str = ""
    source_note: str = ""
    demo_mode: bool = False
    scanned_at: str = ""
    logs: list = field(default_factory=list)

    task: TaskState = field(default_factory=TaskState)
    stop_flag: bool = False

    def log(self, line: str) -> None:
        with self.lock:
            self.logs.append(line)
            if len(self.logs) > 4000:
                del self.logs[:1000]

    @property
    def scanned(self) -> bool:
        return self.board is not None


STATE = AppState()


# ---------------------------------------------------------------- 序列化
def enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [enum_value(v) for v in value]
    if isinstance(value, dict):
        return {k: enum_value(v) for k, v in value.items()}
    return value


def as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if dataclasses.is_dataclass(obj):
        return {k: enum_value(v) for k, v in dataclasses.asdict(obj).items()}
    raise TypeError(type(obj))


def _labels() -> dict[str, str]:
    from clearance_check.model import NET_CLASS_LABELS

    return {k.value: v for k, v in NET_CLASS_LABELS.items()}


def class_to_dict(item: Any) -> dict[str, Any]:
    """NetClassification → 前端表格一行。"""
    from clearance_check.model import ref_ground_voltage_text

    d = as_dict(item)
    labels = _labels()
    d["class_label"] = labels.get(str(d.get("net_class")), str(d.get("net_class")))
    d["auto_class_label"] = labels.get(str(d.get("auto_class")), str(d.get("auto_class")))
    d["range_text"] = str(getattr(item, "range_text", "") or "")
    try:
        d["ref_ground_voltage"] = ref_ground_voltage_text(str(getattr(item, "ref_ground", "") or ""), STATE.classifications)
    except Exception:  # noqa: BLE001
        d["ref_ground_voltage"] = ""
    return d


def violation_to_dict(item: Any) -> dict[str, Any]:
    d = as_dict(item)
    labels = _labels()
    d["class_a_label"] = labels.get(str(d.get("class_a")), str(d.get("class_a")))
    d["class_b_label"] = labels.get(str(d.get("class_b")), str(d.get("class_b")))
    d["deficit_mm"] = round(float(getattr(item, "deficit_mm", 0.0) or 0.0), 3)
    return d


def component_violation_to_dict(item: Any) -> dict[str, Any]:
    d = as_dict(item)
    labels = _labels()
    d["class_a_label"] = labels.get(str(d.get("class_a")), str(d.get("class_a")))
    d["class_b_label"] = labels.get(str(d.get("class_b")), str(d.get("class_b")))
    d["deficit_mm"] = round(float(getattr(item, "deficit_mm", 0.0) or 0.0), 3)
    return d


def board_summary() -> dict[str, Any]:
    """顶栏「当前设计」：数量 + 各类别统计 + 图纸清单。"""
    from clearance_check.model import NetClass

    with STATE.lock:
        board = STATE.board
        classes = dict(STATE.classifications)
        index = STATE.sheet_index

    if board is None:
        return {"scanned": False}

    counts = {c.value: 0 for c in NetClass}
    for item in classes.values():
        key = getattr(item.net_class, "value", str(item.net_class))
        counts[key] = counts.get(key, 0) + 1

    sheets: list[dict[str, Any]] = []
    if index is not None:
        for name in getattr(index, "sheets", {}) or {}:
            sheets.append(
                {
                    "name": name,
                    "nets": len((index.sheets or {}).get(name, []) or []),
                    "shared": len(
                        [
                            n
                            for n in ((index.sheets or {}).get(name) or [])
                            if (index.net_sheets or {}).get(n) and len(index.net_sheets[n]) > 1
                        ]
                    ),
                }
            )

    return {
        "scanned": True,
        "board_name": getattr(board, "name", ""),
        "project_path": STATE.project_path,
        "source_note": STATE.source_note,
        "scanned_at": STATE.scanned_at,
        "demo": STATE.demo_mode,
        "counts": {
            "components": len(getattr(board, "components", {}) or {}),
            "pads": len(getattr(board, "pads", []) or []),
            "nets": len(getattr(board, "nets", []) or []),
            "sheets": len(getattr(index, "sheets", {}) or {}) if index else 0,
            "mounts": len(getattr(board, "mounts", []) or []),
            "floating": len(getattr(board, "floating_parts", []) or []),
            "cutouts": len(getattr(board, "cutouts", []) or []),
        },
        "net_classes": counts,
        "sheets": sheets,
        "violations": {
            "total": len(STATE.violations),
            "components": len(STATE.aggregated),
            "errors": len([v for v in STATE.violations if getattr(v, "severity", "") == "error"]),
            "warnings": len([v for v in STATE.violations if getattr(v, "severity", "") != "error"]),
        },
        "warnings": list(getattr(board, "warnings", []) or [])[:30],
    }


def ensure_rules() -> Any:
    """规则：内存里没有就从磁盘加载，再不行用默认值。"""
    from clearance_check.analysis import rules as R

    with STATE.lock:
        if STATE.rules is None:
            STATE.rules = R.ClearanceRules.load(rules_path())
        return STATE.rules


def rules_to_dict() -> dict[str, Any]:
    """矩阵的键是 (NetClass, NetClass) 元组，必须走 rules.to_dict() 才能变 JSON。"""
    return ensure_rules().to_dict()


def apply_rules(patch: dict[str, Any]) -> dict[str, Any]:
    """把前端改的规则并进当前规则并落盘（矩阵按 "A|B" 键合并）。"""
    from clearance_check.analysis.rules import ClearanceRules

    current = ensure_rules().to_dict()
    for key, value in (patch or {}).items():
        if key == "matrix" and isinstance(value, dict):
            merged = dict(current.get("matrix") or {})
            for mk, mv in value.items():
                try:
                    merged[str(mk)] = float(mv)
                except (TypeError, ValueError):
                    continue
            current["matrix"] = merged
        elif key in current:
            current[key] = value

    try:
        rules = ClearanceRules.from_dict(current)
    except Exception as exc:  # noqa: BLE001
        STATE.log(f"规则解析失败：{exc}")
        return rules_to_dict()

    with STATE.lock:
        STATE.rules = rules
    try:
        rules.save(rules_path())
    except Exception as exc:  # noqa: BLE001
        STATE.log(f"规则保存失败：{exc}")
    return rules.to_dict()


def board_for_view() -> dict[str, Any]:
    """布局视图要的数据：板框、器件（含本体框）、焊盘、挖空、违规位置。

    只给绘制必需字段，几千个焊盘也不会把 JSON 撑爆。
    """
    with STATE.lock:
        board = STATE.board
        classes = dict(STATE.classifications)
        violations = list(STATE.aggregated)
        cutouts = list(getattr(board, "cutouts", []) or []) if board else []

    if board is None:
        return {"scanned": False}

    label_of = _labels()

    components = []
    for comp in (getattr(board, "components", {}) or {}).values():
        components.append(
            {
                "designator": comp.designator,
                "comment": comp.comment,
                "footprint": comp.footprint,
                "x": round(comp.x, 3),
                "y": round(comp.y, 3),
                "rotation": comp.rotation,
                "layer": comp.layer,
                "bbox": [round(v, 3) for v in (comp.bbox or ())],
                "body_bbox": [round(v, 3) for v in (comp.body_bbox or ())],
            }
        )

    pads = []
    for pad in getattr(board, "pads", []) or []:
        item = classes.get(pad.net)
        cls = getattr(item.net_class, "value", "") if item else ""
        pads.append(
            {
                "x": round(pad.x, 3),
                "y": round(pad.y, 3),
                "w": round(pad.width, 3),
                "h": round(pad.height, 3),
                "rot": pad.rotation,
                "layer": pad.layer,
                "net": pad.net,
                "comp": pad.component,
                "cls": cls,
                "cls_label": label_of.get(cls, ""),
                "hole": pad.hole,
            }
        )

    marks = [
        {
            "x": round(v.x, 3),
            "y": round(v.y, 3),
            "a": v.designator_a,
            "b": v.designator_b,
            "deficit": round(float(getattr(v, "deficit_mm", 0.0) or 0.0), 3),
            "severity": v.severity,
            # 悬停提示用的明细：网络 / 类别 / 实测与要求 / 依据规则 / 焊盘对数
            "net_a": v.net_a,
            "net_b": v.net_b,
            "class_a": label_of.get(getattr(v.class_a, "value", ""), ""),
            "class_b": label_of.get(getattr(v.class_b, "value", ""), ""),
            "actual": round(float(getattr(v, "actual_mm", 0.0) or 0.0), 3),
            "required": round(float(getattr(v, "required_mm", 0.0) or 0.0), 3),
            "rule": getattr(v, "rule_name", ""),
            "count": int(getattr(v, "count", 1) or 1),
        }
        for v in violations
    ]

    return {
        "scanned": True,
        "board_name": getattr(board, "name", ""),
        "width": round(getattr(board, "width", 0.0), 3),
        "height": round(getattr(board, "height", 0.0), 3),
        "outline": [[round(x, 3), round(y, 3)] for x, y in (getattr(board, "outline", []) or [])],
        "components": components,
        "pads": pads,
        "cutouts": [
            {
                "kind": c.kind,
                "name": getattr(c, "name", ""),
                "role": getattr(c, "role", ""),
                "x": round(c.x, 3),
                "y": round(c.y, 3),
                "w": round(c.width, 3),
                "h": round(c.height, 3),
                "points": [[round(px, 3), round(py, 3)] for px, py in (c.points or [])],
            }
            for c in cutouts
        ],
        "violations": marks,
        "class_labels": label_of,
    }
