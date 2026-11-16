"""违规结果面板：器件级汇总 + 焊盘级明细 + 导出。"""

from __future__ import annotations

import re

import csv

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..model import (
    NET_CLASS_SHORT_LABELS,
    ComponentViolation,
    NetClass,
    Violation,
    net_class_label,
)
from .theme import ACCENT, DANGER, WARN

_DEVICE_COLUMNS = [
    "器件 A", "器件 B", "网络 A", "网络 B", "类别组合",
    "实测 (mm)", "要求 (mm)", "缺口 (mm)", "位置 (mm)", "依据规则", "类型",
]
_PAD_COLUMNS = [
    "焊盘 A", "网络 A", "焊盘 B", "网络 B",
    "实测 (mm)", "要求 (mm)", "缺口 (mm)", "位置 (mm)", "依据规则", "类型",
]


def _card(title: str, value: str, color: str = ACCENT) -> tuple[QFrame, QLabel]:
    """紧凑指标：标题与数值同一行，高度压到最小。"""
    frame = QFrame()
    frame.setObjectName("Card")
    frame.setMaximumHeight(34)
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(10, 2, 10, 2)
    layout.setSpacing(8)
    caption = QLabel(title)
    caption.setObjectName("CardTitle")
    number = QLabel(value)
    number.setObjectName("CardValue")
    number.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: 700;")
    layout.addWidget(caption)
    layout.addStretch(1)
    layout.addWidget(number)
    return frame, number


def _rule_key(name: str) -> str:
    """规则名去掉具体数值，便于归成一类。"""
    text = re.sub(r"\d+(?:\.\d+)?", "#", name)
    return re.sub(r"\s+", " ", text).strip()


def _rule_label(name: str) -> str:
    """下拉里展示的短名：取第一个数字之前的部分，去掉尾部的括号/标点。"""
    match = re.search(r"\d", name)
    prefix = name[: match.start()] if match else name
    prefix = prefix.strip(" （(·、,，:：")
    prefix = re.sub(r"\s+", " ", prefix).strip()
    return prefix if len(prefix) >= 2 else name.strip()


