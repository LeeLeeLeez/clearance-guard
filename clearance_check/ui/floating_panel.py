"""无网络器件面板：为没有网络的器件（磁芯、结构件、未连线器件）指定电位。

这类器件在 PCB 上没有可用的网络，但它们的金属/磁芯与周围导线仍有爬电要求，
例如靠近高压 MOS 漏极的磁芯。必须由用户指定电位后才能参与避距检查。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..model import NET_CLASS_LABELS, ORDERED_NET_CLASSES, FloatingPart, NetClass
from .theme import ACCENT, MUTED, TEXT, WARN

COLUMNS = [
    "位号",
    "注释 / 封装",
    "位置 (mm)",
    "尺寸 (mm)",
    "焊盘数",
    "电位归属",
    "电压 (V)",
    "下限 (V)",
    "上限 (V)",
    "参与检查",
]


def _spin(value: float | None, suffix: str = " mm", low: float = 0.1, high: float = 200.0) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(low, high)
    box.setDecimals(2)
    box.setSingleStep(0.5)
    box.setSuffix(suffix)
    box.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    box.setValue(float(value) if value else 0.0)
    return box


class FloatingPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parts: list[FloatingPart] = []
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("无网络器件（磁芯 / 结构件 / 未连线器件）")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.summary = QLabel("尚未扫描")
        self.summary.setObjectName("Hint")
        header.addWidget(self.summary)
        layout.addLayout(header)

        hint = QLabel(
            "这些器件上没有任何网络（例如靠近高压 MOS 漏极的磁芯），电位需要人工指定。"
            "外形尺寸由 PCB 桥接读取（器件包围盒，不可编辑）；指定电位并勾选「参与检查」后，"
            "以该外形（有焊盘时用焊盘）参与避距计算，结果里以位号出现。"
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(32)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.Interactive)
        header_view.setStretchLastSection(True)
        for column, width in enumerate((96, 240, 132, 140, 80, 168, 104, 104, 104)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)

        self.placeholder = QLabel("扫描工程后，这里会列出没有任何网络的器件。")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("Hint")
        layout.addWidget(self.placeholder)

    # ------------------------------------------------------------------
    def set_parts(self, parts: list[FloatingPart]) -> None:
        self._parts = list(parts)
        self._rebuild()

    def parts(self) -> list[FloatingPart]:
        return self._parts

    def assigned_count(self) -> int:
        return sum(1 for part in self._parts if part.assigned)

    # ------------------------------------------------------------------
    def _rebuild(self) -> None:
        self._loading = True
        self.table.setRowCount(len(self._parts))
        self.placeholder.setVisible(not self._parts)
        self.table.setVisible(bool(self._parts))
        self.summary.setText(
            f"{len(self._parts)} 个无网络器件 · 已指定 {self.assigned_count()} 个"
            if self._parts else "没有无网络器件"
        )

        for row, part in enumerate(self._parts):
            name = QTableWidgetItem(part.designator)
            name.setForeground(QBrush(QColor(WARN if not part.assigned else TEXT)))
            self.table.setItem(row, 0, name)

            detail = QTableWidgetItem(
                f"{part.comment} · {part.footprint}" if part.footprint else part.comment
            )
            detail.setForeground(QBrush(QColor(MUTED)))
            detail.setToolTip(f"{part.comment}\n{part.footprint}")
            self.table.setItem(row, 1, detail)

            pos = QTableWidgetItem(f"({part.x:.1f}, {part.y:.1f})")
            pos.setForeground(QBrush(QColor(MUTED)))
            self.table.setItem(row, 2, pos)

            if part.width and part.height:
                size_text = f"{part.width:.2f} × {part.height:.2f}"
                size_color = TEXT
            else:
                size_text = "未读到外形"
                size_color = WARN
            size = QTableWidgetItem(size_text)
            size.setTextAlignment(Qt.AlignCenter)
            size.setForeground(QBrush(QColor(size_color)))
            size.setToolTip("外形尺寸由 PCB 桥接读取（器件包围盒），无需手工输入")
            self.table.setItem(row, 3, size)

            count = QTableWidgetItem(str(part.pad_count) if part.pad_count else "无焊盘")
            count.setForeground(QBrush(QColor(MUTED)))
            self.table.setItem(row, 4, count)

            combo = QComboBox()
            combo.setObjectName("CellCombo")
            for net_class in ORDERED_NET_CLASSES:
                combo.addItem(NET_CLASS_LABELS[net_class], net_class.value)
            combo.insertItem(0, "未指定", NetClass.UNKNOWN.value)
            position = combo.findData(part.net_class.value)
            combo.setCurrentIndex(position if position >= 0 else 0)
            combo.setToolTip("该器件整体的电位归属")
            combo.currentIndexChanged.connect(
                lambda _index, index=row, box=combo: self._set_class(index, box)
            )
            self.table.setCellWidget(row, 5, combo)

            for column, field in ((6, "voltage"), (7, "voltage_min"), (8, "voltage_max")):
                value = getattr(part, field)
                spin = _spin(value, " V", 0.0, 2000.0)
                spin.setObjectName("CellSpin")
                spin.valueChanged.connect(
                    lambda v, index=row, f=field: self._set_value(index, f, v)
                )
                self.table.setCellWidget(row, column, spin)

            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked if part.enabled else Qt.Unchecked)
            check.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 9, check)

        self._loading = False

    # ------------------------------------------------------------------
    def _set_class(self, index: int, combo: QComboBox) -> None:
        if self._loading or not (0 <= index < len(self._parts)):
            return
        part = self._parts[index]
        part.net_class = NetClass(combo.currentData())
        self.changed.emit()

    def _set_value(self, index: int, field: str, value: float) -> None:
        if self._loading or not (0 <= index < len(self._parts)):
            return
        part = self._parts[index]
        setattr(part, field, float(value) if value > 0 else None)
        self.changed.emit()

    def _on_check_changed(self, cell: QTableWidgetItem) -> None:
        if self._loading or cell.column() != 9:
            return
        part = self._parts[cell.row()]
        part.enabled = cell.checkState() == Qt.Checked
        self.changed.emit()
