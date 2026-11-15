"""爬电隔离槽面板：显示扫描到的避距槽与其闭合轮廓顶点坐标。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..model import BoardCutout
from .theme import ACCENT, MUTED, WARN

ROLE_LABELS = {
    "creepage": "爬电隔离",
    "footprint": "器件封装",
    "frame": "板框外形",
}

COLUMNS = ["序号", "归属（可编辑）", "中心 (mm)", "尺寸 (mm)", "顶点数", "轮廓状态"]


class SlotPanel(QWidget):
    refreshRequested = Signal()
    changed = Signal()
    reclassifyRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slots: list[BoardCutout] = []
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("爬电隔离槽（板级挖空）")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.summary = QLabel("尚未扫描")
        self.summary.setObjectName("Hint")
        header.addWidget(self.summary)
        layout.addLayout(header)

        hint = QLabel(
            "这些多边形挖空把沿面路径绕远，用于区分「电气间隙 6 mm」与「爬电距离 8 mm」。"
            "轮廓坐标需要单独读取（要遍历整板铜箔，约 20~30 秒），读取后会随快照保存。"
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Horizontal)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(32)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.Interactive)
        header_view.setStretchLastSection(True)
        for column, width in enumerate((56, 150, 130, 130, 72)):
            self.table.setColumnWidth(column, width)
        self.table.itemSelectionChanged.connect(self._show_points)
        splitter.addWidget(self.table)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        points_title = QLabel("闭合轮廓顶点 (mm)")
        points_title.setObjectName("Hint")
        right_layout.addWidget(points_title)
        self.points_view = QPlainTextEdit()
        self.points_view.setReadOnly(True)
        self.points_view.setPlaceholderText("选择左侧的槽查看坐标；没有坐标时点右上角「读取轮廓坐标」")
        right_layout.addWidget(self.points_view, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.placeholder = QLabel("扫描工程后，这里会列出板上的避距槽。")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("Hint")
        layout.addWidget(self.placeholder)

        self.btn_copy = QPushButton("复制坐标")
        self.btn_copy.setObjectName("Ghost")
        self.btn_copy.setAutoDefault(False)
        self.btn_copy.clicked.connect(self._copy_points)
        self.btn_refresh = QPushButton("读取轮廓坐标")
        self.btn_refresh.setAutoDefault(False)
        self.btn_refresh.setToolTip("从 Altium 读取每个槽的闭合轮廓（需要遍历整板铜箔，约 20~30 秒）")
        self.btn_refresh.clicked.connect(self.refreshRequested.emit)
        self.btn_reclassify = QPushButton("重新判定归属")
        self.btn_reclassify.setObjectName("Ghost")
        self.btn_reclassify.setToolTip(
            "忽略人工归属，按板框 / 器件封装 / 爬电隔离重新自动判定全部挖空"
        )
        self.btn_reclassify.clicked.connect(self.reclassifyRequested.emit)

        footer = QHBoxLayout()
        footer.addWidget(self.btn_refresh)
        footer.addWidget(self.btn_reclassify)
        footer.addWidget(self.btn_copy)
        footer.addStretch(1)
        layout.addLayout(footer)

    # ------------------------------------------------------------------
    def set_cutouts(self, cutouts: list[BoardCutout]) -> None:
        # 圆形挖空已转成安装孔，这里只列多边形挖空（含板框 / 封装 / 爬电隔离）
        self._slots = [c for c in cutouts if not c.is_circle]
        self._rebuild()

    def slots(self) -> list[BoardCutout]:
        return self._slots

    def _rebuild(self) -> None:
        self._loading = True
        self.table.setRowCount(len(self._slots))
        self.placeholder.setVisible(not self._slots)
        self.table.setVisible(bool(self._slots))
        with_points = sum(1 for c in self._slots if c.points)
        self.summary.setText(
            f"{len(self._slots)} 个避距槽 · 已读取坐标 {with_points} 个"
            if self._slots else "没有避距槽"
        )
        for row, cutout in enumerate(self._slots):
            name = QTableWidgetItem(f"S{row + 1}")
            self.table.setItem(row, 0, name)

            role = cutout.role or "creepage"
            combo = QComboBox()
            combo.setObjectName("CellCombo")
            for key, label in ROLE_LABELS.items():
                combo.addItem(label, key)
            combo.setCurrentIndex(max(combo.findData(role), 0))
            combo.currentIndexChanged.connect(
                lambda _index, index=row, box=combo: self._on_role_changed(index, box)
            )
            self.table.setCellWidget(row, 1, combo)

            center = QTableWidgetItem(f"({cutout.x:.2f}, {cutout.y:.2f})")
            center.setForeground(QBrush(QColor(MUTED)))
            self.table.setItem(row, 2, center)

            size = QTableWidgetItem(f"{cutout.width:.2f} × {cutout.height:.2f}")
            self.table.setItem(row, 3, size)

            count = QTableWidgetItem(str(len(cutout.points)) if cutout.points else "—")
            count.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 4, count)

            state_text = "已读取" if cutout.points else "未读取"
            state = QTableWidgetItem(state_text)
            state.setForeground(QBrush(QColor(ACCENT if cutout.points else WARN)))
            self.table.setItem(row, 5, state)

        if self._slots:
            self.table.selectRow(0)
        else:
            self.points_view.clear()
        # 重建后可能仍是第 0 行选中，选择变化信号不会触发，这里强制刷新一次
        self._loading = False
        self._show_points()

    def _on_role_changed(self, index: int, combo: QComboBox) -> None:
        if self._loading or not (0 <= index < len(self._slots)):
            return
        self._slots[index].role = str(combo.currentData() or "creepage")
        self.changed.emit()

    def _current(self) -> BoardCutout | None:
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        if not rows:
            return None
        row = min(rows)
        if 0 <= row < len(self._slots):
            return self._slots[row]
        return None

    def _show_points(self) -> None:
        cutout = self._current()
        if cutout is None or not cutout.points:
            self.points_view.setPlainText(
                "" if cutout is None else "尚未读取该槽的轮廓坐标"
            )
            return
        lines = [f"{index + 1:>3}: {x:9.3f}, {y:9.3f}" for index, (x, y) in enumerate(cutout.points)]
        lines.append("")
        lines.append(f"共 {len(cutout.points)} 个顶点（首点与闭合边自动相连）")
        self.points_view.setPlainText("\n".join(lines))

    def _copy_points(self) -> None:
        cutout = self._current()
        if cutout is None or not cutout.points:
            return
        text = "\n".join(f"{x:.3f},{y:.3f}" for x, y in cutout.points)
        QGuiApplication.clipboard().setText(text)
        self.summary.setText(f"已复制 S{self._slots.index(cutout) + 1} 的 {len(cutout.points)} 个顶点")