class ViolationPanel(QWidget):
    MAX_ROWS = 800  # 表格最多铺这么多行，超出只统计不铺行
    locateRequested = Signal(str)
    violationSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._violations: list[Violation] = []
        self._aggregated: list[ComponentViolation] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ---- 统计卡片 ---------------------------------------------
        cards = QHBoxLayout()
        cards.setSpacing(8)
        frame_pairs, self.value_pairs = _card("违规器件对", "0", DANGER)
        frame_deficit, self.value_deficit = _card("最大缺口", "0.00 mm", DANGER)
        cards.addWidget(frame_pairs, 1)
        cards.addWidget(frame_deficit, 1)
        cards.addStretch(1)
        layout.addLayout(cards)

        # ---- 过滤 / 操作 -------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.isolation_only = QCheckBox("只看隔离带（原边↔副边）")
        toolbar.addWidget(self.isolation_only)

        toolbar.addWidget(QLabel("类型"))
        self.severity_filter = QComboBox()
        self.severity_filter.addItem("全部", "")
        self.severity_filter.addItem("仅 ERROR（器件间）", "error")
        self.severity_filter.addItem("仅 WARNING（器件内）", "warning")
        self.severity_filter.setFixedWidth(170)
        toolbar.addWidget(self.severity_filter)

        toolbar.addWidget(QLabel("违规项"))
        self.rule_filter = QComboBox()
        self.rule_filter.addItem("全部违规项", "")
        self.rule_filter.setFixedWidth(220)
        self.rule_filter.setToolTip("按依据规则过滤，例如「焊盘距板框」「隔离带爬电距离」")
        toolbar.addWidget(self.rule_filter)
        toolbar.addStretch(1)

        self.btn_locate = QPushButton("在 Altium 中定位")
        self.btn_locate.setObjectName("Ghost")
        self.btn_export = QPushButton("导出 CSV…")
        toolbar.addWidget(self.btn_locate)
        toolbar.addWidget(self.btn_export)
        layout.addLayout(toolbar)

        # ---- 结果表 -------------------------------------------------
        self.tabs = QTabWidget()
        self.device_table = self._make_table(_DEVICE_COLUMNS)
        self.pad_table = self._make_table(_PAD_COLUMNS)
        self.tabs.addTab(self.device_table, "器件级（推荐）")
        self.tabs.addTab(self.pad_table, "焊盘级明细")
        layout.addWidget(self.tabs, 1)

        self.placeholder = QLabel("核对完网络电压与间距规则后，点「重新校验」生成结果")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("Hint")
        layout.addWidget(self.placeholder)

        self.severity_filter.currentIndexChanged.connect(lambda _: self._rebuild())
        self.rule_filter.currentIndexChanged.connect(lambda _: self._rebuild())
        self.isolation_only.toggled.connect(lambda _: self._rebuild())
        self.btn_export.clicked.connect(self._export_csv)
        self.btn_locate.clicked.connect(self._locate_current)
        self.device_table.doubleClicked.connect(self._locate_current)
        self.pad_table.doubleClicked.connect(self._locate_current)

    # ---- 构建 -----------------------------------------------------
    def _make_table(self, columns: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(29)
        table.setSortingEnabled(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(len(columns) - 2, QHeaderView.Stretch)
        table.setColumnWidth(len(columns) - 1, 76)
        table.setColumnWidth(0, 120)
        table.setColumnWidth(1, 148)
        table.setColumnWidth(2, 150)
        table.setColumnWidth(3, 150)
        if len(columns) == len(_DEVICE_COLUMNS):
            table.setColumnWidth(4, 120)
            table.setColumnWidth(5, 88)
            table.setColumnWidth(6, 88)
            table.setColumnWidth(7, 88)
            table.setColumnWidth(8, 110)
        return table

    # ---- 数据 -----------------------------------------------------
    def set_results(
        self,
        violations: list[Violation],
        aggregated: list[ComponentViolation],
        current: int = -1,
    ) -> None:
        self._violations = violations
        self._aggregated = aggregated
        self._refresh_rule_filter(violations)
        self.value_pairs.setText(str(len(aggregated)))
        max_deficit = max((v.deficit_mm for v in violations), default=0.0)
        self.value_deficit.setText(f"{max_deficit:.2f} mm")
        self.placeholder.setVisible(not violations)
        self._rebuild()
        if current >= 0:
            self.select_pad_violation(current)

    def _refresh_rule_filter(self, violations: list[Violation]) -> None:
        """按规则名归类（去掉具体数值），例如「焊盘距板框 0.4 mm」→「焊盘距板框」"""
        current = self.rule_filter.currentData() or ""
        labels: dict[str, str] = {}
        for item in violations:
            raw_name = str(getattr(item, "rule_name", "") or "")
            if not raw_name:
                continue
            key = _rule_key(raw_name)
            if not key:
                continue
            labels.setdefault(key, _rule_label(raw_name))

        self.rule_filter.blockSignals(True)
        self.rule_filter.clear()
        self.rule_filter.addItem("全部违规项", "")
        for key in sorted(labels):
            self.rule_filter.addItem(labels[key], key)
        index = self.rule_filter.findData(current)
        self.rule_filter.setCurrentIndex(max(index, 0))
        self.rule_filter.blockSignals(False)

    def clear(self) -> None:
        self.set_results([], [])

    def _passes(self, violation: Violation | ComponentViolation) -> bool:
        wanted_rule = self.rule_filter.currentData()
        if wanted_rule and _rule_key(getattr(violation, "rule_name", "")) != wanted_rule:
            return False
        wanted = self.severity_filter.currentData()
        if wanted and violation.severity != wanted:
            return False
        if self.isolation_only.isChecked():
            pair = {violation.class_a, violation.class_b}
            has_primary = bool(pair & {NetClass.PRIMARY_CONST, NetClass.PRIMARY_SWITCHING})
            if not (has_primary and NetClass.SECONDARY in pair):
                return False
        return True

    def _rebuild(self) -> None:
        devices_all = [item for item in self._aggregated if self._passes(item)]
        pads_all = [item for item in self._violations if self._passes(item)]
        # 上万行 QTableWidget 会让界面像卡死，这里只铺前 MAX_ROWS 行；
        # 卡片里仍然显示过滤后的真实条数。
        devices = devices_all[: self.MAX_ROWS]
        pads = pads_all[: self.MAX_ROWS]
        self.value_pairs.setText(str(len(devices_all)))
        self.value_pairs.setToolTip(
            f"共 {len(devices_all)} 对；表格最多展示 {self.MAX_ROWS} 行"
            if len(devices_all) > self.MAX_ROWS
            else f"共 {len(devices_all)} 对"
        )

        self.device_table.setRowCount(len(devices))
        bold = QFont()
        bold.setBold(True)
        for row, item in enumerate(devices):
            same_component = item.designator_a == item.designator_b
            values = [
                item.designator_a,
                "（同一器件内部）" if same_component else item.designator_b,
                item.net_a,
                item.net_b,
                f"{NET_CLASS_SHORT_LABELS[item.class_a]} / {NET_CLASS_SHORT_LABELS[item.class_b]}",
                f"{item.actual_mm:.2f}",
                f"{item.required_mm:.2f}",
                f"{item.deficit_mm:.2f}",
                f"({item.x:.1f}, {item.y:.1f})",
                item.rule_name,
                "WARNING" if item.is_warning else "ERROR",
            ]
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                if column == len(values) - 1:
                    cell.setForeground(
                        QBrush(QColor(WARN if item.is_warning else DANGER))
                    )
                if column in (5, 6, 7):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column == 7:
                    cell.setForeground(QBrush(QColor(DANGER)))
                    cell.setFont(bold)
                self.device_table.setItem(row, column, cell)

        self.pad_table.setRowCount(len(pads))
        for row, item in enumerate(pads):
            values = [
                f"{item.pad_a} ({item.net_a})",
                item.net_a,
                f"{item.pad_b} ({item.net_b})",
                item.net_b,
                f"{item.actual_mm:.2f}",
                f"{item.required_mm:.2f}",
                f"{item.deficit_mm:.2f}",
                f"({item.x:.1f}, {item.y:.1f})",
                item.rule_name,
                "WARNING" if item.is_warning else "ERROR",
            ]
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                if column == len(values) - 1:
                    cell.setForeground(
                        QBrush(QColor(WARN if item.is_warning else DANGER))
                    )
                if column in (4, 5, 6):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column == 6:
                    cell.setForeground(QBrush(QColor(DANGER)))
                if column == 8 and item.rule_name.startswith("隔离"):
                    cell.setForeground(QBrush(QColor(WARN)))
                self.pad_table.setItem(row, column, cell)

        if not devices:
            self.placeholder.setText("没有符合条件的避距违规")
            self.placeholder.setVisible(True)
        else:
            self.placeholder.setVisible(False)

    # ---- 选中 / 定位 ----------------------------------------------
    def current_device(self) -> ComponentViolation | None:
        row = self.device_table.currentRow()
        devices = [item for item in self._aggregated if self._passes(item)]
        return devices[row] if 0 <= row < len(devices) else None

    def current_pad_violation_index(self) -> int:
        row = self.pad_table.currentRow()
        if row < 0:
            return -1
        filtered = [item for item in self._violations if self._passes(item)]
        if row >= len(filtered):
            return -1
        target = filtered[row]
        for index, item in enumerate(self._violations):
            if item is target:
                return index
        return -1

    def select_pad_violation(self, index: int) -> None:
        if not (0 <= index < len(self._violations)):
            return
        target = self._violations[index]
        filtered = [item for item in self._violations if self._passes(item)]
        try:
            row = filtered.index(target)
        except ValueError:
            return
        self.tabs.setCurrentWidget(self.pad_table)
        self.pad_table.selectRow(row)
        self.pad_table.scrollToItem(self.pad_table.item(row, 0))

    def _locate_current(self) -> None:
        pad_index = self.current_pad_violation_index()
        if pad_index >= 0:
            self.violationSelected.emit(pad_index)
            item = self._violations[pad_index]
            designator = item.item_a or item.item_b
            if designator:
                self.locateRequested.emit(designator)
            return
        device = self.current_device()
        if device:
            self.locateRequested.emit(device.designator_a or device.designator_b)

    # ---- 导出 -----------------------------------------------------
    def _export_csv(self) -> None:
        if not self._violations:
            QMessageBox.information(self, "无数据", "当前没有可导出的违规记录")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出违规清单", "clearance_violations.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "器件A", "器件B", "网络A", "网络B", "类别A", "类别B",
                    "实测间距(mm)", "要求间距(mm)", "缺口(mm)", "X(mm)", "Y(mm)", "规则", "类型",
                ])
                for item in self._aggregated:
                    writer.writerow([
                        item.designator_a, item.designator_b, item.net_a, item.net_b,
                        net_class_label(item.class_a), net_class_label(item.class_b),
                        f"{item.actual_mm:.3f}", f"{item.required_mm:.3f}",
                        f"{item.deficit_mm:.3f}", f"{item.x:.3f}", f"{item.y:.3f}",
                        item.rule_name,
                        "WARNING" if item.is_warning else "ERROR",
                    ])
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "已导出", f"共 {len(self._aggregated)} 条违规记录\n{path}")
