"""连接 Altium、列工程、扫描、快照导入导出、示例工程。

原来这些都在 `main_window.py` 的 `_connect_task / _scan_task / _on_scanned` 里，
和 Qt 控件混在一起。这里把「纯数据那几步」原样搬出来，界面改成轮询状态。
"""

from __future__ import annotations

import copy
import json
import math
import threading
import time
from pathlib import Path
from typing import Any

from .store import STATE, TaskState, last_scan_path, snapshot_dir, state_dir


class ServiceError(RuntimeError):
    """直接给前端显示的错误（带处置提示）。"""


# ------------------------------------------------------------ 配置基线
def _baseline() -> tuple[dict[str, Any], list[Any], str]:
    """取出配置基线（分类 / 安装焊盘电位 / 名称）。"""
    with STATE.lock:
        return dict(STATE.baseline), list(STATE.baseline_mounts), STATE.baseline_name


def set_baseline(
    classifications: dict[str, Any] | None,
    mounts: list[Any] | None = None,
    name: str = "",
) -> None:
    """记录配置基线：重新扫描时同名网络沿用这份分类，只对新增网络自动判定。"""
    if not classifications:
        return
    with STATE.lock:
        STATE.baseline = {key: copy.deepcopy(item) for key, item in classifications.items()}
        STATE.baseline_mounts = copy.deepcopy(list(mounts or []))
        STATE.baseline_name = name or "配置基线"


def restore_last_baseline() -> None:
    """启动时把上次自动保存的快照载入为基线（对齐旧版 _restore_last_snapshot）。"""
    from clearance_check.snapshot import load_snapshot

    path = last_scan_path()
    if not path.is_file():
        return
    try:
        snap = load_snapshot(path, reclassify=False)
    except Exception as exc:  # noqa: BLE001 - 基线坏了不该影响启动
        STATE.log(f"恢复上次快照失败：{exc}")
        return
    classifications = dict(getattr(snap, "classifications", {}) or {})
    if not classifications:
        return
    mounts = list(getattr(getattr(snap, "board", None), "mounts", []) or [])
    set_baseline(classifications, mounts, f"上次扫描（{path.name}）")
    STATE.log(f"已载入配置基线：{path.name}（{len(classifications)} 个网络可沿用）")


# ------------------------------------------------------------ 连接 / 工程
def session() -> Any:
    from clearance_check.eda.session import EdaAgentSession

    with STATE.lock:
        if STATE.session is None:
            STATE.session = EdaAgentSession()
        return STATE.session


def connect() -> dict[str, Any]:
    sess = session()
    try:
        sess.connect()
    except Exception as exc:  # noqa: BLE001
        STATE.connected = False
        STATE.connection_error = str(exc)
        raise ServiceError(f"连接 Altium 失败：{exc}") from exc
    STATE.connected = bool(getattr(sess, "connected", True))
    STATE.connection_error = ""
    STATE.log("已连接 Altium（eda-agent）")
    return status()


def disconnect() -> dict[str, Any]:
    with STATE.lock:
        sess = STATE.session
        STATE.connected = False
    if sess is not None:
        try:
            sess.disconnect()
        except Exception as exc:  # noqa: BLE001
            STATE.log(f"断开时出错：{exc}")
    STATE.log("已断开 Altium")
    return status()


def status() -> dict[str, Any]:
    with STATE.lock:
        sess = STATE.session
        connected = STATE.connected
    if sess is not None:
        try:
            connected = bool(sess.connected)
        except Exception:  # noqa: BLE001
            pass
    return {
        "connected": connected,
        "error": STATE.connection_error,
        "scanned": STATE.scanned,
        "demo": STATE.demo_mode,
        "project_path": STATE.project_path,
        "task": STATE.task.snapshot(),
    }


def list_projects() -> dict[str, Any]:
    sess = session()
    try:
        projects = sess.list_projects() or []
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(f"读取工程列表失败：{exc}") from exc
    items = []
    for proj in projects:
        items.append(
            {
                "path": getattr(proj, "path", "") or "",
                "label": getattr(proj, "label", "") or getattr(proj, "path", ""),
            }
        )
    focused = ""
    try:
        current = sess.focused_project()
        focused = getattr(current, "path", "") or ""
    except Exception:  # noqa: BLE001
        pass
    return {"projects": items, "focused": focused}


def open_project(path: str) -> dict[str, Any]:
    sess = session()
    try:
        sess.open_project(path)
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(f"打开工程失败：{exc}") from exc
    STATE.log(f"已打开工程：{path}")
    return {"ok": True, "path": path}


# ------------------------------------------------------------ 扫描
def _make_mounts(items: list[dict] | None) -> list[Any]:
    from clearance_check.model import MountPoint

    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        out.append(
            MountPoint(
                name=str(item.get("name") or ""),
                x=float(item.get("x") or 0.0),
                y=float(item.get("y") or 0.0),
                width=float(item.get("width") or 4.0),
                height=float(item.get("height") or 4.0),
                hole=float(item.get("hole") or 0.0),
                net=str(item.get("net") or "SGND"),
                layer=str(item.get("layer") or "MULTI"),
                note=str(item.get("source") or ""),
            )
        )
    return out


