"""分类表 / 安装焊盘 / 无网络器件 / 避距槽 / 间距规则 的读取与编辑。"""

from __future__ import annotations

import threading
import time
from typing import Any

from .store import STATE, apply_rules, board_summary, class_to_dict, rules_to_dict
from .scan_service import ServiceError


def _require_board() -> Any:
    if not STATE.scanned:
        raise ServiceError("还没有扫描结果，请先扫描工程或导入快照")
    return STATE.board


def _recompute_references() -> None:
    """改完分类后，让参考地 / 跟随关系 / 绝对电位跟上。"""
    from clearance_check.analysis.classify import (
        apply_follow_target,
        apply_reference_potential,
        assign_primary_references,
        assign_secondary_references,
    )

    classes = STATE.classifications
    try:
        apply_follow_target(classes)
    except Exception as exc:  # noqa: BLE001
        STATE.log(f"跟随关系刷新失败：{exc}")
    try:
        assign_secondary_references(classes)
        assign_primary_references(classes)
    except Exception as exc:  # noqa: BLE001
        STATE.log(f"参考地刷新失败：{exc}")
    try:
        apply_reference_potential(classes)
    except Exception as exc:  # noqa: BLE001
        STATE.log(f"参考电位刷新失败：{exc}")


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ 网络分类
def nets() -> dict[str, Any]:
    from clearance_check.model import ORDERED_NET_CLASSES, NET_CLASS_LABELS

    with STATE.lock:
        board = STATE.board
        classes = dict(STATE.classifications)
        index = STATE.sheet_index

    if board is None:
        return {"scanned": False, "nets": [], "classes": []}

    net_sheets = getattr(index, "net_sheets", {}) or {} if index else {}
    rows = []
    for name, item in classes.items():
        row = class_to_dict(item)
        sheets = list(net_sheets.get(name, []) or [])
        row["sheets"] = sheets
        row["shared"] = len(sheets) > 1
        row["auto_named"] = bool(getattr(item, "net_class", None)) and _is_auto_named(name)
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("net") or ""))

    return {
        "scanned": True,
        "nets": rows,
        "classes": [
            {"value": c.value, "label": NET_CLASS_LABELS.get(c, c.value)}
            for c in ORDERED_NET_CLASSES
        ],
        "summary": board_summary(),
    }


def _is_auto_named(name: str) -> bool:
    try:
        from clearance_check.model import is_auto_named

        return bool(is_auto_named(name))
    except Exception:  # noqa: BLE001
        return False


def update_net(name: str, patch: dict[str, Any]) -> dict[str, Any]:
    from clearance_check.model import (
        NetClass,
        default_primary_ground,
        default_secondary_ground,
        net_class_from_label,
        normalize_voltage_fields,
        sync_local_voltage,
        sync_peak_voltage,
    )

    with STATE.lock:
        item = STATE.classifications.get(name)
    if item is None:
        raise ServiceError(f"找不到网络 {name}")

    if "net_class" in patch and patch["net_class"]:
        item.net_class = net_class_from_label(str(patch["net_class"]))
        item.user_edited = True
        item.confirmed = True
        if item.net_class == NetClass.SECONDARY and not item.ref_ground:
            ground = default_secondary_ground(STATE.classifications)
            if ground:
                item.ref_ground = ground
        if item.net_class == NetClass.PRIMARY_CONST and not item.ref_ground:
            ground = default_primary_ground(STATE.classifications)
            if ground:
                item.ref_ground = ground

    if "voltage" in patch:
        item.voltage = _float(patch.get("voltage"))
        item.user_edited = True
        item.confirmed = True
        try:
            sync_local_voltage(item, peak_follow=True)
        except Exception:  # noqa: BLE001
            pass

    if "voltage_min" in patch or "voltage_max" in patch:
        item.voltage_min = _float(patch.get("voltage_min", item.voltage_min))
        item.voltage_max = _float(patch.get("voltage_max", item.voltage_max))
        item.user_edited = True
        item.confirmed = True
        try:
            sync_peak_voltage(item, local_follow=True)
        except Exception:  # noqa: BLE001
            pass

    if "ref_ground" in patch:
        item.ref_ground = str(patch.get("ref_ground") or "")
        item.user_edited = True
        item.confirmed = True

    if "follow_net" in patch:
        item.follow_net = str(patch.get("follow_net") or "")
        item.user_edited = True

    if "sheet_side" in patch:
        item.sheet_side = str(patch.get("sheet_side") or "")
        item.user_edited = True

    try:
        normalize_voltage_fields(item)
    except Exception:  # noqa: BLE001
        pass

    _recompute_references()
    return class_to_dict(STATE.classifications[name])


