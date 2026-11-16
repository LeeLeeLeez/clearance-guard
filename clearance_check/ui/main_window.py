"""主窗口：连接 Altium EDA Agent、扫描工程、分类确认、规则配置与违规呈现。"""

from __future__ import annotations

import copy
import functools
import time
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Qt, Signal, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..altium.demo import build_demo_board, build_demo_project_name, build_demo_sheets
from ..analysis import (
    aggregate_by_component,
    apply_follow_target,
    apply_reference_potential,
    assign_primary_references,
    assign_secondary_references,
    classify_board,
    find_violations,
    merge_classifications,
    merge_mounts,
)
from ..analysis.rules import ClearanceRules
from ..analysis.sheets import build_sheet_index, fill_missing_classifications
from ..snapshot import (
    SnapshotError,
    default_snapshot_path,
    load_snapshot,
    save_snapshot,
)
from ..eda.session import ConnectionState, EdaAgentError, EdaAgentSession, LiveProject
from ..outline_html import load_outline_html

OUTLINE_VERSION = 2  # 板框解析算法版本（SVG 规范弧段）

from ..model import (
    Board,
    BoardCutout,
    FloatingPart,
    MountPoint,
    NetClassification,
    SheetNetIndex,
)
from ..resources import icon_path
from .board_view import BoardView
from .component_panel import ComponentPanel
from .floating_panel import FloatingPanel
from .slot_panel import SlotPanel
from .mount_panel import MountPanel
from .net_panel import NetPanel
from .rules_panel import RulesPanel
from .sheet_panel import SheetNetPanel
from .theme import ACCENT, CLASS_COLORS, DANGER, MUTED, OK, WARN
from .violation_panel import ViolationPanel

APP_TITLE = "Clearance Guard"
APP_SUBTITLE = "Altium 实时避距审核 · 原边 / 副边电压分域"

STATE_TEXT = {
    ConnectionState.DISCONNECTED: ("未连接", MUTED),
    ConnectionState.CONNECTING: ("正在连接…", WARN),
    ConnectionState.READY: ("已连接 Altium", OK),
    ConnectionState.NO_BRIDGE: ("Altium 脚本未响应", WARN),
    ConnectionState.ERROR: ("连接失败", DANGER),
}