def _make_floating(items: list[dict] | None) -> list[Any]:
    from clearance_check.model import FloatingPart

    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        out.append(
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
            )
        )
    return out


def _make_cutouts(items: list[dict] | None) -> list[Any]:
    from clearance_check.model import BoardCutout

    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        out.append(
            BoardCutout(
                kind=str(item.get("kind") or "rect"),
                x=float(item.get("x") or 0.0),
                y=float(item.get("y") or 0.0),
                width=float(item.get("width") or 0.0),
                height=float(item.get("height") or 0.0),
                name=str(item.get("name") or ""),
                role=str(item.get("role") or ""),
                points=[
                    (float(p[0]), float(p[1]))
                    for p in (item.get("points") or [])
                    if isinstance(p, (list, tuple)) and len(p) >= 2
                ],
            )
        )
    return out


def _sanitize(board: Any) -> None:
    """补器件类别 + 归一化挖空归属（避距槽 / 圆形孔 / 板框）。

    板框兜底那段（用最大挖空当板框）在原版里只为"没导入 HTML"的工程服务，
    Web 版沿用同一套 classify_cutouts，但不再自动把挖空当板框（板框由 HTML 导入）。
    """
    from clearance_check.eda.session import _guess_category, classify_cutouts

    for comp in (getattr(board, "components", {}) or {}).values():
        if getattr(comp, "category", ""):
            continue
        category = _guess_category(
            {"comment": comp.comment, "footprint": comp.footprint}
        )
        if category:
            comp.category = category

    cutouts = list(getattr(board, "cutouts", []) or [])
    if not cutouts:
        return
    as_dicts = [
        {
            "kind": c.kind,
            "x": c.x,
            "y": c.y,
            "width": c.width,
            "height": c.height,
            "role": c.role,
        }
        for c in cutouts
    ]
    try:
        classify_cutouts(as_dicts, list(getattr(board, "outline", []) or []), board)
    except Exception as exc:  # noqa: BLE001
        STATE.log(f"挖空归属判定失败（忽略）：{exc}")


def _apply_result(
    board: Any,
    classifications: dict,
    pins: list,
    report: Any,
    note: str,
    project_path: str,
    baseline_mounts: list[Any] | None = None,
) -> None:
    from clearance_check.analysis.sheets import (
        build_sheet_index,
        fill_missing_classifications,
    )
    from clearance_check.analysis.classify import (
        assign_primary_references,
        assign_secondary_references,
    )

    board.mounts = _make_mounts(getattr(report, "mount_pads", None))
    if baseline_mounts:
        # 安装焊盘的电位是人工指定的，重扫后按名称沿用，否则又变回默认副边
        from clearance_check.analysis.merge import merge_mounts

        kept = merge_mounts(board.mounts, baseline_mounts)
        if kept:
            STATE.log(f"沿用配置基线的安装焊盘电位：{kept} 个")
    board.floating_parts = _make_floating(getattr(report, "floating_parts", None))
    board.cutouts = _make_cutouts(getattr(report, "cutouts", None))

    index = build_sheet_index(
        pins,
        getattr(report, "sheet_components", {}) or {},
        board,
        getattr(report, "sheet_paths", {}) or {},
        getattr(report, "instance_sheets", {}) or {},
        getattr(report, "top_sheet", "") or "",
    )
    added = fill_missing_classifications(classifications, index)
    assign_secondary_references(classifications)
    assign_primary_references(classifications)

    with STATE.lock:
        STATE.board = board
        STATE.base_board = copy.deepcopy(board)
        STATE.pins = pins
        STATE.classifications = classifications
        STATE.sheet_index = index
        STATE.sheet_components = getattr(report, "sheet_components", {}) or {}
        STATE.sheet_paths = getattr(report, "sheet_paths", {}) or {}
        STATE.instance_sheets = getattr(report, "instance_sheets", {}) or {}
        STATE.top_sheet = getattr(report, "top_sheet", "") or ""
        STATE.violations = []
        STATE.aggregated = []
        STATE.project_path = project_path
        STATE.source_note = note
        STATE.demo_mode = False
        STATE.scanned_at = time.strftime("%Y-%m-%d %H:%M:%S")

    _sanitize(board)
    if added:
        STATE.log(f"补充分类：{added} 个仅存在于原理图的网络")
    STATE.log(note)