def bulk_update(names: list[str], patch: dict[str, Any]) -> dict[str, Any]:
    updated = 0
    errors = []
    for name in names or []:
        try:
            update_net(str(name), patch)
            updated += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    return {"ok": True, "updated": updated, "errors": errors}


def follow_candidates(query: str = "", limit: int = 20) -> dict[str, Any]:
    """「跟随网络」下拉的候选（排除自动命名网络）。"""
    items = []
    for name in sorted(STATE.classifications):
        if _is_auto_named(name):
            continue
        if query and query.lower() not in name.lower():
            continue
        items.append(name)
        if len(items) >= limit:
            break
    return {"items": items}


def reclassify() -> dict[str, Any]:
    """重跑自动分类（清掉人工标记）。"""
    from clearance_check.analysis.classify import classify_board

    board = _require_board()
    with STATE.lock:
        pins = list(STATE.pins)
    fresh = classify_board(board, pins)
    with STATE.lock:
        STATE.classifications = fresh
    _recompute_references()
    STATE.log(f"已重新自动分类：{len(fresh)} 个网络")
    return {"ok": True, "count": len(fresh)}


def set_sheet_secondary(sheet: str) -> dict[str, Any]:
    """整页设为副边（含汇总到 common 的网络）。"""
    from clearance_check.model import NetClass, default_secondary_ground

    index = STATE.sheet_index
    names = list((getattr(index, "sheets", {}) or {}).get(sheet, []) or [])
    ground = default_secondary_ground(STATE.classifications)
    changed = 0
    for name in names:
        item = STATE.classifications.get(name)
        if item is None:
            continue
        item.net_class = NetClass.SECONDARY
        item.voltage = 0.0
        item.voltage_min = 0.0
        item.voltage_max = 0.0
        if ground:
            item.ref_ground = ground
        item.user_edited = True
        item.confirmed = True
        changed += 1
    _recompute_references()
    STATE.log(f"图纸 {sheet}：{changed} 个网络设为副边")
    return {"ok": True, "changed": changed}


def route_common(names: list[str], side: str) -> dict[str, Any]:
    """把跨页网络移到功率侧 / 控制侧。"""
    changed = 0
    for name in names or []:
        item = STATE.classifications.get(str(name))
        if item is None:
            continue
        item.sheet_side = "power" if side == "power" else "control"
        item.user_edited = True
        changed += 1
    STATE.log(f"已把 {changed} 个跨页网络移到{'功率侧' if side == 'power' else '控制侧'}")
    return {"ok": True, "changed": changed}


def sheets() -> dict[str, Any]:
    """图纸结构：每张图纸的网络 + common 分组。"""
    index = STATE.sheet_index
    if index is None:
        return {"sheets": [], "common": {"power": [], "control": []}, "shared": []}

    sheets_map = getattr(index, "sheets", {}) or {}
    net_sheets = getattr(index, "net_sheets", {}) or {}
    sheet_paths = getattr(index, "sheet_paths", {}) or {}
    if not sheet_paths and STATE.sheet_paths:
        sheet_paths = STATE.sheet_paths

    shared = [n for n, s in net_sheets.items() if len(s or []) > 1]

    def side_of(net: str) -> str:
        item = STATE.classifications.get(net)
        explicit = getattr(item, "sheet_side", "") if item else ""
        if explicit in ("power", "control"):
            return explicit
        try:
            from clearance_check.analysis.sheets import net_side

            value = net_side(net, STATE.board, item, STATE.pins)
            if isinstance(value, str) and value in ("power", "control"):
                return value
        except Exception:  # noqa: BLE001
            pass
        return "control"

    power = sorted([n for n in shared if side_of(n) == "power"])
    control = sorted([n for n in shared if side_of(n) == "control"])

    out = []
    for name in sheets_map:
        nets_here = list(sheets_map.get(name) or [])
        own = [n for n in nets_here if n not in shared]
        out.append(
            {
                "name": name,
                "path": sheet_paths.get(name, ""),
                "paths": sheet_paths.get(name, ""),
                "nets": own,
                "shared_count": len([n for n in nets_here if n in shared]),
                "total": len(nets_here),
            }
        )
    return {"sheets": out, "shared": sorted(shared), "common": {"power": power, "control": control}}


