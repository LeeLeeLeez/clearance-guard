"""扫描快照：把一次扫描的结果（含人工确认）导出为 JSON，供下次直接导入。

大型工程扫描一次要 1~2 分钟，而复核分类往往要反复进行。快照把这些结果固化下来：
板数据、焊盘、网表引脚、图纸归属、安装焊盘电位、每个网络的电压类别与确认状态。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .plainfile import read_text
from .model import (
    Board,
    BoardCutout,
    Component,
    FloatingPart,
    MountPoint,
    NetClass,
    NetClassification,
    Pad,
    SheetNetIndex,
    Track,
)
from .analysis.classify import (
    CLASSIFIER_VERSION,
    apply_follow_target,
    _apply_zero_ohm_bonds,
    _zero_ohm_bonds,
    assign_primary_references,
    assign_secondary_references,
    classify_board,
    normalize_unknown_to_secondary,
)
from .analysis.sheets import build_sheet_index, fill_missing_classifications

SNAPSHOT_FORMAT = "clearance-guard-snapshot"
SNAPSHOT_VERSION = 1


class SnapshotError(RuntimeError):
    """快照文件损坏或版本不兼容。"""


@dataclass
class ScanSnapshot:
    board: Board
    classifications: dict[str, NetClassification] = field(default_factory=dict)
    pins: list[dict[str, Any]] = field(default_factory=list)
    sheet_components: dict[str, list[str]] = field(default_factory=dict)
    sheet_paths: dict[str, str] = field(default_factory=dict)
    instance_sheets: dict[str, str] = field(default_factory=dict)
    top_sheet: str = ""
    sheet_index: SheetNetIndex | None = None
    project_path: str = ""
    board_path: str = ""
    created_at: str = ""
    app_version: str = ""
    warnings: list[str] = field(default_factory=list)
    classifier_version: int = 0
    reclassified: bool = False   # 快照来自旧判定规则，导入时已按新规则重算

    @property
    def created_text(self) -> str:
        return self.created_at.replace("T", " ")[:19]


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------
def _component_dict(comp: Component) -> dict[str, Any]:
    return {
        "designator": comp.designator,
        "comment": comp.comment,
        "footprint": comp.footprint,
        "x": comp.x,
        "y": comp.y,
        "rotation": comp.rotation,
        "layer": comp.layer,
        "height": comp.height,
        "pad_count": comp.pad_count,
        "bbox": list(comp.bbox) if comp.bbox else None,
        "category": comp.category,
        "body_bbox": list(comp.body_bbox) if getattr(comp, "body_bbox", None) else None,
    }


def _pad_dict(pad: Pad) -> dict[str, Any]:
    return {
        "name": pad.name,
        "net": pad.net,
        "component": pad.component,
        "x": pad.x,
        "y": pad.y,
        "width": pad.width,
        "height": pad.height,
        "rotation": pad.rotation,
        "shape": pad.shape,
        "layer": pad.layer,
        "hole": pad.hole,
        "plated": pad.plated,
    }


def _track_dict(track: Track) -> dict[str, Any]:
    return {
        "net": track.net,
        "component": track.component,
        "layer": track.layer,
        "x1": track.x1,
        "y1": track.y1,
        "x2": track.x2,
        "y2": track.y2,
        "width": track.width,
    }


def _cutout_dict(cutout) -> dict:
    return {
        "kind": cutout.kind,
        "x": cutout.x,
        "y": cutout.y,
        "width": cutout.width,
        "height": cutout.height,
        "name": cutout.name,
        "role": cutout.role,
        "points": [list(p) for p in cutout.points],
    }


def _floating_dict(part) -> dict:
    return {
        "designator": part.designator,
        "comment": part.comment,
        "footprint": part.footprint,
        "x": part.x,
        "y": part.y,
        "width": part.width,
        "height": part.height,
        "layer": part.layer,
        "pad_count": part.pad_count,
        "net_class": part.net_class.value,
        "voltage": part.voltage,
        "voltage_min": part.voltage_min,
        "voltage_max": part.voltage_max,
        "enabled": part.enabled,
        "note": part.note,
    }


def _mount_dict(mount: MountPoint) -> dict[str, Any]:
    return {
        "name": mount.name,
        "x": mount.x,
        "y": mount.y,
        "width": mount.width,
        "height": mount.height,
        "hole": mount.hole,
        "net": mount.net,
        "layer": mount.layer,
        "net_class": mount.net_class.value,
        "voltage": mount.voltage,
        "voltage_min": mount.voltage_min,
        "voltage_max": mount.voltage_max,
        "enabled": mount.enabled,
        "note": mount.note,
    }


def _classification_dict(item: NetClassification) -> dict[str, Any]:
    return {
        "net": item.net,
        "net_class": item.net_class.value,
        "voltage": item.voltage,
        "voltage_min": item.voltage_min,
        "voltage_max": item.voltage_max,
        "confidence": item.confidence,
        "reasons": list(item.reasons),
        "confirmed": item.confirmed,
        "needs_review": item.needs_review,
        "auto_class": item.auto_class.value,
        "is_drive": item.is_drive,
        "ref_ground": item.ref_ground,
        "ref_ground_reason": item.ref_ground_reason,
        "local_voltage_min": item.local_voltage_min,
        "local_voltage_max": item.local_voltage_max,
        "reference_applied": item.reference_applied,
        "reference_base_class": item.reference_base_class.value,
        "user_edited": item.user_edited,
        "sheet_side": item.sheet_side,
        "follow_net": item.follow_net,
    }


def build_snapshot(
    board: Board,
    classifications: dict[str, NetClassification],
    pins: list[dict[str, Any]],
    sheet_components: dict[str, list[str]],
    sheet_paths: dict[str, str],
    instance_sheets: dict[str, str],
    top_sheet: str,
    project_path: str = "",
    app_version: str = "",
) -> dict[str, Any]:
    return {
        "format": SNAPSHOT_FORMAT,
        "version": SNAPSHOT_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "app_version": app_version,
        "classifier": CLASSIFIER_VERSION,
        "project": {"path": project_path, "board_path": board.source_path},
        "board": {
            "name": board.name,
            "width": board.width,
            "height": board.height,
            "nets": list(board.nets),
            "source_path": board.source_path,
            "source_format": board.source_format,
            "warnings": list(board.warnings),
            "components": [_component_dict(c) for c in board.components.values()],
            "pads": [_pad_dict(p) for p in board.pads],
            "tracks": [_track_dict(t) for t in board.tracks],
            "mounts": [_mount_dict(m) for m in board.mounts],
            "floating_parts": [_floating_dict(f) for f in getattr(board, "floating_parts", [])],
            "cutouts": [_cutout_dict(c) for c in getattr(board, "cutouts", [])],
            "outline": [[round(x, 4), round(y, 4)] for x, y in getattr(board, "outline", [])],
            "outline_version": int(getattr(board, "outline_version", 0)),
        },
        "pins": pins,
        "sheets": {
            "components": sheet_components,
            "paths": sheet_paths,
            "instance_sheets": instance_sheets,
            "top_sheet": top_sheet,
        },
        "classifications": [_classification_dict(v) for v in classifications.values()],
    }


def save_snapshot(path: str | Path, **kwargs: Any) -> Path:
    payload = build_snapshot(**kwargs)
    target = Path(path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    return target


# --------------------------------------------------------------------------
# 反序列化
# --------------------------------------------------------------------------
def _net_class(value: Any) -> NetClass:
    try:
        return NetClass(str(value))
    except ValueError:
        return NetClass.UNKNOWN


def _opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _board_from_dict(data: dict[str, Any]) -> Board:
    board = Board(
        name=str(data.get("name") or "PCB"),
        width=float(data.get("width") or 0.0),
        height=float(data.get("height") or 0.0),
        nets=[str(n) for n in (data.get("nets") or []) if str(n)],
        source_path=str(data.get("source_path") or ""),
        source_format=str(data.get("source_format") or ""),
        warnings=[str(w) for w in (data.get("warnings") or [])],
    )
    for item in data.get("components") or []:
        if not isinstance(item, dict):
            continue
        board.add_component(
            Component(
                designator=str(item.get("designator") or ""),
                comment=str(item.get("comment") or ""),
                footprint=str(item.get("footprint") or ""),
                x=float(item.get("x") or 0.0),
                y=float(item.get("y") or 0.0),
                rotation=float(item.get("rotation") or 0.0),
                layer=str(item.get("layer") or "TOP"),
                height=float(item.get("height") or 0.0),
                pad_count=int(item.get("pad_count") or 0),
                category=str(item.get("category") or ""),
                body_bbox=(
                    tuple(float(v) for v in item["body_bbox"])
                    if item.get("body_bbox")
                    else None
                ),
                bbox=(
                    tuple(float(v) for v in item["bbox"])
                    if isinstance(item.get("bbox"), (list, tuple)) and len(item["bbox"]) == 4
                    else None
                ),
            )
        )
    for item in data.get("pads") or []:
        if not isinstance(item, dict):
            continue
        board.add_pad(
            Pad(
                name=str(item.get("name") or ""),
                net=str(item.get("net") or ""),
                component=str(item.get("component") or ""),
                x=float(item.get("x") or 0.0),
                y=float(item.get("y") or 0.0),
                width=max(float(item.get("width") or 0.01), 0.001),
                height=max(float(item.get("height") or 0.01), 0.001),
                rotation=float(item.get("rotation") or 0.0),
                shape=str(item.get("shape") or "rect"),
                layer=str(item.get("layer") or "TOP"),
                hole=float(item.get("hole") or 0.0),
                plated=bool(item.get("plated", True)),
            )
        )
    for item in data.get("tracks") or []:
        if not isinstance(item, dict):
            continue
        board.tracks.append(
            Track(
                net=str(item.get("net") or ""),
                component=str(item.get("component") or ""),
                layer=str(item.get("layer") or "TOP"),
                x1=float(item.get("x1") or 0.0),
                y1=float(item.get("y1") or 0.0),
                x2=float(item.get("x2") or 0.0),
                y2=float(item.get("y2") or 0.0),
                width=float(item.get("width") or 0.2),
            )
        )
    for item in data.get("mounts") or []:
        if not isinstance(item, dict):
            continue
        board.mounts.append(
            MountPoint(
                name=str(item.get("name") or ""),
                x=float(item.get("x") or 0.0),
                y=float(item.get("y") or 0.0),
                width=float(item.get("width") or 4.0),
                height=float(item.get("height") or 4.0),
                hole=float(item.get("hole") or 0.0),
                net=str(item.get("net") or ""),
                layer=str(item.get("layer") or "MULTI"),
                net_class=_net_class(item.get("net_class")),
                voltage=_opt_float(item.get("voltage")),
                voltage_min=_opt_float(item.get("voltage_min")),
                voltage_max=_opt_float(item.get("voltage_max")),
                enabled=bool(item.get("enabled", True)),
                note=str(item.get("note") or ""),
            )
        )

    for mount in board.mounts:
        if mount.net_class is NetClass.UNKNOWN:
            mount.net_class = NetClass.SECONDARY
            mount.note = mount.note or "旧数据：未指定电位，按副边处理"

    board.cutouts = [
        BoardCutout(
            kind=str(item.get("kind") or "circle"),
            x=float(item.get("x") or 0.0),
            y=float(item.get("y") or 0.0),
            width=float(item.get("width") or 0.0),
            height=float(item.get("height") or 0.0),
            name=str(item.get("name") or ""),
            role=str(item.get("role") or ""),
            points=[
                (float(p[0]), float(p[1]))
                for p in (item.get("points") or [])
                if isinstance(p, (list, tuple)) and len(p) == 2
            ],
        )
        for item in (data.get("cutouts") or [])
        if isinstance(item, dict)
    ]

    board.outline_version = int(data.get("outline_version") or 0)
    board.outline = [
        (float(p[0]), float(p[1]))
        for p in (data.get("outline") or [])
        if isinstance(p, (list, tuple)) and len(p) == 2
    ]

    board.floating_parts = []
    for item in data.get("floating_parts") or []:
        if not isinstance(item, dict):
            continue
        board.floating_parts.append(
            FloatingPart(
                designator=str(item.get("designator") or ""),
                comment=str(item.get("comment") or ""),
                footprint=str(item.get("footprint") or ""),
                x=float(item.get("x") or 0.0),
                y=float(item.get("y") or 0.0),
                width=float(item.get("width") or 0.0),
                height=float(item.get("height") or 0.0),
                layer=str(item.get("layer") or "TOP"),
                pad_count=int(item.get("pad_count") or 0),
                net_class=_net_class(item.get("net_class")),
                voltage=_opt_float(item.get("voltage")),
                voltage_min=_opt_float(item.get("voltage_min")),
                voltage_max=_opt_float(item.get("voltage_max")),
                enabled=bool(item.get("enabled", True)),
                note=str(item.get("note") or ""),
            )
        )
    return board


def _classifications_from_list(items: list[dict[str, Any]]) -> dict[str, NetClassification]:
    result: dict[str, NetClassification] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        net = str(item.get("net") or "")
        if not net:
            continue
        result[net] = NetClassification(
            net=net,
            net_class=_net_class(item.get("net_class")),
            voltage=_opt_float(item.get("voltage")),
            voltage_min=_opt_float(item.get("voltage_min")),
            voltage_max=_opt_float(item.get("voltage_max")),
            confidence=float(item.get("confidence") or 0.0),
            reasons=[str(r) for r in (item.get("reasons") or [])],
            confirmed=bool(item.get("confirmed", False)),
            needs_review=bool(item.get("needs_review", False)),
            auto_class=_net_class(item.get("auto_class")),
            is_drive=bool(item.get("is_drive", False)),
            ref_ground=str(item.get("ref_ground") or ""),
            ref_ground_reason=str(item.get("ref_ground_reason") or ""),
            local_voltage_min=_opt_float(item.get("local_voltage_min")),
            local_voltage_max=_opt_float(item.get("local_voltage_max")),
            reference_applied=bool(item.get("reference_applied", False)),
            reference_base_class=_net_class(item.get("reference_base_class")),
            user_edited=bool(item.get("user_edited", False)),
            sheet_side=str(item.get("sheet_side") or ""),
            follow_net=str(item.get("follow_net") or ""),
        )
    return result


def load_snapshot(path: str | Path, reclassify: bool = True) -> ScanSnapshot:
    target = Path(path)
    try:
        raw = read_text(target)
    except OSError as exc:
        raise SnapshotError(f"无法读取快照文件：{exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"快照文件不是有效的 JSON：{exc}") from exc

    if not isinstance(data, dict) or data.get("format") != SNAPSHOT_FORMAT:
        raise SnapshotError("这不是 Clearance Guard 的扫描快照文件")
    version = int(data.get("version") or 0)
    if version > SNAPSHOT_VERSION:
        raise SnapshotError(
            f"快照版本 {version} 高于当前程序支持的版本 {SNAPSHOT_VERSION}，请升级工具"
        )

    board_data = data.get("board")
    if not isinstance(board_data, dict):
        raise SnapshotError("快照缺少 board 数据")
    board = _board_from_dict(board_data)

    sheets = data.get("sheets") if isinstance(data.get("sheets"), dict) else {}
    sheet_components = {
        str(name): [str(d) for d in (designators or [])]
        for name, designators in (sheets.get("components") or {}).items()
    }
    sheet_paths = {
        str(name): str(path_value)
        for name, path_value in (sheets.get("paths") or {}).items()
    }
    instance_sheets = {
        str(instance): str(sheet)
        for instance, sheet in (sheets.get("instance_sheets") or {}).items()
    }
    top_sheet = str(sheets.get("top_sheet") or "")
    pins = [p for p in (data.get("pins") or []) if isinstance(p, dict)]
    classifications = _classifications_from_list(data.get("classifications") or [])

    stored_classifier = int(data.get("classifier") or 0)
    reclassified = False
    if reclassify and stored_classifier < CLASSIFIER_VERSION and board.nets:
        # 判定规则升级后，旧快照里的类别已经过时：按新规则重算，避免看到旧结论
        try:
            classifications = classify_board(board, pins)
            reclassified = True
        except Exception:  # noqa: BLE001 - 重算失败就退回快照里的结果
            pass

    normalize_unknown_to_secondary(classifications)
    try:
        _apply_zero_ohm_bonds(classifications, _zero_ohm_bonds(board))
    except Exception:  # noqa: BLE001 - 旧数据异常不影响载入
        pass
    assign_secondary_references(classifications)
    assign_primary_references(classifications)
    apply_follow_target(classifications)

    # 板上有、快照里没有的网络（工程改动后新增）：按当前逻辑补一份自动分类
    missing = [net for net in board.nets if net and net not in classifications]
    if missing:
        try:
            auto = classify_board(board, pins)
        except Exception:  # noqa: BLE001 - 失败就保持缺失，界面上按「未定」处理
            auto = {}
        for net in missing:
            if net in auto:
                classifications[net] = auto[net]

    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    snapshot = ScanSnapshot(
        board=board,
        classifications=classifications,
        pins=pins,
        sheet_components=sheet_components,
        sheet_paths=sheet_paths,
        instance_sheets=instance_sheets,
        top_sheet=top_sheet,
        project_path=str(project.get("path") or ""),
        board_path=str(project.get("board_path") or board.source_path),
        created_at=str(data.get("created_at") or ""),
        app_version=str(data.get("app_version") or ""),
        warnings=list(board.warnings),
        classifier_version=stored_classifier,
        reclassified=reclassified,
    )
    if sheet_components:
        snapshot.sheet_index = build_sheet_index(
            pins, sheet_components, board, sheet_paths, instance_sheets, top_sheet
        )
    else:
        snapshot.sheet_index = SheetNetIndex()
    fill_missing_classifications(snapshot.classifications, snapshot.sheet_index)
    assign_secondary_references(snapshot.classifications)
    assign_primary_references(snapshot.classifications)
    return snapshot


def default_snapshot_path() -> Path:
    base = Path(os.getenv("APPDATA", str(Path.home()))) / "ClearanceGuard"
    return base / "last_scan.json"