def _scan_job(kind: str, project_path: str, with_netlist: bool) -> None:
    from clearance_check.analysis.classify import classify_board

    task = STATE.task
    try:
        task.stage = "读取工程"
        task.detail = "连接 Altium 并读取器件 / 焊盘 / 网络…"
        sess = session()
        started = time.time()

        def progress(stage: str, detail: str) -> None:
            task.stage = stage or task.stage
            task.detail = detail or ""
            STATE.log(f"[{stage}] {detail}")

        board, report = sess.scan(
            project_path=project_path,
            with_netlist=with_netlist,
            progress=progress,
            should_stop=lambda: STATE.stop_flag,
        )
        elapsed = time.time() - started

        task.stage = "分类"
        pins = getattr(report, "pins", []) or []
        # 有配置基线时只对新增网络做自动判定，同名网络沿用人工确认过的结果
        baseline, baseline_mounts, baseline_name = _baseline()
        board_nets = {name for name in board.nets if name}
        if baseline:
            known = set(baseline)
            new_nets = sorted(board_nets - known)
            seeded = {
                name: copy.deepcopy(item)
                for name, item in baseline.items()
                if name in board_nets
            }
            if new_nets:
                task.detail = f"自动分类 {len(new_nets)} 个新网络（其余沿用基线）…"
                auto = classify_board(board, pins, only_nets=set(new_nets))
                classifications = {**seeded, **auto}
                note = f"自动分类 {len(new_nets)} 个新网络，其余 {len(seeded)} 个沿用{baseline_name}"
            else:
                task.detail = "同名网络全部沿用配置基线…"
                classifications = seeded
                note = f"沿用{baseline_name}的 {len(seeded)} 个网络，未做自动分类"
        else:
            task.detail = "按网络名与拓扑判定电压类别…"
            classifications = classify_board(board, pins)
            note = f"自动分类全部 {len(board_nets)} 个网络"

        task.stage = "装配"
        task.detail = "生成图纸索引与参考地…"
        _apply_result(
            board,
            classifications,
            pins,
            report,
            note,
            project_path,
            baseline_mounts=baseline_mounts,
        )

        for line in getattr(report, "steps", []) or []:
            STATE.log(line)
        for warn in getattr(report, "warnings", []) or []:
            STATE.log("提示：" + str(warn))

        STATE.log(
            "扫描完成：器件 %s / 焊盘 %s / 网络 %s（用时 %.1fs）"
            % (
                getattr(report, "component_count", "?"),
                getattr(report, "pad_count", "?"),
                getattr(report, "net_count", "?"),
                elapsed,
            )
        )
        # 本次结果成为下次扫描的配置基线（等价旧版把 last_scan.json 当基线）
        set_baseline(STATE.classifications, getattr(STATE.board, "mounts", None), "上次扫描")
        _auto_save()
        task.finished = True
    except Exception as exc:  # noqa: BLE001
        task.error = str(exc)
        STATE.log(f"扫描失败：{exc}")
    finally:
        task.running = False
        task.finished_at = time.time()



# ------------------------------------------------------------ 扫描框（区域重扫）
def _rect_tuple(rect) -> tuple[float, float, float, float]:
    """把 {x1,y1,x2,y2} 或 [x1,y1,x2,y2] 归一化成 (x1<x2, y1<y2)。"""
    if isinstance(rect, dict):
        values = [rect.get(k) for k in ("x1", "y1", "x2", "y2")]
    else:
        values = list(rect or [])
    if len(values) != 4:
        raise ServiceError("扫描框需要 4 个坐标（x1, y1, x2, y2）")
    try:
        x1, y1, x2, y2 = (float(v) for v in values)
    except (TypeError, ValueError):
        raise ServiceError("扫描框坐标不是有效数字")
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
        raise ServiceError("扫描框坐标必须是有限数值")
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _board_bbox(board) -> tuple[float, float, float, float] | None:
    pts = list(getattr(board, "outline", None) or [])
    if len(pts) >= 3:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))
    xs: list[float] = []
    ys: list[float] = []
    for pad in getattr(board, "pads", None) or []:
        r = max(pad.width, pad.height) / 2
        xs += [pad.x - r, pad.x + r]
        ys += [pad.y - r, pad.y + r]
    if xs:
        return (min(xs), min(ys), max(xs), max(ys))
    return None


