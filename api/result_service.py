"""避距校验、违规结果、CSV 导出、布局求解与板框导入。"""

from __future__ import annotations

import copy
import csv
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .store import (
    STATE,
    backup_dir,
    board_for_view,
    component_violation_to_dict,
    ensure_rules,
    state_dir,
    violation_to_dict,
    web_dist,
)
from .scan_service import ServiceError

_LAST_PLANS: list[Any] = []


def _require_board() -> Any:
    if not STATE.scanned:
        raise ServiceError("还没有扫描结果，请先扫描工程或导入快照")
    return STATE.board


# ------------------------------------------------------------ 校验
def unassigned_mounts(mounts) -> list[str]:
    """列出未指派网络的安装孔名：net 为空或电位仍是「未定」。"""
    from clearance_check.model import NetClass

    return [
        str(getattr(m, "name", "") or "?")
        for m in mounts or []
        if getattr(m, "enabled", True)
        and (
            not str(getattr(m, "net", "") or "").strip()
            or getattr(m, "net_class", None) is NetClass.UNKNOWN
        )
    ]


def run_clearance() -> dict[str, Any]:
    from clearance_check.analysis.clearance import (
        aggregate_by_component,
        find_violations,
    )

    board = _require_board()
    rules = ensure_rules()
    with STATE.lock:
        classes = dict(STATE.classifications)
        mounts = list(getattr(board, "mounts", []) or [])
        parts = list(getattr(board, "floating_parts", []) or [])

    missing = unassigned_mounts(mounts)
    if missing:
        raise ServiceError(
            "以下安装孔未指派网络（应指派 SGND），请先在「安装焊盘」页处理后再校验："
            + "、".join(missing)
        )

    started = time.time()
    try:
        violations = find_violations(
            board,
            classes,
            rules,
            mounts=mounts,
            parts=parts,
        )
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(f"避距计算失败：{exc}") from exc
    aggregated = []
    try:
        aggregated = aggregate_by_component(violations)
    except Exception as exc:  # noqa: BLE001
        STATE.log(f"器件级汇总失败：{exc}")

    with STATE.lock:
        STATE.violations = violations
        STATE.aggregated = aggregated

    elapsed = time.time() - started
    STATE.log(
        "校验完成：%d 条焊盘级 / %d 条器件级（用时 %.1fs）"
        % (len(violations), len(aggregated), elapsed)
    )
    return summary()