# ------------------------------------------------------------ 安装焊盘 / 无网络器件 / 槽
def _cls_value(value: Any) -> str:
    return getattr(value, "value", str(value or ""))


def objects() -> dict[str, Any]:
    from clearance_check.model import NetClass

    board = _require_board()
    labels = {c.value: name for c, name in _class_labels().items()}
    mounts = []
    for m in getattr(board, "mounts", []) or []:
        cls = _cls_value(m.net_class)
        mounts.append(
            {
                "name": m.name,
                "x": round(m.x, 3),
                "y": round(m.y, 3),
                "width": round(m.width, 3),
                "height": round(m.height, 3),
                "hole": round(m.hole, 3),
                "net": m.net,
                "layer": m.layer,
                "net_class": cls,
                "net_class_label": labels.get(cls, cls),
                "voltage": m.voltage,
                "voltage_min": m.voltage_min,
                "voltage_max": m.voltage_max,
                "enabled": bool(m.enabled),
                "note": m.note,
            }
        )
    floating = []
    for f in getattr(board, "floating_parts", []) or []:
        cls = _cls_value(f.net_class)
        floating.append(
            {
                "designator": f.designator,
                "comment": f.comment,
                "footprint": f.footprint,
                "x": round(f.x, 3),
                "y": round(f.y, 3),
                "width": round(f.width, 3),
                "height": round(f.height, 3),
                "layer": f.layer,
                "pad_count": f.pad_count,
                "net_class": cls,
                "net_class_label": labels.get(cls, cls),
                "voltage": f.voltage,
                "voltage_min": f.voltage_min,
                "voltage_max": f.voltage_max,
                "enabled": bool(f.enabled),
            }
        )
    cutouts = []
    for i, c in enumerate(getattr(board, "cutouts", []) or []):
        cutouts.append(
            {
                "index": i,
                "kind": c.kind,
                "name": getattr(c, "name", ""),
                "x": round(c.x, 3),
                "y": round(c.y, 3),
                "width": round(c.width, 3),
                "height": round(c.height, 3),
                "diameter": round(float(getattr(c, "diameter", 0.0) or 0.0), 3),
                "role": c.role,
                "points": len(c.points or []),
                "outline": [[round(x, 3), round(y, 3)] for x, y in (c.points or [])],
            }
        )
    return {
        "mounts": mounts,
        "floating": floating,
        "cutouts": cutouts,
        "net_classes": [
            {"value": c.value, "label": labels.get(c.value, c.value)}
            for c in (NetClass.PRIMARY_CONST, NetClass.PRIMARY_SWITCHING, NetClass.SECONDARY)
        ],
    }


def components(search: str = "") -> dict[str, Any]:
    """器件列表：位号 / 注释 / 封装 / 类别 / 层 / 坐标 / 焊盘数。"""
    board = _require_board()
    keyword = (search or "").strip().lower()
    rows = []
    for designator, comp in (getattr(board, "components", {}) or {}).items():
        text = " ".join(
            filter(None, [comp.designator, comp.comment, comp.footprint])
        ).lower()
        if keyword and keyword not in text:
            continue
        try:
            pad_count = len(board.pads_of_component(designator))
        except Exception:  # noqa: BLE001
            pad_count = 0
        rows.append(
            {
                "designator": comp.designator,
                "comment": comp.comment,
                "footprint": comp.footprint,
                "category": comp.category or "",
                "layer": comp.layer,
                "x": round(comp.x, 3),
                "y": round(comp.y, 3),
                "rotation": comp.rotation,
                "pad_count": pad_count,
            }
        )
    rows.sort(key=lambda r: r["designator"])
    return {
        "rows": rows,
        "total": len(getattr(board, "components", {}) or {}),
        "categories": [
            {"value": "", "label": "普通器件"},
            {"value": "burn_terminal", "label": "烧录端子"},
        ],
    }