class Worker(QThread):
    """后台执行耗时任务，避免界面卡住。"""

    progressed = Signal(str, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D401
        try:
            result = self._fn(self.progressed.emit, lambda: self._stop)
        except Exception as exc:  # noqa: BLE001 - 统一转成界面提示
            self.failed.emit(str(exc))
            return
        if not self._stop:
            self.succeeded.emit(result)


def _log_import(message: str) -> None:
    """导入快照的排错日志（打包后无控制台，只能落文件）。"""
    try:
        directory = Path(os.environ.get("APPDATA", str(Path.home()))) / "ClearanceGuard"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "import_log.txt").open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def _project_root() -> Path:
    """exe 运行时用 exe 所在目录，源码运行时用工程根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_TITLE} · {APP_SUBTITLE}")
        self.setWindowIcon(QIcon(str(icon_path())))
        self.resize(1480, 940)

        self.session = EdaAgentSession()
        self.board: Board | None = None
        self.pins: list[dict] = []
        self.classifications: dict[str, NetClassification] = {}
        self.sheet_index: SheetNetIndex | None = None
        self.sheet_components: dict[str, list[str]] = {}
        self.sheet_paths: dict[str, str] = {}
        self.instance_sheets: dict[str, str] = {}
        self.top_sheet: str = ""
        self.snapshot_source: str = ""
        self._baseline_snapshot = None      # 已套用的配置基线（后续扫描/导入自动沿用）
        self._baseline_name: str = ""
        self._sheet_dirty = False
        self.violations = []
        self.aggregated = []
        self.demo_mode = False
        self._worker: Worker | None = None
        self._projects: list[LiveProject] = []
        self._log_lines: list[str] = []

        self._rules_path = (
            Path(os.getenv("APPDATA", str(Path.home()))) / "ClearanceGuard" / "clearance_rules.json"
        )

        self._build_ui()
        self._restore_rules()
        self._update_connection_badge(ConnectionState.DISCONNECTED)
        self._restore_last_snapshot()
        self._start_pcb_watch()
        self._update_pcb_button_state()

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_design_bar())

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 10)
        # ---- 「工程与扫描」= 扫描日志 + 电压分类 + 安装焊盘 + 无网络器件 ----
        self.sheet_panel = SheetNetPanel()
        self.net_panel = NetPanel()
        self.net_book = QTabWidget()
        self.net_book.addTab(self.sheet_panel, "按原理图图纸")
        self.net_book.addTab(self.net_panel, "全表编辑与确认")
        self.rules_panel = RulesPanel()
        self.mount_panel = MountPanel()
        self.floating_panel = FloatingPanel()

        self.tabs = QTabWidget()
        self.overview_tabs = QTabWidget()
        self.overview_tabs.addTab(self._build_log_tab(), "扫描日志")
        self.component_panel = ComponentPanel()
        self.component_panel.changed.connect(self._on_component_category_changed)
        self.overview_tabs.addTab(self.component_panel, "器件列表")
        self.overview_tabs.addTab(self.net_book, "网络电压分类")
        self.overview_tabs.addTab(self.mount_panel, "安装焊盘")
        self.overview_tabs.addTab(self.floating_panel, "无网络器件")
        self.tabs.addTab(self.overview_tabs, "工程与扫描")
        self.slot_panel = SlotPanel()
        self.slot_panel.refreshRequested.connect(self._load_cutout_outlines)
        self.slot_panel.changed.connect(self._on_cutout_role_changed)
        self.slot_panel.reclassifyRequested.connect(self._reclassify_cutouts)
        self.tabs.addTab(self.slot_panel, "Board cutout")
        self.tabs.addTab(self.rules_panel, "间距规则")
        self.violation_panel = ViolationPanel()
        self.tabs.addTab(self.violation_panel, "违规结果")
        self.tabs.addTab(self._build_board_tab(), "布局视图")
        body_layout.addWidget(self.tabs)
        root.addWidget(body, 1)

        self.setCentralWidget(central)

        status = self.statusBar()
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("Hint")
        status.addWidget(self.status_label, 1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("Ghost")
        self.btn_cancel.setFixedWidth(70)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._cancel_task)
        status.addPermanentWidget(self.btn_cancel)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        status.addPermanentWidget(self.progress)

        self.net_panel.changed.connect(self._on_classification_changed)
        self.net_panel.recalcRequested.connect(self._reclassify)
        self.rules_panel.runRequested.connect(self._run_clearance)
        self.net_book.currentChanged.connect(self._on_net_book_changed)
        self.sheet_panel.changed.connect(self._on_sheet_edit)
        self.mount_panel.changed.connect(self._on_mount_changed)
        self.floating_panel.changed.connect(self._on_floating_changed)
        self.rules_panel.rulesChanged.connect(self._on_rules_changed)
        self.violation_panel.locateRequested.connect(self._locate_in_altium)
        self.violation_panel.violationSelected.connect(self._select_violation_in_view)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(84)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel(APP_TITLE)
        title.setObjectName("AppTitle")
        subtitle = QLabel(APP_SUBTITLE)
        subtitle.setObjectName("AppSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)
        layout.addSpacing(18)

        self.connection_badge = QLabel("● 未连接")
        self.connection_badge.setStyleSheet(f"color: {MUTED}; font-weight: 600;")
        layout.addWidget(self.connection_badge)

        self.btn_connect = QPushButton("连接 Altium")
        self.btn_connect.setObjectName("Ghost")
        self.btn_connect.clicked.connect(self._toggle_connection)
        layout.addWidget(self.btn_connect)

        layout.addSpacing(10)
        project_label = QLabel("工程")
        project_label.setObjectName("Hint")
        layout.addWidget(project_label)

        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(360)
        self.project_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.project_combo, 1)

        self.btn_refresh = QPushButton("刷新列表")
        self.btn_refresh.setObjectName("Ghost")
        self.btn_refresh.clicked.connect(self._refresh_projects)
        layout.addWidget(self.btn_refresh)

        self.btn_open = QPushButton("打开工程…")
        self.btn_open.setObjectName("Ghost")
        self.btn_open.clicked.connect(self._open_project)
        layout.addWidget(self.btn_open)

        self.btn_scan = QPushButton("扫描工程")
        self.btn_scan.setObjectName("Primary")
        self.btn_scan.setToolTip("读取整个工程：PCB + 原理图网表 + 图纸归属（大板 1~2 分钟）")
        self.btn_scan.clicked.connect(self._scan)
        layout.addWidget(self.btn_scan)

        scan_box = QVBoxLayout()
        scan_box.setSpacing(4)
        self.btn_scan_pcb = QPushButton("扫描pcb")
        self.btn_scan_pcb.setObjectName("Primary")
        self.btn_scan_pcb.setMinimumHeight(30)
        self.btn_scan_pcb.setEnabled(False)
        self.btn_scan_pcb.setToolTip("只重读 PCB（器件/焊盘/挖空），跳过原理图网表与图纸")
        self.btn_scan_pcb.clicked.connect(self._scan_pcb)
        scan_box.addWidget(self.btn_scan_pcb)
        layout.addLayout(scan_box)

        return header

    def _start_pcb_watch(self) -> None:
        """定时检查 PCB 修改时间，决定「扫描pcb」是否可用。"""
        if getattr(self, "_pcb_timer", None) is None:
            self._pcb_timer = QTimer(self)
            self._pcb_timer.setInterval(4000)
            self._pcb_timer.timeout.connect(self._update_pcb_button_state)
            self._pcb_timer.start()

    def _on_component_category_changed(self) -> None:
        self._append_log("器件类别已修改，正在重新校验（烧录端子豁免板框间距）…")
        if self.classifications:
            self._start_clearance()

    def _reclassify_cutouts(self) -> None:
        """忽略人工归属，按几何重新判定全部挖空。"""
        if self.board is None or not self.board.cutouts:
            return
        for cut in self.board.cutouts:
            cut.role = ""
        self._sanitize_board_geometry(self.board)
        self.slot_panel.set_cutouts(self.board.cutouts)
        self.mount_panel.set_mounts(self.board.mounts)
        self._append_log("已按几何重新判定挖空归属")
        if self.classifications:
            self._start_clearance()

    def _on_cutout_role_changed(self) -> None:
        self._append_log("挖空归属已人工修改，正在按新归属重算避距…")
        if self.board is None:
            return
        if self.board.cutouts:
            self.board.outline = list(self.board.outline)
        if self.violations or self._baseline_snapshot is not None or self.classifications:
            self._start_clearance()

    def _apply_baseline_cutout_roles(self, board: Board) -> int:
        """扫描后把配置基线里的挖空归属套到本次扫描到的挖空上。

        按「中心 ±1 mm、尺寸 ±1 mm」求交集：能对上的就沿用手工归属
        （板框外形 / 器件封装 / 爬电隔离），对不上的仍走自动判定。
        """
        baseline = self._baseline_snapshot
        if baseline is None or getattr(baseline, "board", None) is None:
            return 0
        old_cutouts = [cut for cut in baseline.board.cutouts if cut.role]
        if not old_cutouts:
            return 0

        matched = 0
        for cut in board.cutouts:
            for old in old_cutouts:
                if (
                    abs(cut.x - old.x) <= 1.0
                    and abs(cut.y - old.y) <= 1.0
                    and abs(cut.width - old.width) <= 1.0
                    and abs(cut.height - old.height) <= 1.0
                ):
                    if cut.role != old.role:
                        cut.role = old.role
                        matched += 1
                    break
        if matched:
            self._append_log(
                f"按配置基线沿用 {matched} 个挖空归属（{self._baseline_name or '上次快照'}）"
            )
        return matched

    def _sanitize_board_geometry(self, board: Board) -> None:
        """过滤非避距槽挖空；板框缺失时用「板框挖空」外框兜底。

        扫描和导入快照都会走这里，所以旧快照里过时的挖空列表也会被清理。
        """
        from ..eda.session import _guess_category, classify_cutouts

        # 旧快照没有类别字段：按注释/封装补一次（烧录端子等）
        guessed = 0
        for comp in board.components.values():
            if comp.category:
                continue
            category = _guess_category({"comment": comp.comment, "footprint": comp.footprint})
            if category:
                comp.category = category
                guessed += 1
        if guessed:
            self._append_log(f"按注释/封装识别器件类别 {guessed} 个（如烧录端子）")

        self._refresh_stale_outline(board)

        cutouts = list(board.cutouts)
        if not cutouts:
            return
        as_dicts = [
            {"kind": cut.kind, "x": cut.x, "y": cut.y,
             "width": cut.width, "height": cut.height, "role": cut.role}
            for cut in cutouts
        ]
        outline = list(board.outline)
        if len(outline) < 3:
            frame = max(as_dicts, key=lambda item: item["width"] * item["height"])
            frame_area = frame["width"] * frame["height"]
            others = sorted(
                (item["width"] * item["height"] for item in as_dicts
                 if item is not frame),
                reverse=True,
            )
            second = others[0] if others else 0.0
            bx0, by0, bx1, by1 = board.board_bbox()
            board_area = max((bx1 - bx0) * (by1 - by0), 1e-6)
            # 板框必须「框住大部分焊盘」——孔再大也装不下整板
            fx0 = frame["x"] - frame["width"] / 2
            fx1 = frame["x"] + frame["width"] / 2
            fy0 = frame["y"] - frame["height"] / 2
            fy1 = frame["y"] + frame["height"] / 2
            pads = board.pads
            inside_pads = sum(
                1 for pad in pads
                if fx0 - 1.0 <= pad.x <= fx1 + 1.0 and fy0 - 1.0 <= pad.y <= fy1 + 1.0
            )
            holds_board = bool(pads) and inside_pads >= 0.8 * len(pads)
            # 板框挖空通常远大于其它挖空：面积占比 ≥30% 或比第二大 5 倍以上
            if holds_board and (
                frame_area > 0.3 * board_area or frame_area > 5 * max(second, 1.0)
            ):
                half_w, half_h = frame["width"] / 2, frame["height"] / 2
                outline = [
                    (frame["x"] - half_w, frame["y"] - half_h),
                    (frame["x"] + half_w, frame["y"] - half_h),
                    (frame["x"] + half_w, frame["y"] + half_h),
                    (frame["x"] - half_w, frame["y"] + half_h),
                ]
                board.outline = outline

        labelled = classify_cutouts(as_dicts, outline, board)
        role_by_key = {
            (round(item["x"], 3), round(item["y"], 3), item["kind"]): item.get("role", "")
            for item in labelled
        }
        for cut in board.cutouts:
            key = (round(cut.x, 3), round(cut.y, 3), cut.kind)
            role = role_by_key.get(key)
            if role:
                cut.role = role
        # 安装焊盘里由「封装内圆孔」转来的 CH* 一律剔除（旧快照/旧扫描也能自动纠正）
        footprint_holes = {
            (round(cut.x, 2), round(cut.y, 2))
            for cut in board.cutouts
            if cut.is_circle and cut.role == "footprint"
        }
        if footprint_holes:
            before_mounts = len(board.mounts)
            board.mounts = [
                mount
                for mount in board.mounts
                if not (
                    str(getattr(mount, "name", "")).startswith("CH")
                    and (round(mount.x, 2), round(mount.y, 2)) in footprint_holes
                )
            ]
            removed = before_mounts - len(board.mounts)
            if removed:
                self._append_log(
                    f"封装内圆孔不计入安装焊盘：已移除 {removed} 个 CH* 安装孔"
                )
                index = 0
                for mount in board.mounts:
                    if str(getattr(mount, "name", "")).startswith("CH"):
                        index += 1
                        mount.name = f"CH{index}"

        # 反向补齐：被判为「爬电隔离」的圆形挖空如果不在安装焊盘里，补回来
        added = 0
        for cut in board.cutouts:
            if not cut.is_circle or cut.role == "footprint":
                continue
            exists = any(
                abs(mount.x - cut.x) < 0.05 and abs(mount.y - cut.y) < 0.05
                for mount in board.mounts
            )
            if exists:
                continue
            diameter = max(cut.width, cut.height)
            board.mounts.append(
                MountPoint(
                    name=f"CH{sum(1 for m in board.mounts if str(m.name).startswith('CH')) + 1}",
                    x=cut.x,
                    y=cut.y,
                    width=cut.width,
                    height=cut.height,
                    hole=diameter,
                    note=f"板级挖空（圆形 Ø{diameter:.2f} mm）",
                )
            )
            added += 1
        if added:
            self._append_log(f"按挖空归属补回 {added} 个安装孔")

        counts = {"frame": 0, "footprint": 0, "creepage": 0}
        for cut in board.cutouts:
            if cut.role in counts:
                counts[cut.role] += 1
        self._append_log(
            "挖空归属：板框 %d / 器件封装 %d / 爬电隔离 %d"
            % (counts["frame"], counts["footprint"], counts["creepage"])
        )

    def _build_design_bar(self) -> QWidget:
        """常驻的「当前设计」栏：所有页签都看得到，不随切页消失。"""
        bar = QFrame()
        bar.setObjectName("Card")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(10)
        heading = QLabel("当前设计")
        heading.setObjectName("SectionTitle")
        head.addWidget(heading)
        head.addStretch(1)

        self.btn_classify = QPushButton("自动判定电压类别")
        self.btn_classify.clicked.connect(self._reclassify)
        self.btn_export_snapshot = QPushButton("导出快照…")
        self.btn_export_snapshot.setObjectName("Ghost")
        self.btn_export_snapshot.setToolTip("把本次扫描结果（含人工确认）存成 JSON，下次无需重新扫描")
        self.btn_export_snapshot.clicked.connect(self._export_snapshot)
        self.btn_import_snapshot = QPushButton("导入快照…")
        self.btn_import_snapshot.setObjectName("Ghost")
        self.btn_import_snapshot.clicked.connect(self._import_snapshot)
        self.chk_netlist = QCheckBox("读取原理图网表")
        self.chk_netlist.setToolTip("读取编译网表里的引脚名（D/G/S 等）参与拓扑推断，大工程约需 20~30 秒")
        self.chk_netlist.setChecked(True)

        for widget in (
            self.btn_classify,
            self.btn_export_snapshot,
            self.btn_import_snapshot,
            self.chk_netlist,
        ):
            head.addWidget(widget)
        layout.addLayout(head)

        self.info_label = QLabel(
            "尚未扫描任何工程。点击右上角「连接 Altium」读取已打开的工程。"
        )
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info_label.setObjectName("Hint")
        layout.addWidget(self.info_label)
        return bar

    def _build_log_tab(self) -> QWidget:
        """扫描日志：独占整页，行距紧凑。"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))
        self.log_view.setPlaceholderText("扫描日志会显示在这里…")
        layout.addWidget(self.log_view, 1)
        return tab

    def _build_board_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.chk_labels = QCheckBox("显示位号")
        self.chk_labels.setChecked(True)
        self.chk_top = QCheckBox("顶层")
        self.chk_top.setChecked(True)
        self.chk_bottom = QCheckBox("底层")
        self.chk_bottom.setChecked(True)
        self.chk_error = QCheckBox("ERROR")
        self.chk_error.setChecked(True)
        self.chk_error.setToolTip("不同器件焊盘之间的违规（红）")
        self.chk_warning = QCheckBox("WARNING")
        self.chk_warning.setChecked(True)
        self.chk_warning.setToolTip("同一器件内部焊盘的违规（黄）")
        fit = QPushButton("适应窗口")
        fit.setObjectName("Ghost")
        self.btn_import_outline = QPushButton("导入板框HTML…")
        self.btn_import_outline.setObjectName("Ghost")
        self.btn_import_outline.setToolTip(
            "从板框记录 HTML 读取 SVG 外形（不再调用 eda-agent 的板框接口）"
        )
        self.btn_import_outline.clicked.connect(self._import_outline_html)
        self.btn_select_area = QPushButton("求解框")
        self.btn_select_area.setToolTip("点击后在画布上拖拽出矩形选区，松开即固定选区")
        self.btn_select_area.clicked.connect(self._start_area_select)
        self.btn_solve = QPushButton("求解")
        self.btn_solve.setEnabled(False)
        # 必须先「求解框」并拖出选区，才允许求解；求解一次后立即失效
        self._area_ready = False
        self._area_rect = None
        self._last_pcb_mtime: float | None = None
        self.btn_solve.setToolTip(
            "对已选定的矩形区域计算最小移动量方案（单器件位移上限 1 mm）"
        )
        self.btn_solve.clicked.connect(self._solve_area)
        for widget in (
            self.chk_labels,
            self.chk_top,
            self.chk_bottom,
            self.chk_error,
            self.chk_warning,
            self.btn_import_outline,
            self.btn_select_area,
            self.btn_solve,
            fit,
        ):
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        legend = QLabel(
            "焊盘颜色 = 电压类别；灰环 = 安装孔（外径/内径）；"
            "红点 = ERROR（跨器件），黄点 = WARNING（同器件内部）"
        )
        legend.setObjectName("Hint")
        toolbar.addWidget(legend)
        layout.addLayout(toolbar)

        self.board_view = BoardView()
        self.board_view.areaSelected.connect(self._on_area_selected)

        self.change_table = QTableWidget(0, 6)
        self.change_table.setHorizontalHeaderLabels(
            ["器件", "变更前位置 (mm)", "变更前旋转", "变更后位置 (mm)", "变更后旋转", "位移"]
        )
        self.change_table.verticalHeader().setVisible(False)
        self.change_table.verticalHeader().setDefaultSectionSize(28)
        self.change_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.change_table.setAlternatingRowColors(True)
        head = self.change_table.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.Interactive)
        head.setStretchLastSection(True)
        for column, width in enumerate((66, 104, 68, 104, 68)):
            self.change_table.setColumnWidth(column, width)

        change_tabs = QTabWidget()
        change_tabs.addTab(self.change_table, "变更详情")
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.board_view)
        splitter.addWidget(change_tabs)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([980, 430])
        layout.addWidget(splitter, 1)

        fit.clicked.connect(self.board_view.fit_view)
        self.chk_error.toggled.connect(self._update_severity_filter)
        self.chk_warning.toggled.connect(self._update_severity_filter)
        self.chk_labels.toggled.connect(self.board_view.set_labels_visible)
        self.chk_top.toggled.connect(self._update_layers)
        self.chk_bottom.toggled.connect(self._update_layers)
        return tab

    # ------------------------------------------------------------------
    # 连接与工程
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 布局优化
    # ------------------------------------------------------------------
    def _import_outline_html(self) -> None:
        """从 HTML 记录导入板框外形（替代 eda-agent 读取）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入板框 HTML", "", "HTML 文件 (*.html *.htm);;所有文件 (*)"
        )
        if not path:
            return
        _log_import(f"开始导入板框 {path}")
        self.status_label.setText("正在解析板框 HTML…")
        self._append_log(f"读取板框 HTML：{Path(path).name}")
        try:
            self._import_outline_html_inner(path)
        except Exception:  # noqa: BLE001 - 明细写日志并提示，避免“点了没反应”
            import traceback

            detail = traceback.format_exc()
            _log_import("板框导入失败：" + detail)
            QMessageBox.warning(
                self, "导入板框失败", detail.strip().splitlines()[-1]
            )

    def _import_outline_html_inner(self, path: str) -> None:
        _log_import(f"解析板框 HTML：{path}")
        pad_bbox = None
        pad_points = None
        if self.board is not None and self.board.pads:
            pad_bbox = self._outline_anchor_bbox()
            # 取一部分焊盘做样本：用来判断朝向 + 把 HTML 的相对坐标摆到实际位置
            pads = self.board.pads
            step = max(1, len(pads) // 400)
            pad_points = [(pad.x, pad.y) for pad in pads[::step]]
        try:
                        points = load_outline_html(path, pad_bbox, pad_points)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导入板框失败", str(exc))
            return

        if self.board is None:
            self._outline_pending = points
            self._append_log(f"已读取板框 {len(points)} 点，扫描工程后自动套用")
            return
        # 先清掉旧板框，避免新旧两条外形叠在一起
        self.board.outline = []
        self.board.outline = points
        self.board.outline_version = OUTLINE_VERSION
        self._remember_outline_source(path)
        self._sanitize_board_geometry(self.board)
        self.slot_panel.set_cutouts(self.board.cutouts)
        self.board_view.set_data(self.board, self.classifications, self.violations)
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        self._append_log(
            f"已从 {Path(path).name} 导入板框：{len(points)} 点，"
            f"范围 ({min(xs):.2f}, {min(ys):.2f}) ~ ({max(xs):.2f}, {max(ys):.2f}) mm"
        )
        _log_import(f"板框导入成功：{len(points)} 点")
        self._auto_save_snapshot()
        self.status_label.setText("板框已更新（来自 HTML），点「重新校验」刷新违规")

    def _outline_source_file(self) -> Path:
        base = Path(os.getenv("APPDATA", str(Path.home()))) / "ClearanceGuard"
        return base / "outline_source.json"

    def _remember_outline_source(self, path: str) -> None:
        try:
            target = self._outline_source_file()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"path": path}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _saved_outline_source(self) -> str:
        try:
            data = json.loads(self._outline_source_file().read_text(encoding="utf-8"))
            return str(data.get("path") or "")
        except (OSError, ValueError):
            return ""

    def _refresh_stale_outline(self, board: Board) -> None:
        """旧版本解析出来的板框（弧向错误）自动按新算法重算一次。

        板框仍然“只在导入 HTML 时更新”，这里只是把同一条记录用修正后的
        算法重新解析，避免用户看到旧形状必须手动再导一次。
        """
        if not board.outline or getattr(board, "outline_version", 0) >= OUTLINE_VERSION:
            return
        source = self._saved_outline_source()
        if not source or not Path(source).is_file():
            self._append_log(
                "板框由旧版算法导入，形状可能不准；请重新导入一次板框 HTML"
            )
            if hasattr(self, "status_label"):
                self.status_label.setText("板框为旧版解析结果，建议重新导入板框 HTML")
            return
        pads = board.pads
        step = max(1, len(pads) // 400) if pads else 1
        try:
            points = load_outline_html(
                source,
                self._outline_anchor_bbox(),
                [(pad.x, pad.y) for pad in pads[::step]] if pads else None,
            )
        except (OSError, ValueError) as exc:
            self._append_log(f"板框自动修复失败：{exc}")
            return
        board.outline = list(points)
        board.outline_version = OUTLINE_VERSION
        self._append_log(
            f"板框已按新算法从 {Path(source).name} 重新解析（{len(points)} 点）"
        )

    def _outline_anchor_bbox(self) -> tuple[float, float, float, float]:
        """板框对齐目标：优先用「板框挖空」的外框，其次才用焊盘范围。

        焊盘里可能散落着板外对象（夹具孔、备用焊盘），直接拿焊盘外框会把板框拉偏。
        """
        board = self.board
        if board is None:
            return (0.0, 0.0, 0.0, 0.0)
        frame = None
        for cut in board.cutouts:
            if getattr(cut, "role", "") == "frame":
                frame = cut
                break
        if frame is None and board.cutouts:
            frame = max(board.cutouts, key=lambda c: c.width * c.height)
        if frame is not None and frame.width > 0 and frame.height > 0:
            return (
                frame.x - frame.width / 2,
                frame.y - frame.height / 2,
                frame.x + frame.width / 2,
                frame.y + frame.height / 2,
            )
        # 没有板框挖空时：用焊盘的 0.5%~99.5% 分位，剔除板外零星对象
        pads = board.pads
        if not pads:
            return board.board_bbox()

        def trimmed(values: list[float]) -> tuple[float, float]:
            ordered = sorted(values)
            last = len(ordered) - 1
            return ordered[int(last * 0.005)], ordered[int(last * 0.995)]

        x0, x1 = trimmed([pad.x for pad in pads])
        y0, y1 = trimmed([pad.y for pad in pads])
        if x1 - x0 < 1.0 or y1 - y0 < 1.0:
            return board.board_bbox()
        return (x0, y0, x1, y1)

    def _start_area_select(self) -> None:
        if self.board is None:
            return
        self._area_rect = None
        self._area_ready = False
        self.btn_solve.setEnabled(False)
        self.status_label.setText("请在布局图上拖拽出矩形选区，松开后点「求解」")
        self._append_log("进入矩形框选模式：拖拽选择要优化的区域")
        self.board_view.start_area_select()

    def _on_area_selected(self, rect) -> None:
        if self.board is None:
            return
        # 场景坐标 y 轴是 -mm，这里翻回来
        x0 = min(rect.left(), rect.right())
        x1 = max(rect.left(), rect.right())
        y0 = min(-rect.top(), -rect.bottom())
        y1 = max(-rect.top(), -rect.bottom())
        self._area_rect = (x0, y0, x1, y1)
        self._area_ready = True
        self.btn_solve.setEnabled(True)
        self.status_label.setText(
            f"已选择区域 ({x0:.1f}, {y0:.1f}) ~ ({x1:.1f}, {y1:.1f}) mm，点「求解」开始优化"
        )
        self._append_log(
            f"选区已固定：({x0:.1f}, {y0:.1f}) ~ ({x1:.1f}, {y1:.1f}) mm，等待点「求解」"
        )

    def _solve_area(self) -> None:
        rect_mm = getattr(self, "_area_rect", None)
        if self.board is None or not rect_mm or not getattr(self, "_area_ready", False):
            self._area_rect = None
            self._area_ready = False
            self.btn_solve.setEnabled(False)
            self.status_label.setText("请先点「求解框」框出区域，再点「求解」")
            self._append_log("未框选区域：请先点「求解框」，在画布上拖出矩形后再求解")
            return
        # 选区只能用一次，避免误点「求解」重复套用旧区域
        self._area_ready = False
        self.btn_solve.setEnabled(False)
        x0, y0, x1, y1 = rect_mm
        self._append_log(
            f"开始求解选区 ({x0:.1f}, {y0:.1f}) ~ ({x1:.1f}, {y1:.1f}) mm 的最小移动量…"
        )
        board = self.board
        classifications = dict(self.classifications)
        rules = self.rules_panel.rules()
        mounts = list(board.mounts)
        parts = list(board.floating_parts)
        session = self.session

        def task(progress, stop):
            import copy as _copy

            from ..analysis.optimize import optimize_area

            progress("优化", "计算选区内的最小移动量方案…")
            work_board = _copy.deepcopy(board)
            plans = optimize_area(
                work_board, classifications, rules, rect_mm, mounts=mounts, parts=parts
            )
            moved = [plan for plan in plans if plan.moved]
            if not moved:
                return plans, "", 0
            progress("备份", "备份工程全部文件…")
            backup_dir, pcb_files = self._backup_project()
            progress("移动", f"通过 eda-agent 移动 {len(moved)} 个器件…")
            moves = [
                {
                    "designator": plan.designator,
                    "x": plan.x1 / 0.0254,
                    "y": plan.y1 / 0.0254,
                    "rotation": plan.rot1,
                }
                for plan in moved
            ]
            session._call("pcb_move_components", {"moves": moves}, timeout=180)
            return plans, backup_dir, len(moved), pcb_files

        def done(result):
            plans, backup_dir, count, pcb_files = result
            self._apply_move_plans(plans, backup_dir, count, pcb_files)
            self._start_clearance()

        self._run(task, done, "正在优化布局…")

    def _apply_move_plans(
        self, plans, backup_dir: str, count: int, pcb_files: int = 0
    ) -> None:
        board = self.board
        moved_keys = {(plan.designator, plan.x1, plan.y1) for plan in plans if plan.moved}
        for plan in plans:
            if not plan.moved:
                continue
            comp = board.components.get(plan.designator)
            if comp is None:
                continue
            dx, dy = plan.x1 - plan.x0, plan.y1 - plan.y0
            for pad in board.pads_of_component(plan.designator):
                pad.x += dx
                pad.y += dy
            comp.x = plan.x1
            comp.y = plan.y1
            comp.rotation = plan.rot1

        self.change_table.setRowCount(len(plans))
        for row, plan in enumerate(plans):
            cells = list(plan.rows) + [
                f"{plan.shift_mm:.2f} mm" if plan.moved else plan.reason
            ]
            for column, text in enumerate(cells):
                self.change_table.setItem(row, column, QTableWidgetItem(text))

        if count:
            self._append_log(
                f"已移动 {count} 个器件；PCB 工程备份 {pcb_files} 个文件、"
                f"源码快照见 {backup_dir}"
            )
        else:
            self._append_log("选区内没有可行解，未移动任何器件")
        self.status_label.setText(
            f"优化完成：移动 {count} 个器件"
            + (f"，PCB 备份 {Path(backup_dir).name}" if backup_dir else "")
        )
        self.board_view.set_data(board, self.classifications, self.violations)
        self.board_view.clear_area()
        self._area_rect = None
        self._area_ready = False
        self.btn_solve.setEnabled(False)
        self.component_panel.set_components(board)

    # Altium 工程备份：只挑工程/原理图/PCB/库/输出作业这类文件
    PCB_SUFFIXES = (
        ".PrjPcb", ".PrjPcbStructure", ".PcbDoc", ".SchDoc", ".PcbLib", ".SchLib",
        ".IntLib", ".OutJob", ".BomDoc", ".Harness", ".Annotation", ".PcbDwf",
        ".SchDot", ".PcbDot", ".CmpLib", ".DsnWrk", ".PcbRul", ".RUL",
    )
    PCB_SKIP_DIRS = {"__Previews", "History", "Project Outputs for", "Backup"}

    def _backup_project(self) -> tuple[str, int]:
        """求解前备份：Altium 工程文件 + 本工具源码（不含 json/dev）。

        返回 (备份根目录, PCB 工程文件数量)。
        """
        import shutil

        root = _project_root()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = root / "backup" / stamp

        pcb_count = self._copy_altium_project(base / "pcb")
        self._copy_tool_sources(base / "tool")
        return str(base), pcb_count

    def _copy_altium_project(self, dest: Path) -> int:
        """复制当前 PCB 工程的文档（PrjPcb / PcbDoc / SchDoc / 库 …）。"""
        import shutil

        projects = self._altium_project_roots()
        count = 0
        for project_root in projects:
            base_name = project_root.name or "project"
            for dirpath, dirnames, filenames in os.walk(project_root):
                dirnames[:] = [
                    name for name in dirnames
                    if name not in self.PCB_SKIP_DIRS
                ]
                for name in filenames:
                    if not name.endswith(self.PCB_SUFFIXES):
                        continue
                    src = Path(dirpath) / name
                    rel = src.relative_to(project_root)
                    target = dest / base_name / rel
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, target)
                        count += 1
                    except OSError as exc:
                        self._append_log(f"PCB 备份跳过 {src.name}：{exc}")
        return count

    def _altium_project_roots(self) -> list[Path]:
        """工程文件所在目录 + PcbDoc 所在目录（去重，保留更外层的那一个）。"""
        candidates: list[Path] = []
        board = self.board
        if board is not None and board.source_path:
            candidates.append(Path(board.source_path).parent)
        # 当前选中的 .PrjPcb（下拉框里带的是工程路径）
        try:
            project_path = str(self._selected_project() or "")
        except Exception:  # noqa: BLE001 - 下拉框还没建好时忽略
            project_path = ""
        if project_path.lower().endswith(".prjpcb"):
            candidates.append(Path(project_path).parent)
        roots: list[Path] = []
        for path in candidates:
            if not path.is_dir():
                continue
            if any(path.is_relative_to(existing) for existing in roots):
                continue
            roots = [item for item in roots if not item.is_relative_to(path)]
            roots.append(path)
        return roots

    def _copy_tool_sources(self, dest: Path) -> None:
        """本工具源码快照（跳过 json / dev / 备份自身，体积很小）。"""
        import shutil

        if getattr(sys, "frozen", False):
            return  # 打包成 exe 后没有源码可备份
        root = _project_root()
        skip = {"dev", "backup", ".venv", "venv", "__pycache__", ".pytest_cache",
                ".git", "json"}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in skip]
            for name in filenames:
                src = Path(dirpath) / name
                target = dest / src.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)


    def _update_severity_filter(self, _checked: bool = False) -> None:
        self.board_view.set_severity_filter(
            self.chk_error.isChecked(), self.chk_warning.isChecked()
        )

    def _update_connection_badge(self, state: ConnectionState) -> None:
        text, color = STATE_TEXT.get(state, ("未知", MUTED))
        self.connection_badge.setText(f"● {text}")
        self.connection_badge.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.btn_connect.setText("断开" if state is ConnectionState.READY else "连接 Altium")

    def _toggle_connection(self) -> None:
        if self.session.connected:
            self.session.disconnect()
            self._update_connection_badge(ConnectionState.DISCONNECTED)
            self.status_label.setText("已断开 EDA Agent")
            return
        self._run(
            self._connect_task,
            self._on_connected,
            "正在启动 EDA Agent 并连接 Altium…",
        )

    def _connect_task(self, progress, _stop):
        progress("连接", "启动 eda-agent MCP 服务…")
        state = self.session.connect()
        if state is not ConnectionState.READY:
            message = self.session.last_error or {
                ConnectionState.NO_BRIDGE: "Altium 侧脚本轮询没有响应，请在 Altium 中运行 StartMCPServer。",
                ConnectionState.ERROR: "无法启动 EDA Agent 服务。",
            }.get(state, "连接失败")
            raise EdaAgentError(message)
        progress("工程", "读取已打开的工程列表…")
        projects = self.session.list_projects()
        return state, projects

    def _on_connected(self, result) -> None:
        state, projects = result
        self._update_connection_badge(state)
        self._projects = projects
        self.project_combo.clear()
        for project in projects:
            if project.path.lower().endswith((".prjpcb", ".prjscr")):
                self.project_combo.addItem(project.label, project.path)
        focused = next(
            (p for p in projects if p.path.lower().endswith(".prjpcb")), None
        )
        if focused:
            index = self.project_combo.findData(focused.path)
            if index >= 0:
                self.project_combo.setCurrentIndex(index)
        self.status_label.setText(f"已连接：{len(projects)} 个打开的工程")
        self._append_log(f"连接成功，Altium 工程 {len(projects)} 个")

    def _refresh_projects(self) -> None:
        if not self.session.connected:
            self._toggle_connection()
            return
        self._run(self._refresh_task, self._on_connected, "刷新工程列表…")

    def _refresh_task(self, progress, _stop):
        progress("工程", "刷新已打开的工程…")
        return ConnectionState.READY, self.session.list_projects()

    def _open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Altium 工程", "", "Altium 工程 (*.PrjPcb);;所有文件 (*)"
        )
        if not path:
            return
        if not self.session.connected:
            QMessageBox.information(self, "未连接", "请先连接 Altium，再打开工程。")
            return

        def task(progress, _stop):
            progress("打开工程", f"在 Altium 中打开 {Path(path).name}…")
            project = self.session.open_project(path)
            return ConnectionState.READY, self.session.list_projects(), project

        def done(result):
            state, projects, project = result
            self._projects = projects
            self.project_combo.clear()
            for item in projects:
                if item.path.lower().endswith((".prjpcb", ".prjscr")):
                    self.project_combo.addItem(item.label, item.path)
            index = self.project_combo.findData(project.path)
            if index >= 0:
                self.project_combo.setCurrentIndex(index)
            self._on_connected((state, projects))
            self.status_label.setText(f"已打开工程 {project.name}")

        self._run(task, done, "正在打开工程…")

    def _selected_project(self) -> str:
        return self.project_combo.currentData() or ""

    # ------------------------------------------------------------------
    # 扫描
    # ------------------------------------------------------------------
    def _scan(self) -> None:
        if not self.session.connected:
            answer = QMessageBox.question(
                self,
                "尚未连接",
                "还没有连接 Altium。是否先连接？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer == QMessageBox.Yes:
                self._toggle_connection()
            return
        self.demo_mode = False
        project_path = self._selected_project()
        if not project_path:
            QMessageBox.information(self, "未选择工程", "请先在下拉框中选择一个工程。")
            return
        with_netlist = self.chk_netlist.isChecked()
        self._run(
            functools.partial(
                self._scan_task, project_path, with_netlist, self._baseline_snapshot
            ),
            self._on_scanned,
            "正在扫描工程（大板读取器件需要 20~30 秒）…",
        )

    def _pcb_mtime(self) -> float | None:
        """当前 PCB 文档的修改时间（用来判断是否需要重扫）。"""
        board = self.board
        if board is None or not board.source_path:
            return None
        try:
            return Path(board.source_path).stat().st_mtime
        except OSError:
            return None

    def _update_pcb_button_state(self) -> None:
        """「扫描pcb」的启用条件：已连接 + 已扫描过 + 当前空闲。

        Altium 里的改动在保存前不会更新文件 mtime，所以这里**不**拿 mtime
        当门槛，只把它写进提示，让用户随时可以手动重扫。
        """
        if not hasattr(self, "btn_scan_pcb"):
            return
        busy = self._worker is not None and self._worker.isRunning()
        enabled = bool(self.session.connected and self.board is not None and not busy)
        self.btn_scan_pcb.setEnabled(enabled)

        mtime = self._pcb_mtime()
        changed = (
            mtime is not None
            and self._last_pcb_mtime is not None
            and abs(mtime - self._last_pcb_mtime) > 1e-6
        )
        if not self.session.connected:
            self.btn_scan_pcb.setToolTip("请先连接 Altium")
        elif self.board is None:
            self.btn_scan_pcb.setToolTip("先点「扫描工程」读取一次，之后即可单独重扫 PCB")
        elif busy:
            self.btn_scan_pcb.setToolTip("正在扫描，请稍候…")
        elif changed:
            self.btn_scan_pcb.setToolTip("检测到 PCB 文件已改动，点击只重读 PCB（跳过原理图网表）")
        else:
            self.btn_scan_pcb.setToolTip(
                "只重读当前 PCB（器件/焊盘/挖空），跳过原理图网表；"
                "Altium 里未保存的改动同样会被读到"
            )

    def _scan_pcb(self) -> None:
        """只重扫 PCB：跳过原理图网表，完成后自动刷新违规与布局视图。"""
        if not self.session.connected:
            QMessageBox.information(
                self, "尚未连接", "请先连接 Altium，再执行「扫描pcb」。"
            )
            return
        project_path = self._selected_project()
        if not project_path:
            QMessageBox.information(self, "未选择工程", "请先在下拉框中选择一个工程。")
            return
        if self.board is None:
            QMessageBox.information(self, "尚未扫描", "请先点「扫描工程」读取一次完整数据。")
            return
        self.btn_scan_pcb.setEnabled(False)
        self.demo_mode = False
        self._run(
            functools.partial(self._scan_pcb_task, project_path),
            self._on_pcb_scanned,
            "正在扫描 PCB（器件 / 焊盘 / 挖空，跳过原理图网表）…",
        )

    def _scan_pcb_task(self, project_path: str, progress, stop):
        started = time.time()
        board, report = self.session.scan(
            project_path=project_path,
            progress=progress,
            should_stop=stop,
            with_netlist=False,
            with_sheets=False,
            with_cutout_outlines=False,
        )
        return board, report, time.time() - started

    def _on_pcb_scanned(self, result) -> None:
        board, report, seconds = result
        # 挖空端点（铜箔分页，30 秒）不在 PCB 快扫范围内：
        # 沿用它在上一次完整扫描里读到的顶点，避免每按一次都重读整板。
        old_points = {
            (round(cut.x, 2), round(cut.y, 2)): list(cut.points)
            for cut in (self.board.cutouts if self.board else [])
            if cut.points
        }
        board.cutouts = [
            BoardCutout(
                kind=str(item.get("kind") or "circle"),
                x=float(item.get("x") or 0.0),
                y=float(item.get("y") or 0.0),
                width=float(item.get("width") or 0.0),
                height=float(item.get("height") or 0.0),
                role=str(item.get("role") or ""),
                points=[(float(p[0]), float(p[1])) for p in (item.get("points") or [])]
                or old_points.get((round(float(item.get("x") or 0.0), 2),
                                   round(float(item.get("y") or 0.0), 2)), []),
            )
            for item in (report.cutouts or [])
        ]
        board.mounts = self._make_mounts(report.mount_pads)
        board.floating_parts = self._make_floating_parts(report.floating_parts)
        if not board.outline:
            board.outline = list(getattr(self.board, "outline", []) or [])
        self._apply_baseline_cutout_roles(board)

        # 已有分类沿用；PCB 上新增的网络才做自动判定
        board_nets = {name for name in board.nets if name}
        known = set(self.classifications)
        new_nets = sorted(board_nets - known)
        seed = {
            name: copy.deepcopy(item)
            for name, item in self.classifications.items()
            if name in board_nets
        }
        auto = (
            classify_board(board, self.pins, only_nets=set(new_nets)) if new_nets else {}
        )
        classifications = {**seed, **auto}

        self._apply_scan_result(
            board,
            classifications,
            self.pins,
            self.sheet_index or SheetNetIndex(),
            self.sheet_components,
            self.sheet_paths,
            self.instance_sheets,
            self.top_sheet,
            demo=self.demo_mode,
            source_note="PCB 增量扫描",
        )
        self._append_log(
            f"仅扫描 PCB 完成：{seconds:.1f}s，器件 {report.component_count} / "
            f"焊盘 {report.pad_count} / 网络 {report.net_count}，"
            f"新增网络 {len(new_nets)} 个"
            + (f"（{'、'.join(new_nets[:6])}…）" if len(new_nets) > 6 else
               (f"（{'、'.join(new_nets)}）" if new_nets else ""))
        )
        for step in report.steps:
            self._append_log(step)
        self._auto_save_snapshot()
        # 只刷新布局视图；违规结果等用户点「重新校验」再算（避免几千行表格卡死）
        self.violations = []
        self.aggregated = []
        self.violation_panel.clear()
        self.board_view.set_data(board, classifications, [])
        self.status_label.setText(
            "PCB 已更新（未重算违规）——点「违规结果」页的「重新校验」查看最新违规"
        )
        self._update_pcb_button_state()

    def _scan_task(
        self,
        project_path: str,
        with_netlist: bool,
        baseline,
        progress,
        stop,
    ):
        started = time.time()
        board, report = self.session.scan(
            project_path=project_path,
            progress=progress,
            should_stop=stop,
            with_netlist=with_netlist,
        )
        scan_seconds = time.time() - started
        board_nets = {name for name in board.nets if name}
        known = set(baseline.classifications) if baseline is not None else set()
        new_nets = sorted(board_nets - known)

        seeded: dict[str, NetClassification] = {}
        if baseline is not None:
            seeded = {
                name: copy.deepcopy(item)
                for name, item in baseline.classifications.items()
                if name in board_nets
            }

        if baseline is not None and not new_nets:
            classifications = seeded
            progress("分类", "全部网络都在配置基线内，跳过自动分类")
            note = f"沿用配置基线 {len(seeded)} 个网络，未做自动分类"
        elif baseline is not None:
            progress("分类", f"仅对 {len(new_nets)} 个新网络自动分类…")
            auto = classify_board(board, report.pins, only_nets=set(new_nets))
            classifications = {**seeded, **auto}
            shown = "、".join(new_nets[:12]) + ("…" if len(new_nets) > 12 else "")
            note = f"自动分类 {len(new_nets)} 个新网络（其余沿用基线）：{shown}"
        else:
            progress("分类", "根据网络名与拓扑判定电压类别…")
            classifications = classify_board(board, report.pins)
            note = f"自动分类全部 {len(board_nets)} 个网络"

        return board, report, classifications, note, scan_seconds

    def _make_mounts(self, items: list[dict] | None) -> list[MountPoint]:
        """把扫描结果里的安装焊盘 dict 转成 MountPoint。"""
        mounts: list[MountPoint] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            mounts.append(
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
        return mounts

    def _make_floating_parts(self, items: list[dict] | None) -> list[FloatingPart]:
        """把扫描结果里的无网络器件 dict 转成 FloatingPart。"""
        parts: list[FloatingPart] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            parts.append(
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
        return parts

    def _on_scanned(self, result) -> None:
        board, report, classifications, classify_note, scan_seconds = result
        self._append_log(
            f"扫描完成：读取 {scan_seconds:.1f}s，分类 {classify_note}"
        )
        sheet_index = build_sheet_index(
            report.pins,
            report.sheet_components,
            board,
            report.sheet_paths,
            report.instance_sheets,
            report.top_sheet,
        )
        board.mounts = self._make_mounts(report.mount_pads)
        board.floating_parts = self._make_floating_parts(report.floating_parts)
        board.cutouts = [
            BoardCutout(
                kind=str(item.get("kind") or "circle"),
                x=float(item.get("x") or 0.0),
                y=float(item.get("y") or 0.0),
                width=float(item.get("width") or 0.0),
                height=float(item.get("height") or 0.0),
                role=str(item.get("role") or ""),
                points=[(float(p[0]), float(p[1])) for p in (item.get("points") or [])],
            )
            for item in (report.cutouts or [])
        ]
        self._apply_baseline_cutout_roles(board)
        # 板框来自 HTML 导入，扫描不再从 Altium 读取，这里沿用上一份
        if not board.outline:
            board.outline = list(getattr(self.board, "outline", []) or [])
            if len(board.outline) >= 3:
                self._append_log(f"板框沿用已导入外形：{len(board.outline)} 点")
        if not board.outline and getattr(self, "_outline_pending", None):
            board.outline = list(self._outline_pending)
            self._outline_pending = None
        self._apply_scan_result(
            board,
            classifications,
            report.pins,
            sheet_index,
            report.sheet_components,
            report.sheet_paths,
            report.instance_sheets,
            report.top_sheet,
            demo=False,
        )
        self.status_label.setText(
            f"扫描完成：器件 {report.component_count} / 焊盘 {report.pad_count} / 网络 {report.net_count}"
            f" / 图纸 {len(sheet_index.sheets)}"
        )
        for step in report.steps:
            self._append_log(step)
        for warning in report.warnings:
            self._append_log("提示：" + warning)
        self._last_pcb_mtime = self._pcb_mtime()
        self._auto_save_snapshot()
        self._update_pcb_button_state()


    def _apply_scan_result(
        self,
        board: Board,
        classifications: dict[str, NetClassification],
        pins: list[dict],
        sheet_index: SheetNetIndex,
        sheet_components: dict[str, list[str]],
        sheet_paths: dict[str, str],
        instance_sheets: dict[str, str],
        top_sheet: str,
        demo: bool = False,
        source_note: str = "",
    ) -> None:
        """把一次扫描（或导入的快照）灌进界面。"""
        self.board = board
        self.pins = pins
        self.classifications = classifications
        self.snapshot_source = source_note
        if self._baseline_snapshot is not None:
            merged = merge_classifications(
                classifications, self._baseline_snapshot.classifications, board
            )
            merged.mounts_kept = merge_mounts(board.mounts, self._baseline_snapshot.board.mounts)
            self._append_log(f"按配置基线 {self._baseline_name} 套用：{merged.summary}")
            self.snapshot_source = (
                f"{self.snapshot_source}　|　配置基线 {self._baseline_name}"
                if self.snapshot_source else f"配置基线 {self._baseline_name}"
            )
        self.sheet_index = sheet_index
        added = fill_missing_classifications(classifications, sheet_index)
        if added:
            self._append_log(f"补充分类：{added} 个仅存在于原理图的网络")
        assign_secondary_references(classifications)
        assign_primary_references(classifications)
        self.sheet_components = sheet_components
        self.sheet_paths = sheet_paths
        self.instance_sheets = instance_sheets
        self.top_sheet = top_sheet
        self.demo_mode = demo
        self.violations = []
        self.aggregated = []

        self.net_panel.set_items(list(classifications.values()))
        self.sheet_panel.set_data(board, classifications, sheet_index, pins)
        # 先清理几何（剔除封装内圆孔、标注挖空归属），再刷新各面板
        self._sanitize_board_geometry(board)
        self.mount_panel.set_mounts(board.mounts)
        self.floating_panel.set_parts(board.floating_parts)
        self.component_panel.set_components(board)
        self.slot_panel.set_cutouts(board.cutouts)
        self._sheet_dirty = False
        self._last_pcb_mtime = self._pcb_mtime()
        if hasattr(self, "btn_solve"):
            self._area_rect = None
            self._area_ready = False
            self.btn_solve.setEnabled(False)
        self.violation_panel.clear()
        self.board_view.set_data(board, classifications, [])
        self._update_overview()
        self.tabs.setCurrentWidget(self.overview_tabs)
        self.overview_tabs.setCurrentWidget(self.net_book)
        self.net_book.setCurrentWidget(self.sheet_panel)

    def _load_demo(self) -> None:
        board, pins = build_demo_board()
        classifications = classify_board(board, pins)
        sheets, sheet_paths = build_demo_sheets()
        sheet_index = build_sheet_index(
            pins, sheets, board, sheet_paths, instance_sheets={}, top_sheet="VPDB_main"
        )
        self._apply_scan_result(
            board,
            classifications,
            pins,
            sheet_index,
            sheets,
            sheet_paths,
            {},
            "VPDB_main",
            demo=True,
        )
        self.project_combo.clear()
        self.project_combo.addItem(build_demo_project_name(), "(demo)")
        self.status_label.setText("已载入内置示例工程：隔离反激电源 90 W")
        self._append_log("载入示例工程：" + build_demo_project_name())
        self._update_connection_badge(self.session.state)

    # ------------------------------------------------------------------
    # 扫描快照（导出 / 导入 / 自动保存）
    # ------------------------------------------------------------------
    def _snapshot_kwargs(self) -> dict:
        return {
            "board": self.board,
            "classifications": self.classifications,
            "pins": self.pins,
            "sheet_components": self.sheet_components,
            "sheet_paths": self.sheet_paths,
            "instance_sheets": self.instance_sheets,
            "top_sheet": self.top_sheet,
            "project_path": "(demo)" if self.demo_mode else self._selected_project(),
            "app_version": __version__,
        }

    def _default_snapshot_name(self) -> str:
        """默认文件名：<板名>_scan_<时间戳>.json，避免多次导出互相覆盖。"""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = (self.board.name if self.board is not None else "scan").strip() or "scan"
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        return f"{name}_scan_{stamp}.json"

    def _export_snapshot(self) -> None:
        if self.board is None:
            QMessageBox.information(self, "没有数据", "请先扫描一个工程或导入快照。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出扫描快照", self._default_snapshot_name(),
            "Clearance Guard 快照 (*.json)",
        )
        if not path:
            return
        try:
            save_snapshot(path, **self._snapshot_kwargs())
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        size = Path(path).stat().st_size / 1024
        self.status_label.setText(f"快照已导出：{path}（{size:.0f} KB）")
        self._append_log(f"导出快照 {path}（{size:.0f} KB）")

    def _import_snapshot(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入扫描快照", "", "Clearance Guard 快照 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        self._import_snapshot_path(path)

    def _import_snapshot_path(self, path: str) -> bool:
        """按路径导入快照（对话框与命令行共用）。"""
        _log_import(f"开始导入 {path}")
        try:
            return self._import_snapshot_path_inner(path)
        except Exception:  # noqa: BLE001 - 任何异常都写日志并提示
            import traceback

            detail = traceback.format_exc()
            _log_import("失败：" + detail)
            QMessageBox.warning(self, "导入失败", detail.strip().splitlines()[-1])
            return False

    def _import_snapshot_path_inner(self, path: str) -> bool:
        try:
            head = Path(path).read_bytes()[:48]
            _log_import(f"文件头: {head!r}")
        except OSError as exc:
            _log_import(f"读取文件头失败: {exc}")
        try:
            snapshot = load_snapshot(path)
        except SnapshotError as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - 其它异常也要能看到
            QMessageBox.warning(self, "导入失败", f"{type(exc).__name__}: {exc}")
            return False
        # 导入时先摘掉旧基线，避免本次导入被上一份基线反向覆盖；
        # 导入完成后把这份快照记为新的配置基线，供下次扫描套用。
        self._baseline_snapshot = None
        self._apply_snapshot(snapshot, path)
        self._baseline_snapshot = snapshot
        self._baseline_name = Path(path).name
        self._append_log(f"导入快照 {path}（生成于 {snapshot.created_text}）")
        self._append_log(
            f"已把 {self._baseline_name} 设为配置基线：下次扫描时同名网络沿用其"
            "类别 / 电压 / 参考地，新增网络按自动分类"
        )
        _log_import(f"导入成功 {path}")
        return True

    def _apply_snapshot(self, snapshot, path: str = "") -> None:
        demo = snapshot.board.source_path.startswith("(内置示例)")
        self._apply_scan_result(
            snapshot.board,
            snapshot.classifications,
            snapshot.pins,
            snapshot.sheet_index or SheetNetIndex(),
            snapshot.sheet_components,
            snapshot.sheet_paths,
            snapshot.instance_sheets,
            snapshot.top_sheet,
            demo=demo,
            source_note=f"扫描快照 {snapshot.created_text}",
        )
        self.project_combo.clear()
        if snapshot.project_path:
            label = (
                build_demo_project_name()
                if snapshot.project_path == "(demo)"
                else Path(snapshot.project_path).name
            )
            self.project_combo.addItem(f"{label}（快照）", snapshot.project_path)
        note = "（快照来自旧判定规则，已按当前规则重新分类）" if snapshot.reclassified else ""
        self.status_label.setText(
            f"已导入扫描快照（{snapshot.created_text}）：器件 {len(snapshot.board.components)} / "
            f"焊盘 {len(snapshot.board.pads)} / 网络 {len(snapshot.board.nets)}{note}"
        )
        if snapshot.reclassified:
            self._append_log("快照的分类规则版本较旧，导入时已重新分类")

    def _auto_save_snapshot(self) -> None:
        # 自动化测试 / 显式关闭时不要覆盖用户的 last_scan.json
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("CLEARANCE_NO_AUTOSAVE"):
            return
        if self.board is None or self.demo_mode:
            return
        try:
            save_snapshot(default_snapshot_path(), **self._snapshot_kwargs())
        except (OSError, TypeError, ValueError):
            return
        self._append_log(f"已自动保存快照：{default_snapshot_path()}")

    def _restore_last_snapshot(self) -> None:
        path = default_snapshot_path()
        if not path.is_file():
            return
        try:
            snapshot = load_snapshot(path)
        except (SnapshotError, OSError):
            return
        self._apply_snapshot(snapshot, str(path))
        extra = "，已按当前规则重新分类" if snapshot.reclassified else ""
        self.status_label.setText(
            f"已恢复上次扫描快照（{snapshot.created_text}{extra}）。可直接复核，或用「扫描工程」重新读取"
        )
        self._append_log(f"恢复上次扫描快照：{path}")

    def _reclassify(self) -> None:
        if not self.board:
            QMessageBox.information(self, "没有数据", "请先扫描一个工程。")
            return
        self.classifications = classify_board(self.board, self.pins)
        fill_missing_classifications(self.classifications, self.sheet_index)
        assign_secondary_references(self.classifications)
        assign_primary_references(self.classifications)
        if self._baseline_snapshot is not None:
            self._append_log(f"已清除配置基线 {self._baseline_name}")
            self._baseline_snapshot = None
            self._baseline_name = ""
        self.net_panel.set_items(list(self.classifications.values()))
        self.sheet_panel.set_data(
            self.board, self.classifications, self.sheet_index, self.pins
        )
        self._sheet_dirty = False
        self.board_view.set_data(self.board, self.classifications, [])
        self.status_label.setText("已重新执行自动分类，可按需修改后点「重新校验」")
        self._update_pending_card()

    # ------------------------------------------------------------------
    # 间距校验
    # ------------------------------------------------------------------
    def _run_clearance(self) -> None:
        if not self.board:
            QMessageBox.information(self, "没有数据", "请先扫描一个工程。")
            return
        self._start_clearance()

    def _start_clearance(self) -> None:
        rules = self.rules_panel.rules()
        board = self.board
        classifications = self.classifications
        if board is None:
            return

        def task(progress, stop):
            # 副边网络补默认参考地；参考地改成跳变（或改区间）后重算绝对电位
            apply_follow_target(classifications)
            assign_secondary_references(classifications)
            assign_primary_references(classifications)
            apply_reference_potential(classifications)
            progress("校验", "按规则计算焊盘间距…")
            violations = find_violations(
                board,
                classifications,
                rules,
                mounts=board.mounts,
                parts=board.floating_parts,
            )
            progress("汇总", "按器件归并违规…")
            aggregated = aggregate_by_component(violations)
            return violations, aggregated

        def done(result):
            violations, aggregated = result
            self.violations = violations
            self.aggregated = aggregated
            self.violation_panel.set_results(violations, aggregated)
            self.board_view.set_data(board, classifications, violations)
            self.net_panel.refresh()
            self.sheet_panel.refresh_all()
            self.tabs.setCurrentWidget(self.violation_panel)
            self.status_label.setText(
                f"校验完成：{len(aggregated)} 个器件对存在避距违规（焊盘级 {len(violations)} 条）"
            )
            self._append_log(
                f"间距校验：器件对 {len(aggregated)}，焊盘级 {len(violations)}，"
                f"规则来源 {'内置示例' if self.demo_mode else '用户配置'}"
            )

        self._run(task, done, "正在执行间距校验…")

    def _on_rules_changed(self) -> None:
        self._save_rules(quiet=True)
        if self.violations:
            self.status_label.setText(
                "规则已更新，点「间距规则」页右下角「重新校验」重新计算"
            )

    def _on_classification_changed(self) -> None:
        apply_follow_target(self.classifications)
        apply_reference_potential(self.classifications)
        self.sheet_panel.refresh_all()
        self._update_pending_card()
        self._sheet_dirty = False
        if self.violations:
            self.status_label.setText(
                "分类已修改，点「间距规则」页右下角「重新校验」重新计算"
            )

    def _on_sheet_edit(self) -> None:
        """在图纸视图里改了电压：立刻折算绝对电位并刷新两边，不做避距校验。"""
        apply_follow_target(self.classifications)
        apply_reference_potential(self.classifications)
        self.net_panel.refresh()
        self.sheet_panel.refresh_all()
        self._update_pending_card()
        self._sheet_dirty = False
        self.status_label.setText("电压已更新（参考地与绝对电位已同步）")

    def _load_cutout_outlines(self) -> None:
        """从 Altium 读取每个避距槽的闭合轮廓（耗时，单独触发）。"""
        if self.board is None or not self.board.cutouts:
            QMessageBox.information(self, "没有避距槽", "请先扫描一个工程。")
            return

        board = self.board
        slots = [c for c in board.cutouts if not c.is_circle]
        session = self.session

        def task(progress, stop):
            progress("轮廓", "读取板级挖空的闭合轮廓（约 20~30 秒）…")
            if not session.connect():
                raise RuntimeError("未连接 Altium，无法读取轮廓")
            return session.cutout_outlines(board.source_path)

        def done(outlines) -> None:
            matched = 0
            for outline in outlines or []:
                target = self._match_cutout(slots, outline)
                if target is None:
                    continue
                target.points = list(outline.get("points") or [])
                matched += 1
            self.slot_panel.set_cutouts(board.cutouts)
            self.status_label.setText(
                f"已读取 {matched} / {len(slots)} 个避距槽的轮廓坐标"
            )

        self._run(task, done, "正在读取挖空轮廓…")

    @staticmethod
    def _match_cutout(slots, outline):
        """按中心位置 + 尺寸把读到的轮廓配到扫描出的槽上。"""
        best = None
        best_score = 1e9
        for slot in slots:
            distance = ((slot.x - outline["x"]) ** 2 + (slot.y - outline["y"]) ** 2) ** 0.5
            size_delta = abs(slot.width - outline["width"]) + abs(slot.height - outline["height"])
            if distance > 1.0 or size_delta > 1.0:
                continue
            score = distance + size_delta * 0.5
            if score < best_score:
                best, best_score = slot, score
        return best

    def _on_floating_changed(self) -> None:
        if self.board is None:
            return
        self.status_label.setText("无网络器件电位已更新，重新校验时会参与检查")

    def _on_mount_changed(self) -> None:
        if self.board is None:
            return
        self.status_label.setText("安装焊盘电位已更新，重新校验时会参与检查")

    def _on_net_book_changed(self, _index: int) -> None:
        if self._sheet_dirty and self.net_book.currentWidget() is self.sheet_panel:
            self._refresh_sheet_panel()

    def _refresh_sheet_panel(self) -> None:
        if self.board is None:
            return
        self.sheet_panel.set_data(
            self.board, self.classifications, self.sheet_index, self.pins
        )
        self._sheet_dirty = False

    def _update_pending_card(self) -> None:
        """低置信度已并入「当前设计」栏，这里保留空实现供旧调用点使用。"""
        return

    def _update_overview(self) -> None:
        """刷新常驻的「当前设计」栏。"""
        if self.board is None:
            return
        board = self.board
        index = self.sheet_index
        sections = sorted(index.sheets) if index is not None else []
        counts = {"PRIMARY_CONST": 0, "PRIMARY_SWITCHING": 0, "SECONDARY": 0}
        for item in self.classifications.values():
            key = item.net_class.value
            if key in counts:
                counts[key] += 1
        review = sum(1 for item in self.classifications.values() if item.needs_review)
        text = (
            f"<b>{board.name}</b>　{len(board.components)} 器件 · "
            f"{len(board.pads)} 焊盘 · {len([n for n in board.nets if n])} 网络"
            f"　|　原边恒定 {counts['PRIMARY_CONST']} · 原边跳变 "
            f"{counts['PRIMARY_SWITCHING']} · 副边 {counts['SECONDARY']}"
            f"　|　低置信度 {review}"
        )
        if board.source_path:
            text += f"<br><span style='color:#94A3B8'>{board.source_path}</span>"
        if sections:
            shown = "、".join(sections[:8])
            more = f" 等 {len(sections)} 张" if len(sections) > 8 else ""
            text += f"<br><span style='color:#94A3B8'>图纸：{shown}{more}</span>"
        self.info_label.setText(text)

    def _select_violation_in_view(self, index: int) -> None:
        self.board_view.select_violation(index)

    def _update_layers(self) -> None:
        self.board_view.set_layer_visible(self.chk_top.isChecked(), self.chk_bottom.isChecked())

    def _append_log(self, text: str) -> None:
        self._log_lines.append(text)
        del self._log_lines[:-400]
        self.log_view.setPlainText("\n".join(self._log_lines))
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    # ------------------------------------------------------------------
    # 在 Altium 中定位
    # ------------------------------------------------------------------
    def _locate_in_altium(self, designator: str) -> None:
        if not self.session.connected or self.demo_mode or not designator:
            self.status_label.setText(f"已选中 {designator}（未连接 Altium，无法联动定位）")
            return

        def task(progress, _stop):
            progress("定位", f"在 Altium 中定位 {designator}…")
            return self.session.cross_probe(designator, target="pcb")

        self._run(task, lambda _res: self.status_label.setText(f"已在 Altium 中定位 {designator}"),
                  f"正在定位 {designator}…")

    # ------------------------------------------------------------------
    # 任务执行框架
    # ------------------------------------------------------------------
    def _run(self, fn, on_success, busy_text: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "当前任务尚未完成，请等待结束。")
            return
        self.progress.setVisible(True)
        self.btn_cancel.setVisible(True)
        self.status_label.setText(busy_text)
        def deliver(result) -> None:
            """界面回调里的异常必须显示出来，否则界面看起来像卡死。"""
            try:
                on_success(result)
            except Exception as exc:  # noqa: BLE001 - 统一转成界面提示
                message = f"界面刷新失败：{exc}"
                self.status_label.setText(message)
                self._append_log(message)
                QMessageBox.warning(self, "操作失败", message)

        worker = Worker(fn, self)
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(deliver)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_progress(self, stage: str, detail: str) -> None:
        self.status_label.setText(f"{stage}：{detail}")
        self._append_log(f"[{stage}] {detail}")

    def _on_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.status_label.setText("操作失败：" + message.splitlines()[0][:120])
        self._append_log("失败：" + message)
        if self.session.state is ConnectionState.NO_BRIDGE:
            self._update_connection_badge(ConnectionState.NO_BRIDGE)
            answer = QMessageBox.question(
                self,
                "Altium 脚本桥没有响应",
                "eda-agent 已启动，但 Altium 侧的脚本轮询没有响应"
                "（常见原因：Altium 静默 10 分钟后轮询自动退出）。\n\n"
                "是否尝试自动启动 Altium 脚本桥？（会向正在运行的 Altium 发送运行脚本命令）",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                self._relaunch_bridge()
            return
        QMessageBox.warning(self, "操作失败", message)

    def _relaunch_bridge(self) -> None:
        def task(progress, _stop):
            progress("桥接", "正在启动 Altium 脚本轮询（约 10~30 秒）…")
            ok, output = self.session.relaunch_bridge()
            if not ok:
                raise EdaAgentError("Altium 脚本桥启动失败：\n" + output[-400:])
            progress("连接", "重新连接 EDA Agent…")
            state = self.session.connect()
            if state is not ConnectionState.READY:
                raise EdaAgentError(self.session.last_error or "桥接已启动，但重新连接失败")
            return state, self.session.list_projects()

        self._run(task, self._on_connected, "正在启动 Altium 脚本桥…")

    def _cancel_task(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self.status_label.setText("正在取消：当前步骤结束后停止…")
            self.btn_cancel.setEnabled(False)

    def _on_worker_finished(self) -> None:
        self.progress.setVisible(False)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(True)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    # ------------------------------------------------------------------
    # 规则持久化
    # ------------------------------------------------------------------
    def _restore_rules(self) -> None:
        if self._rules_path.is_file():
            try:
                self.rules_panel.set_rules(ClearanceRules.load(self._rules_path))
            except (OSError, ValueError):
                pass

    def _save_rules(self, quiet: bool = False) -> None:
        try:
            self._rules_path.parent.mkdir(parents=True, exist_ok=True)
            self.rules_panel.rules().save(self._rules_path)
        except OSError:
            if not quiet:
                self.status_label.setText("规则保存失败")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_rules(quiet=True)
        self._auto_save_snapshot()
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        self.session.disconnect()
        super().closeEvent(event)