def summary() -> dict[str, Any]:
    errors = [v for v in STATE.violations if getattr(v, "severity", "") == "error"]
    warnings = [v for v in STATE.violations if getattr(v, "severity", "") != "error"]
    agg_errors = [v for v in STATE.aggregated if getattr(v, "severity", "") == "error"]
    worst = max((float(getattr(v, "deficit_mm", 0.0) or 0.0) for v in STATE.aggregated), default=0.0)
    return {
        "ok": True,
        "pads": {
            "total": len(STATE.violations),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "components": {
            "total": len(STATE.aggregated),
            "errors": len(agg_errors),
            "warnings": len(STATE.aggregated) - len(agg_errors),
        },
        "worst_deficit": round(worst, 3),
    }


_PRIMARY_CLASSES = ("PRIMARY_CONST", "PRIMARY_SWITCHING")


def _rule_key(name: str) -> str:
    """规则名去掉具体数值，归成一类（与 PySide6 版 _rule_key 一致）。"""
    import re

    return re.sub(r"\s+", " ", re.sub(r"\d+(?:\.\d+)?", "#", name or "")).strip()


def _rule_label(name: str) -> str:
    """下拉里展示的短名：取第一个数字之前的部分，去掉尾部标点。"""
    import re

    name = name or ""
    match = re.search(r"\d", name)
    prefix = name[: match.start()] if match else name
    prefix = prefix.strip(" （(·、,，:：")
    prefix = re.sub(r"\s+", " ", prefix).strip()
    return prefix if len(prefix) >= 2 else name.strip()


def _is_isolation(item: Any) -> bool:
    """原边 ↔ 副边 的组合（对应原版 isolation_only）。"""
    pair = {getattr(item, "class_a", None), getattr(item, "class_b", None)}
    values = {getattr(c, "value", str(c)) for c in pair if c is not None}
    has_primary = bool(values & set(_PRIMARY_CLASSES))
    return has_primary and "SECONDARY" in values


def results(
    kind: str = "component",
    severity: str = "all",
    search: str = "",
    rule: str = "",
    isolation: bool = False,
) -> dict[str, Any]:
    """kind=component 器件级（默认）；kind=pad 焊盘级明细。

    筛选维度（与原版 violation_panel 对齐）：
      severity   all / error / warning
      rule       规则类别（_rule_key 归一化后的 key）
      isolation  只看隔离带（原边 ↔ 副边）
      search     位号 / 网络模糊搜索
    """
    rows = STATE.aggregated if kind == "component" else STATE.violations

    # 规则类别清单（基于未过滤的全量，供前端下拉）
    catalog: dict[str, dict[str, Any]] = {}
    for item in rows:
        key = _rule_key(getattr(item, "rule_name", ""))
        entry = catalog.get(key)
        if entry is None:
            catalog[key] = {
                "key": key,
                "label": _rule_label(getattr(item, "rule_name", "")),
                "count": 1,
            }
        else:
            entry["count"] += 1

    out = []
    for item in rows:
        if severity == "error" and getattr(item, "severity", "") != "error":
            continue
        if severity == "warning" and getattr(item, "severity", "") == "error":
            continue
        if rule and _rule_key(getattr(item, "rule_name", "")) != rule:
            continue
        if isolation and not _is_isolation(item):
            continue
        if kind == "component":
            d = component_violation_to_dict(item)
            text = f"{d.get('designator_a')} {d.get('designator_b')} {d.get('net_a')} {d.get('net_b')}"
        else:
            d = violation_to_dict(item)
            text = f"{d.get('item_a')} {d.get('item_b')} {d.get('net_a')} {d.get('net_b')}"
        if search and search.lower() not in text.lower():
            continue
        out.append(d)
    out.sort(key=lambda d: -float(d.get("deficit_mm") or 0.0))
    return {
        "rows": out,
        "summary": summary(),
        "rules": sorted(catalog.values(), key=lambda r: -r["count"]),
        "total": len(rows),
        "filtered": len(out),
    }


def export_csv(path: str = "") -> dict[str, Any]:
    rows = [component_violation_to_dict(item) for item in STATE.aggregated]
    if not rows:
        raise ServiceError("还没有违规结果，请先点「重新校验」")
    if path:
        target = Path(path)
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
    else:
        folder = state_dir() / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"clearance_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    fields = [
        ("designator_a", "器件A"),
        ("designator_b", "器件B"),
        ("net_a", "网络A"),
        ("net_b", "网络B"),
        ("class_a_label", "类别A"),
        ("class_b_label", "类别B"),
        ("actual_mm", "实测(mm)"),
        ("required_mm", "要求(mm)"),
        ("deficit_mm", "缺口(mm)"),
        ("x", "X"),
        ("y", "Y"),
        ("rule_name", "依据规则"),
        ("severity", "类型"),
        ("count", "焊盘对数"),
    ]
    with open(target, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([label for _, label in fields])
        for row in rows:
            writer.writerow([row.get(key, "") for key, _ in fields])
    STATE.log(f"已导出 CSV：{target}")
    return {"ok": True, "path": str(target)}


# ------------------------------------------------------------ 布局视图
def layout() -> dict[str, Any]:
    return board_for_view()


def import_outline_html(path: str) -> dict[str, Any]:
    from clearance_check.outline_html import load_outline_html

    board = _require_board()
    target = Path(path)
    if not target.is_file():
        raise ServiceError(f"文件不存在：{path}")
    try:
        bbox = None
        points = None
        try:
            bbox = board.board_bbox()
            points = [(p.x, p.y) for p in board.pads]
        except Exception:  # noqa: BLE001
            pass
        outline = load_outline_html(target, pad_bbox=bbox, pad_points=points)
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(f"解析板框 HTML 失败：{exc}") from exc
    if not outline or len(outline) < 3:
        raise ServiceError("这条 HTML 里没解析出有效板框")
    with STATE.lock:
        board.outline = [(float(x), float(y)) for x, y in outline]
        board.outline_version = int(getattr(board, "outline_version", 0)) + 1
    STATE.log(f"已导入板框：{target.name}（{len(board.outline)} 点）")
    return {"ok": True, "points": len(board.outline), "path": str(target)}


def solve(rect: list[float]) -> dict[str, Any]:
    """对选区求解最小移动量（只算方案，不动 Altium）。"""
    global _LAST_PLANS

    from clearance_check.analysis.optimize import optimize_area

    board = _require_board()
    if not rect or len(rect) != 4:
        raise ServiceError("选区无效：需要 [x0, y0, x1, y1]（mm）")
    x0, y0, x1, y1 = (float(v) for v in rect)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        raise ServiceError("选区太小")
    classes = dict(STATE.classifications)
    rules = ensure_rules()
    mounts = list(getattr(board, "mounts", []) or [])
    parts = list(getattr(board, "floating_parts", []) or [])

    started = time.time()
    try:
        plans = optimize_area(
            copy.deepcopy(board),
            classes,
            rules,
            (x0, y0, x1, y1),
            mounts=mounts,
            parts=parts,
        )
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(f"求解失败：{exc}") from exc
    _LAST_PLANS = list(plans or [])
    rows = []
    for plan in _LAST_PLANS:
        rows.append(
            {
                "designator": getattr(plan, "designator", ""),
                "x0": round(float(getattr(plan, "x0", 0.0)), 3),
                "y0": round(float(getattr(plan, "y0", 0.0)), 3),
                "x1": round(float(getattr(plan, "x1", 0.0)), 3),
                "y1": round(float(getattr(plan, "y1", 0.0)), 3),
                "rot0": getattr(plan, "rot0", 0.0),
                "rot1": getattr(plan, "rot1", 0.0),
                "moved": bool(getattr(plan, "moved", False)),
                "reason": getattr(plan, "reason", ""),
                "shift_mm": round(float(getattr(plan, "shift_mm", 0.0)), 3),
                "details": list(getattr(plan, "rows", []) or []),
            }
        )
    moved = [r for r in rows if r["moved"]]
    STATE.log(
        "求解完成：%d 个器件可移动（用时 %.1fs）" % (len(moved), time.time() - started)
    )
    return {"rows": rows, "movable": len(moved), "elapsed": round(time.time() - started, 1)}


def apply_moves() -> dict[str, Any]:
    """把上次求解里可行的移动真正下发到 Altium（先备份工程）。"""
    from clearance_check.model import NET_CLASS_LABELS  # noqa: F401

    moved = [p for p in _LAST_PLANS if getattr(p, "moved", False)]
    if not moved:
        raise ServiceError("没有可应用的移动方案，请先求解")
    sess = STATE.session
    if sess is None or not STATE.connected:
        raise ServiceError("未连接 Altium，无法移动器件")

    backup_path, files = _backup_project()
    moves = [
        {
            "designator": plan.designator,
            "x": plan.x1 / 0.0254,
            "y": plan.y1 / 0.0254,
            "rotation": plan.rot1,
        }
        for plan in moved
    ]
    try:
        sess._call("pcb_move_components", {"moves": moves}, timeout=180)
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(f"移动器件失败：{exc}") from exc

    board = STATE.board
    for plan in moved:
        comp = (getattr(board, "components", {}) or {}).get(plan.designator)
        if comp is None:
            continue
        dx, dy = plan.x1 - plan.x0, plan.y1 - plan.y0
        for pad in board.pads_of_component(plan.designator):
            pad.x += dx
            pad.y += dy
        comp.x = plan.x1
        comp.y = plan.y1
        comp.rotation = plan.rot1

    STATE.log(
        "已移动 %d 个器件；工程备份 %d 个文件 → %s" % (len(moved), files, backup_path)
    )
    return {"ok": True, "moved": len(moved), "backup": str(backup_path), "files": files}


def _backup_project() -> tuple[Path, int]:
    """把工程目录里的设计文件复制到 backup/<时间戳>/pcb/。"""
    board = STATE.board
    project = STATE.project_path or getattr(board, "source_path", "")
    folder = backup_dir() / time.strftime("%Y%m%d_%H%M%S") / "pcb"
    folder.mkdir(parents=True, exist_ok=True)
    if not project or not Path(project).exists():
        STATE.log("没有工程路径，跳过工程备份")
        return folder.parent, 0

    root = Path(project)
    base = root.parent if root.is_file() else root
    exts = {
        ".PrjPcb",
        ".PrjPcbStructure",
        ".PcbDoc",
        ".SchDoc",
        ".PcbLib",
        ".SchLib",
        ".OutJob",
    }
    skip = {"__Previews", "History", "Project Outputs", "backup"}
    count = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if os.path.splitext(fn)[1] not in exts:
                continue
            src = Path(dirpath) / fn
            rel = src.relative_to(base)
            dst = folder / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, dst)
                count += 1
            except OSError as exc:
                STATE.log(f"备份失败 {rel}：{exc}")
    return folder.parent, count


def locate(designator: str) -> dict[str, Any]:
    sess = STATE.session
    if sess is None or not STATE.connected or STATE.demo_mode:
        raise ServiceError("未连接 Altium（或当前是示例工程），无法联动定位")
    try:
        sess.cross_probe(designator, target="pcb")
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(f"定位失败：{exc}") from exc
    return {"ok": True, "designator": designator}


def near_miss() -> dict[str, Any]:
    """接近违规的网络（比值 1.35 以内），用于提前预警。"""
    from clearance_check.analysis.clearance import near_miss_nets

    try:
        names = near_miss_nets(STATE.violations, STATE.classifications)
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(f"计算接近违规失败：{exc}") from exc
    return {"nets": sorted(names) if not isinstance(names, dict) else names}