def update_component(designator: str, patch: dict[str, Any]) -> dict[str, Any]:
    """改器件类别（烧录端子等），影响避距判定时的类别推断。"""
    board = _require_board()
    comp = (getattr(board, "components", {}) or {}).get(designator)
    if comp is None:
        raise ServiceError(f"找不到器件 {designator}")
    if "category" in patch:
        comp.category = str(patch["category"] or "")
    return {"ok": True}


def _class_labels() -> dict[Any, str]:
    from clearance_check.model import NET_CLASS_LABELS

    return dict(NET_CLASS_LABELS)


def update_mount(name: str, patch: dict[str, Any]) -> dict[str, Any]:
    from clearance_check.model import NetClass, net_class_from_label

    board = _require_board()
    for m in getattr(board, "mounts", []) or []:
        if m.name != name:
            continue
        if "net_class" in patch:
            m.net_class = net_class_from_label(str(patch["net_class"]))
        if "voltage" in patch:
            m.voltage = _float(patch.get("voltage"))
        if "voltage_min" in patch:
            m.voltage_min = _float(patch.get("voltage_min"))
        if "voltage_max" in patch:
            m.voltage_max = _float(patch.get("voltage_max"))
        if "enabled" in patch:
            m.enabled = bool(patch["enabled"])
        return {"ok": True}
    raise ServiceError(f"找不到安装焊盘 {name}")


def update_floating(designator: str, patch: dict[str, Any]) -> dict[str, Any]:
    from clearance_check.model import net_class_from_label

    board = _require_board()
    for f in getattr(board, "floating_parts", []) or []:
        if f.designator != designator:
            continue
        if "net_class" in patch:
            f.net_class = net_class_from_label(str(patch["net_class"]))
        if "voltage" in patch:
            f.voltage = _float(patch.get("voltage"))
        if "voltage_min" in patch:
            f.voltage_min = _float(patch.get("voltage_min"))
        if "voltage_max" in patch:
            f.voltage_max = _float(patch.get("voltage_max"))
        if "enabled" in patch:
            f.enabled = bool(patch["enabled"])
        return {"ok": True}
    raise ServiceError(f"找不到器件 {designator}")


def update_cutout(index: int, patch: dict[str, Any]) -> dict[str, Any]:
    board = _require_board()
    cutouts = list(getattr(board, "cutouts", []) or [])
    if not (0 <= index < len(cutouts)):
        raise ServiceError("挖空序号越界")
    if "role" in patch:
        cutouts[index].role = str(patch["role"] or "")
    return {"ok": True}


def _outline_job(board_path: str) -> None:
    task = STATE.task
    try:
        sess = STATE.session
        if sess is None:
            raise ServiceError("还没有连接 Altium")
        task.stage = "读取轮廓"
        task.detail = "遍历整板铜箔，大板约 30 秒…"
        pairs = sess.cutout_outlines(board_path=board_path) or []
        idx = {i: c for i, c in enumerate(getattr(STATE.board, "cutouts", []) or [])}
        matched = 0
        for pair in pairs:
            try:
                cutout_index, points = pair
            except (TypeError, ValueError):
                continue
            cut = idx.get(int(cutout_index))
            if cut is None:
                continue
            cut.points = [(float(p[0]), float(p[1])) for p in (points or [])]
            matched += 1
        STATE.log(f"已读取 {matched} 个挖空的轮廓坐标")
        task.finished = True
    except Exception as exc:  # noqa: BLE001
        task.error = str(exc)
        STATE.log(f"读取轮廓失败：{exc}")
    finally:
        task.running = False
        task.finished_at = time.time()


def start_cutout_outlines(board_path: str = "") -> dict[str, Any]:
    from .store import TaskState

    with STATE.lock:
        if STATE.task.running:
            raise ServiceError("已有任务在运行")
        STATE.task = TaskState(running=True, kind="outlines", stage="准备", started_at=time.time())
    threading.Thread(target=_outline_job, args=(board_path,), daemon=True).start()
    return {"ok": True}


# ------------------------------------------------------------ 规则
def rules() -> dict[str, Any]:
    data = rules_to_dict()
    data["matrix_keys"] = sorted((data.get("matrix") or {}).keys())
    return data


def update_rules(patch: dict[str, Any]) -> dict[str, Any]:
    data = apply_rules(patch or {})
    STATE.log("间距规则已更新")
    return data
