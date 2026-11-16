"""连接 Altium、列工程、扫描、快照导入导出、示例工程。

原来这些都在 `main_window.py` 的 `_connect_task / _scan_task / _on_scanned` 里，
和 Qt 控件混在一起。这里把「纯数据那几步」原样搬出来，界面改成轮询状态。
"""

from __future__ import annotations

import copy
import threading
import time
from pathlib import Path
from typing import Any

from .store import STATE, TaskState, last_scan_path, snapshot_dir


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
                net=str(item.get("net") or ""),
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
                note = (
                    f"自动分类 {len(new_nets)} 个新网络，"
                    f"其余 {len(seeded)} 个沿用{baseline_name}"
                )
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

        # 本次结果成为下次扫描的配置基线（等价旧版把 last_scan.json 当基线）
        set_baseline(
            STATE.classifications,
            getattr(STATE.board, "mounts", None),
            "上次扫描",
        )

        STATE.log(
            "扫描完成：器件 %s / 焊盘 %s / 网络 %s（用时 %.1fs）"
            % (
                getattr(report, "component_count", "?"),
                getattr(report, "pad_count", "?"),
                getattr(report, "net_count", "?"),
                elapsed,
            )
        )
        _auto_save()
        task.finished = True
    except Exception as exc:  # noqa: BLE001
        task.error = str(exc)
        STATE.log(f"扫描失败：{exc}")
    finally:
        task.running = False
        task.finished_at = time.time()


def _scan_pcb_job(project_path: str) -> None:
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
        started = time.time()

        def progress(stage: str, detail: str) -> None:
            task.stage = stage or task.stage
            task.detail = detail or ""
            STATE.log(f"[{stage}] {detail}")

        board, report = sess.scan(
            project_path=project_path or STATE.project_path,
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

        # 分类：沿用旧的，新网络补自动分类
        old = dict(STATE.classifications)
        new_nets = [n for n in (getattr(board, "nets", []) or []) if n and n not in old]
        classes = {n: c for n, c in old.items() if n in set(getattr(board, "nets", []) or [])}
        if new_nets:
            try:
                classes.update(classify_board(board, STATE.pins, only_nets=set(new_nets)))
            except Exception as exc:  # noqa: BLE001
                STATE.log(f"新网络分类失败（忽略）：{exc}")

        with STATE.lock:
            STATE.board = board
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


def start_scan_pcb(project_path: str = "") -> dict[str, Any]:
    with STATE.lock:
        if STATE.task.running:
            raise ServiceError("已有任务在运行，请等它结束")
        if not STATE.scanned:
            raise ServiceError("请先「扫描工程」读取一次，之后才能单独重扫 PCB")
        STATE.task = TaskState(running=True, kind="scan_pcb", stage="准备", started_at=time.time())
        STATE.stop_flag = False
    STATE.log("开始重扫 PCB（沿用上次的分类结果）")
    threading.Thread(
        target=_scan_pcb_job, args=(project_path or STATE.project_path,), daemon=True
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

    # 导入的快照同时作为配置基线：之后「扫描工程」会沿用里面的分类与安装焊盘电位
    set_baseline(
        classifications,
        getattr(board, "mounts", None),
        f"快照 {Path(path).name}",
    )

    _sanitize(board)
    # 顺手写成 last_scan.json：下次启动自动恢复的就是这份基线，不用重复导入
    _auto_save()
    STATE.log(f"已导入快照：{path}（已设为配置基线）")
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
