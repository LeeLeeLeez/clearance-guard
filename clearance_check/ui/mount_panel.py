"""安装焊盘面板：为机械安装孔/安装焊盘指定电位（原边/副边、电压等级）。

安装焊盘在板上通常不带网络或挂在一个与机械结构无关的网络上，它的实际电位由
结构决定（例如固定在机壳、散热器或某一段母线上），因此必须由用户指定后才能
参与避距检查。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush, QDoubleValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..model import NET_CLASS_LABELS, NetClass, MountPoint, ORDERED_NET_CLASSES
from .theme import MUTED, WARN

COLUMNS = [
    "名称",
    "位置 (mm)",
    "孔径 (mm)",
    "外径 (mm)",
    "板上网络",
    "电位归属",
    "电压 (V)",
    "下限 (V)",
    "上限 (V)",
    "参与检查",
]


class MountPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mounts: list[MountPoint] = []
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("安装焊盘（机械结构连接点）")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.summary = QLabel("尚未扫描")
        self.summary.setObjectName("Hint")
        header.addWidget(self.summary)
        layout.addLayout(header)

        hint = QLabel(
            "安装孔通过螺钉连接到机壳、散热器或母线，其电位不由板上网络决定。"
            "默认按「副边」处理；若实际固定在原边母线上，请改成原边并填写电压。"
            "勾选「参与检查」后作为独立对象参与避距计算。"
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
        for column, width in enumerate((110, 128, 96, 96, 130, 150, 92, 92, 92)):
            self.table.setColumnWidth(column, width)
        self.table.itemChanged.connect(self._on_check_changed)
        layout.addWidget(self.table, 1)

        self.placeholder = QLabel("扫描工程后，这里会列出板上的安装焊盘 / 安装孔。")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("Hint")
        layout.addWidget(self.placeholder)

    # ------------------------------------------------------------------
    def set_mounts(self, mounts: list[MountPoint]) -> None:
        self._mounts = list(mounts)
        self._rebuild()

    def mounts(self) -> list[MountPoint]:
        return self._mounts

    def assigned_count(self) -> int:
        return sum(1 for mount in self._mounts if mount.assigned)

    # ------------------------------------------------------------------
    def _rebuild(self) -> None:
        self.table.setRowCount(0)
        if not self._mounts:
            self.table.setVisible(False)
            self.placeholder.setVisible(True)
            self.summary.setText("没有检测到安装焊盘")
            return

        self.table.setVisible(True)
        self.placeholder.setVisible(False)
        self.table.setRowCount(len(self._mounts))
        self._loading = True

        for row, mount in enumerate(self._mounts):
            name = QTableWidgetItem(mount.label)
            name.setToolTip(f"来源：{mount.note or '板上焊盘'}")
            self.table.setItem(row, 0, name)

            position = QTableWidgetItem(f"({mount.x:.2f}, {mount.y:.2f})")
            position.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 1, position)

            hole = QTableWidgetItem(f"{mount.hole:.2f}")
            hole.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, hole)

            width_mm = max(mount.width, mount.height)
            if abs(mount.width - mount.height) > 0.05:
                outer_text = f"{mount.width:.2f} × {mount.height:.2f}"
            else:
                outer_text = f"{width_mm:.2f}"
            outer = QTableWidgetItem(outer_text)
            outer.setTextAlignment(Qt.AlignCenter)
            outer.setToolTip("避距按这个外径（焊盘外沿）计算，不是孔径")
            if width_mm < mount.hole - 1e-6:
                outer.setForeground(QBrush(QColor(WARN)))
                outer.setToolTip("外径不大于孔径，请检查焊盘尺寸")
            self.table.setItem(row, 3, outer)

            net = QTableWidgetItem(mount.net or "（无网络）")
            if not mount.net:
                net.setForeground(QBrush(QColor(MUTED)))
            self.table.setItem(row, 4, net)

            if mount.net_class is NetClass.UNKNOWN:
                mount.net_class = NetClass.SECONDARY
            combo = QComboBox()
            combo.setObjectName("CellCombo")
            for net_class in ORDERED_NET_CLASSES:
                combo.addItem(NET_CLASS_LABELS[net_class], net_class.value)
            position = combo.findData(mount.net_class.value)
            combo.setCurrentIndex(position if position >= 0 else combo.findData(
                NetClass.SECONDARY.value))
            combo.currentIndexChanged.connect(
                lambda _index, m=mount, box=combo: self._on_class_changed(m, box)
            )
            self.table.setCellWidget(row, 5, combo)

            for column, value in ((6, mount.voltage), (7, mount.voltage_min), (8, mount.voltage_max)):
                editor = QLineEdit("" if value is None else f"{value:g}")
                editor.setPlaceholderText("—")
                editor.setValidator(QDoubleValidator(0.0, 3000.0, 2, editor))
                editor.setAlignment(Qt.AlignCenter)
                editor.editingFinished.connect(
                    lambda m=mount, col=column, widget=editor: self._on_voltage_changed(m, col, widget)
                )
                self.table.setCellWidget(row, column, editor)

            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked if mount.enabled else Qt.Unchecked)
            check.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 8, check)

        self._loading = False
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        total = len(self._mounts)
        assigned = self.assigned_count()
        self.summary.setText(f"{total} 个安装焊盘 · 已指定电位 {assigned} 个")
        if total and assigned < total:
            self.summary.setStyleSheet(f"color: {WARN};")
        else:
            self.summary.setStyleSheet(f"color: {MUTED};")

    # ------------------------------------------------------------------
    def _on_class_changed(self, mount: MountPoint, combo: QComboBox) -> None:
        if self._loading:
            return
        mount.net_class = NetClass(combo.currentData())
        if mount.net_class is NetClass.UNKNOWN:
            mount.enabled = False
        else:
            mount.enabled = True
        self._refresh_summary()
        self.changed.emit()

    def _on_voltage_changed(self, mount: MountPoint, column: int, editor: QLineEdit) -> None:
        if self._loading:
            return
        text = editor.text().strip()
        value: float | None
        if not text:
            value = None
        else:
            try:
                value = float(text)
            except ValueError:
                return
        if column == 6:
            mount.voltage = value
            if value is not None:
                mount.voltage_min = mount.voltage_min if mount.voltage_min is not None else value
                mount.voltage_max = mount.voltage_max if mount.voltage_max is not None else value
        elif column == 7:
            mount.voltage_min = value
        else:
            mount.voltage_max = value
        self.changed.emit()

    def _on_check_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 8:
            return
        row = item.row()
        if not (0 <= row < len(self._mounts)):
            return
        self._mounts[row].enabled = item.checkState() == Qt.Checked
        self._refresh_summary()
        self.changed.emit()