def _hit(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def normalize_scan_box(rect, board=None):
    """校验并裁剪扫描框：返回 (box, clamped, board_bbox)。

    - 框会自动裁剪到板框 bbox 内；
    - 与板框无交集、或小于 1×1 mm 时抛 ServiceError（带处置提示）。
    """
    box = _rect_tuple(rect)
    bbox = _board_bbox(board) if board is not None else None
    if bbox is None:
        return box, False, None
    x1, y1, x2, y2 = box
    bx1, by1, bx2, by2 = bbox
    cx1, cy1 = max(x1, bx1), max(y1, by1)
    cx2, cy2 = min(x2, bx2), min(y2, by2)
    if cx2 <= cx1 or cy2 <= cy1:
        raise ServiceError("扫描框不在板框范围内，请重新框选")
    if (cx2 - cx1) < 1.0 or (cy2 - cy1) < 1.0:
        raise ServiceError("扫描框太小（至少 1×1 mm）")
    clamped = (cx1, cy1, cx2, cy2) != (x1, y1, x2, y2)
    return (cx1, cy1, cx2, cy2), clamped, bbox


def crop_board(board, rect):
    """按扫描框裁剪 Board：只保留框内器件/焊盘/走线/安装孔/无网络件/挖空。

    板框 outline 保持整板（板边距离仍按真实板框判定），非框内对象全部忽略。
    """
    x1, y1, x2, y2 = rect
    box = (x1, y1, x2, y2)
    out = copy.deepcopy(board)

    def _comp_box(c):
        if getattr(c, "bbox", None):
            return c.bbox
        return (c.x, c.y, c.x, c.y)

    kept_comps = {
        d: c for d, c in out.components.items() if _hit(_comp_box(c), box)
    }
    out.components = kept_comps

    kept_pads = []
    for pad in out.pads:
        r = max(pad.width, pad.height) / 2
        pbox = (pad.x - r, pad.y - r, pad.x + r, pad.y + r)
        if pad.component and pad.component in kept_comps:
            kept_pads.append(pad)
        elif _hit(pbox, box):
            kept_pads.append(pad)
    out.pads = kept_pads

    counts: dict[str, int] = {}
    for pad in kept_pads:
        if pad.component:
            counts[pad.component] = counts.get(pad.component, 0) + 1
    for d, c in out.components.items():
        c.pad_count = counts.get(d, 0)

    out.tracks = [
        t for t in (out.tracks or [])
        if _hit((min(t.x1, t.x2), min(t.y1, t.y2), max(t.x1, t.x2), max(t.y1, t.y2)), box)
    ]

    kept_mounts = []
    for m in out.mounts or []:
        r = max(getattr(m, "width", 0.0), getattr(m, "height", 0.0), getattr(m, "hole", 0.0)) / 2
        if _hit((m.x - r, m.y - r, m.x + r, m.y + r), box):
            kept_mounts.append(m)
    out.mounts = kept_mounts

    out.floating_parts = [
        f for f in (out.floating_parts or [])
        if x1 <= f.x <= x2 and y1 <= f.y <= y2
    ]
    out.cutouts = [c for c in (out.cutouts or []) if _hit(c.bbox, box)]

    nets: list[str] = []
    for name in (
        [p.net for p in out.pads]
        + [t.net for t in out.tracks]
        + [getattr(m, "net", "") for m in out.mounts]
    ):
        if name and name not in nets:
            nets.append(name)
    out.nets = nets

    out.warnings = list(getattr(out, "warnings", None) or []) + [
        "区域扫描：只保留 (%.2f, %.2f) ~ (%.2f, %.2f) mm 范围内的对象；板框仍按整板判定"
        % (x1, y1, x2, y2)
    ]
    return out, {
        "components": len(out.components),
        "pads": len(out.pads),
        "mounts": len(out.mounts),
        "tracks": len(out.tracks),
    }


def _scan_box_path() -> Path:
    return state_dir() / "scan_box.json"


def _load_scan_box(board):
    """从磁盘恢复扫描框（没有/失效则返回 None）。"""
    path = _scan_box_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
        box, _clamped, _bbox = normalize_scan_box(data, board)
    except Exception as exc:  # noqa: BLE001
        STATE.log(f"恢复扫描框失败（忽略）：{exc}")
        return None
    return {"x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3]}


def get_scan_box() -> dict[str, Any]:
    with STATE.lock:
        box = dict(STATE.scan_box) if STATE.scan_box else None
        board = STATE.board
    if box is None:
        box = _load_scan_box(board)
        if box is not None:
            with STATE.lock:
                STATE.scan_box = dict(box)
    bbox = _board_bbox(board) if board is not None else None
    return {"box": box, "board_bbox": list(bbox) if bbox else None}


def set_scan_box(rect) -> dict[str, Any]:
    """保存扫描框（rect=None 表示清除）；返回最终保存值与被裁剪标记。"""
    with STATE.lock:
        board = STATE.board
    if rect is None:
        with STATE.lock:
            STATE.scan_box = None
        try:
            _scan_box_path().unlink(missing_ok=True)
        except OSError:
            pass
        with STATE.lock:
            bbox = _board_bbox(board) if board is not None else None
        return {"box": None, "board_bbox": list(bbox) if bbox else None, "clamped": False}

    box, clamped, bbox = normalize_scan_box(rect, board)
    payload = {"x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3]}
    with STATE.lock:
        STATE.scan_box = payload
    try:
        _scan_box_path().write_text(json.dumps(payload), "utf-8")
    except OSError as exc:
        STATE.log(f"保存扫描框到磁盘失败（忽略）：{exc}")
    with STATE.lock:
        STATE.log(
            "扫描框已设置：%.2f, %.2f ~ %.2f, %.2f mm%s"
            % (box[0], box[1], box[2], box[3], "（已裁剪到板框内）" if clamped else "")
        )
    return {"box": payload, "board_bbox": list(bbox) if bbox else None, "clamped": clamped}


def _resolve_project_path(sess, project_path: str) -> str:
    """project_path 为空时取 Altium 当前聚焦工程，避免用到快照里的旧路径。"""
    if project_path:
        return project_path
    try:
        focused = sess.focused_project()
        if focused is not None and focused.path:
            return focused.path
    except Exception as exc:  # noqa: BLE001
        STATE.log(f"读取当前聚焦工程失败（忽略）：{exc}")
    return STATE.project_path


def _load_full_board():
    """取最近一次整板快照作为局部刷新的基线。"""
    from clearance_check.snapshot import load_snapshot

    path = last_scan_path()
    if not path.is_file():
        return None
    try:
        return load_snapshot(path).board
    except Exception as exc:  # noqa: BLE001
        STATE.log(f"读取整板基线失败（忽略）：{exc}")
        return None


def _refresh_region_job(project_path: str, region) -> None:
    """局部刷新：只读框内器件的坐标/焊盘，其余沿用整板基线。

    耗时只与框内器件数量相关；3D 本体随器件位移平移，不重读。
    """
    import copy as _copy

    from clearance_check.eda.session import _map_layer, _text, mm
    from clearance_check.model import Component, Pad

    task = STATE.task
    try:
        task.stage = "局部刷新"
        task.detail = "读取框内器件坐标…"
        sess = session()
        project_path = _resolve_project_path(sess, project_path)
        started = time.time()

        with STATE.lock:
            base = STATE.base_board
        if base is None:
            base = _load_full_board()
        if base is None:
            raise ServiceError("缺少整板基线：请先执行一次「扫描工程」或整板「扫描pcb」")

        t_coords = time.time()
        coords = []
        try:
            coords = sess.components_in_rect(region, margin=5.0)
        except Exception as exc:  # noqa: BLE001
            STATE.log(f"按坐标读取器件不可用（{exc}），改用全量坐标表")
        if not coords:
            try:
                coords = sess.export_coordinates()
            except Exception as exc:  # noqa: BLE001
                STATE.log(f"局部刷新不可用（{exc}），回退为整板读取后再裁剪")
                _scan_pcb_job(project_path, region)
                return
        STATE.log("局部刷新：器件坐标 %s 个（%.1fs）"
                  % (len(coords), time.time() - t_coords))

        def _get(row, *names):
            """obj_query / export_coordinates 的字段名大小写不统一，逐个尝试。"""
            if not isinstance(row, dict):
                return None
            lowered = {str(k).lower(): v for k, v in row.items()}
            for name in names:
                if name in row:
                    return row[name]
                if name.lower() in lowered:
                    return lowered[name.lower()]
            return None

        def _coord(row):
            return (
                _text(_get(row, "Designator", "designator")),
                mm(_get(row, "X", "x")),
                mm(_get(row, "Y", "y")),
                float(_get(row, "Rotation", "rotation") or 0.0),
                _text(_get(row, "Layer", "layer") or "TOP"),
            )

        fresh = {c[0]: c for c in (_coord(r) for r in coords) if c[0]}
        if coords and not fresh:
            STATE.log("器件坐标字段解析失败，改用全量坐标表")
            try:
                coords = sess.export_coordinates()
            except Exception as exc:  # noqa: BLE001
                raise ServiceError(f"读取器件坐标失败：{exc}") from exc
            fresh = {c[0]: c for c in (_coord(r) for r in coords) if c[0]}
        if not fresh:
            raise ServiceError("没有读到任何器件坐标，已放弃本次局部刷新（未改动现有数据）")
        board = _copy.deepcopy(base)
        x1, y1, x2, y2 = region
        margin = 5.0
        qbox = (x1 - margin, y1 - margin, x2 + margin, y2 + margin)

        def inside(px, py):
            return qbox[0] <= px <= qbox[2] and qbox[1] <= py <= qbox[3]

        targets: list[str] = []
        seen: set[str] = set()
        for d, c in board.components.items():
            if (c.bbox and _hit(c.bbox, qbox)) or inside(c.x, c.y):
                targets.append(d)
                seen.add(d)
        for d, r in fresh.items():
            if d not in seen and inside(r[1], r[2]):
                targets.append(d)
                seen.add(d)

        removed = 0
        for d in list(targets):
            r = fresh.get(d)
            if r is None:
                comp = board.components.get(d)
                # 原点不在查询范围内的器件，可能只是被坐标过滤漏掉，保留基线
                if comp is not None and not inside(comp.x, comp.y):
                    continue
                if board.components.pop(d, None) is not None:
                    removed += 1
                continue
            _des, nx, ny, rot, layer = r
            comp = board.components.get(d)
            if comp is None:
                comp = Component(designator=d, x=nx, y=ny, rotation=rot, layer=layer)
                board.components[d] = comp
            else:
                dx, dy = nx - comp.x, ny - comp.y
                comp.x, comp.y = nx, ny
                comp.rotation, comp.layer = rot, layer
                if comp.bbox and (dx or dy):
                    comp.bbox = (comp.bbox[0] + dx, comp.bbox[1] + dy,
                                 comp.bbox[2] + dx, comp.bbox[3] + dy)
                if comp.body_bbox and (dx or dy):
                    comp.body_bbox = (comp.body_bbox[0] + dx, comp.body_bbox[1] + dy,
                                      comp.body_bbox[2] + dx, comp.body_bbox[3] + dy)

        target_set = set(targets) & set(board.components)
        t_pads = time.time()
        pads_by = sess.component_pads(sorted(target_set)) if target_set else {}
        STATE.log("局部刷新：焊盘 %s 个器件（%.1fs）"
                  % (len(target_set), time.time() - t_pads))
        board.pads = [p for p in board.pads if p.component not in target_set]
        for d in sorted(target_set):
            for raw in pads_by.get(d) or []:
                net = _text(raw.get("net"))
                w = mm(raw.get("top_x_size"), 0.0) or mm(raw.get("x_size"), 0.0)
                h = mm(raw.get("top_y_size"), 0.0) or mm(raw.get("y_size"), 0.0)
                pad = Pad(
                    name=_text(raw.get("name")),
                    net=net,
                    component=d,
                    x=mm(raw.get("x")),
                    y=mm(raw.get("y")),
                    width=max(w, 0.01),
                    height=max(h, 0.01),
                    rotation=float(raw.get("rotation") or 0.0),
                    shape="rect",
                    layer=_map_layer(_text(raw.get("layer"))),
                    hole=mm(raw.get("hole_size")),
                    plated=not bool(raw.get("is_smd")),
                )
                if pad.net:
                    board.ensure_net(pad.net)
                board.pads.append(pad)

        # 新器件没有 bbox 时用焊盘外框补
        for d in sorted(target_set):
            comp = board.components.get(d)
            if comp is None or comp.bbox:
                continue
            ps = [p for p in board.pads if p.component == d]
            if not ps:
                continue
            xs = [p.x - p.width / 2 for p in ps] + [p.x + p.width / 2 for p in ps]
            ys = [p.y - p.height / 2 for p in ps] + [p.y + p.height / 2 for p in ps]
            comp.bbox = (min(xs), min(ys), max(xs), max(ys))

        # 保留整板：框内已刷新，框外沿用基线 —— 布局视图仍显示整板
        full_updated = _copy.deepcopy(board)
        boxed_pads = len([p for p in full_updated.pads if p.component in target_set])

        with STATE.lock:
            STATE.board = full_updated
            STATE.base_board = _copy.deepcopy(full_updated)
            STATE.scanned_at = time.strftime("%Y-%m-%d %H:%M:%S")
            STATE.source_note = (STATE.source_note or "") + "（扫描框局部刷新）"
        _sanitize(full_updated)

        # 扫描框重扫完成后自动重新校验，省去用户再点一次「重新校验」
        auto_note = ""
        task.stage = "重新校验"
        task.detail = "扫描框刷新完成，正在自动重新校验…"
        try:
            from .result_service import run_clearance

            check = run_clearance()
            comp = (check or {}).get("components") or {}
            auto_note = "；已自动校验：器件级 %s（ERROR %s）" % (
                comp.get("total", "?"),
                comp.get("errors", "?"),
            )
            STATE.log(
                "扫描框重扫后自动校验完成：器件级 %s / ERROR %s"
                % (comp.get("total", "?"), comp.get("errors", "?"))
            )
        except Exception as exc:  # noqa: BLE001
            auto_note = "；自动校验失败，请手动点「重新校验」"
            STATE.log(f"扫描框重扫后自动校验失败：{exc}")

        task.detail = (
            "局部刷新：刷新器件 %s / 焊盘 %s；整板 %s 个器件照常显示（框外沿用基线）%s"
            % (len(target_set), boxed_pads, len(full_updated.components), auto_note)
        )
        STATE.log(
            "局部刷新完成：刷新器件 %s / 焊盘 %s，移除 %s；整板 %s 器件，用时 %.1fs"
            % (len(target_set), boxed_pads, removed, len(full_updated.components),
               time.time() - started)
        )
        task.finished = True
    except Exception as exc:  # noqa: BLE001
        task.error = str(exc)
        STATE.log(f"局部刷新失败：{exc}")
    finally:
        task.running = False
        task.finished_at = time.time()


def _scan_pcb_job(project_path: str, region=None) -> None:
    """只重读 PCB：器件 / 焊盘 / 安装孔 / 挖空（挖空端点沿用上次结果）。

    分类结果沿用上一次扫描（新出现的网络按自动规则补一份）；
    完成后**不重算违规** —— 原版就是这么做的，避免几千行结果把界面拖死。
    """
    from clearance_check.analysis.classify import classify_board

    task = STATE.task
    try:
        task.stage = "重读 PCB"
        task.detail = "读取器件 / 焊盘 / 安装孔 / 挖空…"
        sess = session()
        project_path = _resolve_project_path(sess, project_path)
        started = time.time()

        def progress(stage: str, detail: str) -> None:
            task.stage = stage or task.stage
            task.detail = detail or ""
            STATE.log(f"[{stage}] {detail}")

        board, report = sess.scan(
            project_path=project_path,
            with_netlist=False,
            with_sheets=False,
            with_cutout_outlines=False,
            progress=progress,
            should_stop=lambda: STATE.stop_flag,
        )

        # 沿用上次的挖空轮廓点（按中心 + 尺寸配对）
        old_cutouts = list(getattr(STATE.board, "cutouts", []) or [])
        new_cutouts = _make_cutouts(getattr(report, "cutouts", None))
        for cut in new_cutouts:
            for old in old_cutouts:
                if abs(cut.x - old.x) < 0.05 and abs(cut.y - old.y) < 0.05 and old.points:
                    cut.points = list(old.points)
                    break

        board.mounts = _make_mounts(getattr(report, "mount_pads", None))
        _, baseline_mounts, _ = _baseline()
        if baseline_mounts:
            from clearance_check.analysis.merge import merge_mounts

            kept = merge_mounts(board.mounts, baseline_mounts)
            if kept:
                STATE.log(f"沿用配置基线的安装焊盘电位：{kept} 个")
        board.floating_parts = _make_floating(getattr(report, "floating_parts", None))
        board.cutouts = new_cutouts
        if not getattr(board, "outline", None):
            board.outline = list(getattr(STATE.board, "outline", []) or [])

        if region:
            region, clamped, _bbox = normalize_scan_box(region, board)
            STATE.log(
                "区域扫描：整板读取完成，框内区域 (%.2f, %.2f) ~ (%.2f, %.2f)%s"
                % (region[0], region[1], region[2], region[3],
                   "（已裁剪到板框内）" if clamped else "")
            )

        # 分类：沿用旧的，新网络补自动分类
        old = dict(STATE.classifications)
        new_nets = [n for n in (getattr(board, "nets", []) or []) if n and n not in old]
        if region:
            # 区域扫描：保留框外的全部人工分类，避免之后整板扫描还要重新确认
            classes = dict(old)
        else:
            classes = {n: c for n, c in old.items() if n in set(getattr(board, "nets", []) or [])}
        if new_nets:
            try:
                classes.update(classify_board(board, STATE.pins, only_nets=set(new_nets)))
            except Exception as exc:  # noqa: BLE001
                STATE.log(f"新网络分类失败（忽略）：{exc}")

        with STATE.lock:
            STATE.board = board
            STATE.base_board = copy.deepcopy(board)
            STATE.classifications = classes
            STATE.scanned_at = time.strftime("%Y-%m-%d %H:%M:%S")
            STATE.source_note = (STATE.source_note or "") + "（重扫 PCB）"
        _sanitize(board)

        for line in getattr(report, "steps", []) or []:
            STATE.log(line)
        STATE.log(
            "重扫 PCB 完成：器件 %s / 焊盘 %s（用时 %.1fs）；违规未重算，需要时去「违规结果」点重新校验"
            % (
                getattr(report, "component_count", "?"),
                getattr(report, "pad_count", "?"),
                time.time() - started,
            )
        )
        task.finished = True
    except Exception as exc:  # noqa: BLE001
        task.error = str(exc)
        STATE.log(f"重扫 PCB 失败：{exc}")
    finally:
        task.running = False
        task.finished_at = time.time()


def start_scan_pcb(
    project_path: str = "",
    region: Any = None,
    use_scan_box: bool = False,
    local: bool = False,
) -> dict[str, Any]:
    with STATE.lock:
        if STATE.task.running:
            raise ServiceError("已有任务在运行，请等它结束")
        if not STATE.scanned:
            raise ServiceError("请先「扫描工程」读取一次，之后才能单独重扫 PCB")
        if use_scan_box and region is None:
            region = STATE.scan_box
        board = STATE.board
    if region is not None:
        region, _clamped, _bbox = normalize_scan_box(region, board)
    if local and region is None:
        raise ServiceError("请先在布局视图里绘制扫描框，再使用「扫描框PCB重扫」")
    STATE.log(
        "开始重扫 PCB（沿用上次的分类结果）"
        + ("，仅扫描框内区域" if region is not None else "")
        + ("，局部刷新" if local and region is not None else "")
    )
    with STATE.lock:
        STATE.task = TaskState(running=True, kind="scan_pcb", stage="准备", started_at=time.time())
        STATE.stop_flag = False
    target = _refresh_region_job if (local and region is not None) else _scan_pcb_job
    threading.Thread(
        target=target,
        args=(project_path, region),
        daemon=True,
    ).start()
    return {"ok": True, "task": STATE.task.snapshot()}


def _auto_save() -> None:
    try:
        export_snapshot(str(last_scan_path()), quiet=True)
    except Exception as exc:  # noqa: BLE001
        STATE.log(f"自动保存快照失败：{exc}")


def start_scan(project_path: str = "", with_netlist: bool = True) -> dict[str, Any]:
    with STATE.lock:
        if STATE.task.running:
            raise ServiceError("已有任务在运行，请等它结束")
        STATE.task = TaskState(
            running=True, kind="scan", stage="准备", started_at=time.time()
        )
        STATE.stop_flag = False
        STATE.logs = []
    STATE.log(f"开始扫描：{project_path or '(当前工程)'}")
    thread = threading.Thread(
        target=_scan_job, args=("scan", project_path, with_netlist), daemon=True
    )
    thread.start()
    return {"ok": True, "task": STATE.task.snapshot()}


def cancel_task() -> dict[str, Any]:
    STATE.stop_flag = True
    STATE.task.cancelled = True
    STATE.log("已请求取消（当前步骤结束后生效）")
    return {"ok": True, "task": STATE.task.snapshot()}


def recover_bridge() -> dict[str, Any]:
    """桥接轮询脚本卡死/停止时，让 eda-agent 重新拉起 Altium 侧脚本。"""
    sess = session()
    return sess._call("app_recover_script", {}, timeout=120) or {}


def task_status() -> dict[str, Any]:
    with STATE.lock:
        return {"task": STATE.task.snapshot(), "logs": STATE.logs[-400:]}


# ------------------------------------------------------------ 示例 / 快照
def load_demo() -> dict[str, Any]:
    from clearance_check.altium.demo import build_demo_board
    from clearance_check.analysis.classify import classify_board

    board, pins = build_demo_board()
    classifications = classify_board(board, pins)
    with STATE.lock:
        STATE.logs = []
    _apply_result(board, classifications, pins, _DemoReport(board, pins), "内置示例工程", "")
    with STATE.lock:
        STATE.demo_mode = True
    STATE.log("已载入内置示例工程（90 W 隔离反激，含 4 处刻意违规）")
    return {"ok": True}


class _DemoReport:
    """示例工程没有 ScanReport，这里补一个同形状的壳。"""

    def __init__(self, board: Any, pins: list) -> None:
        self.pins = pins
        self.sheet_components: dict[str, list[str]] = {}
        self.sheet_paths: dict[str, str] = {}
        self.instance_sheets: dict[str, str] = {}
        self.top_sheet = ""
        self.mount_pads: list = []
        self.floating_parts: list = []
        self.cutouts: list = []
        self.steps: list[str] = []
        self.warnings: list[str] = []
        self.component_count = len(getattr(board, "components", {}) or {})
        self.pad_count = len(getattr(board, "pads", []) or [])
        self.net_count = len(getattr(board, "nets", []) or [])


def export_snapshot(path: str = "", quiet: bool = False) -> dict[str, Any]:
    from clearance_check.snapshot import save_snapshot

    if not STATE.scanned:
        raise ServiceError("还没有扫描结果，无法导出快照")
    target = Path(path) if path else (
        snapshot_dir() / f"{getattr(STATE.board, 'name', 'scan')}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    save_snapshot(
        target,
        board=STATE.board,
        classifications=STATE.classifications,
        pins=STATE.pins,
        sheet_components=STATE.sheet_components,
        sheet_paths=STATE.sheet_paths,
        instance_sheets=STATE.instance_sheets,
        top_sheet=STATE.top_sheet,
        project_path=STATE.project_path,
    )
    if not quiet:
        STATE.log(f"已导出快照：{target}")
    return {"ok": True, "path": str(target)}


def import_snapshot(path: str) -> dict[str, Any]:
    from clearance_check.snapshot import load_snapshot

    snap = load_snapshot(path)
    board = getattr(snap, "board", None)
    if board is None:
        raise ServiceError("快照里没有板数据")
    classifications = dict(getattr(snap, "classifications", {}) or {})
    pins = list(getattr(snap, "pins", []) or [])

    from clearance_check.analysis.sheets import (
        build_sheet_index,
        fill_missing_classifications,
    )
    from clearance_check.analysis.classify import (
        assign_primary_references,
        assign_secondary_references,
    )

    index = build_sheet_index(
        pins,
        dict(getattr(snap, "sheet_components", {}) or {}),
        board,
        dict(getattr(snap, "sheet_paths", {}) or {}),
        dict(getattr(snap, "instance_sheets", {}) or {}),
        getattr(snap, "top_sheet", "") or "",
    )
    fill_missing_classifications(classifications, index)
    assign_secondary_references(classifications)
    assign_primary_references(classifications)

    with STATE.lock:
        STATE.board = board
        STATE.base_board = copy.deepcopy(board)
        STATE.pins = pins
        STATE.classifications = classifications
        STATE.sheet_index = index
        STATE.sheet_components = dict(getattr(snap, "sheet_components", {}) or {})
        STATE.sheet_paths = dict(getattr(snap, "sheet_paths", {}) or {})
        STATE.instance_sheets = dict(getattr(snap, "instance_sheets", {}) or {})
        STATE.top_sheet = getattr(snap, "top_sheet", "") or ""
        STATE.project_path = getattr(snap, "project_path", "") or ""
        STATE.source_note = f"导入快照 {Path(path).name}"
        STATE.demo_mode = False
        STATE.scanned_at = time.strftime("%Y-%m-%d %H:%M:%S")
        STATE.violations = []
        STATE.aggregated = []

    _sanitize(board)
    STATE.log(f"已导入快照：{path}")
    return {"ok": True, "path": str(path)}


def snapshot_files() -> dict[str, Any]:
    """列出可导入的快照（json/ 目录 + 上次自动保存）。"""
    items = []
    seen = set()
    for folder in (snapshot_dir(), last_scan_path().parent):
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:40]:
            if f.name in seen:
                continue
            seen.add(f.name)
            items.append(
                {
                    "name": f.name,
                    "path": str(f),
                    "size": f.stat().st_size,
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
                }
            )
    return {"files": items}
