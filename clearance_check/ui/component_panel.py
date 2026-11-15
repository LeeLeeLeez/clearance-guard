"""器件列表面板：搜索并浏览板上所有器件。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
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

from ..model import Board, Component
from .theme import ACCENT, MUTED, WARN

COLUMNS = ["位号", "注释", "封装", "类别", "层", "X (mm)", "Y (mm)", "旋转", "焊盘数"]

CATEGORY_COLUMN = 3
CATEGORIES = [
    ("", "普通器件"),
    ("burn_terminal", "烧录端子"),
]


class ComponentPanel(QWidget):
    """器件列表：按位号 / 注释 / 封装模糊搜索，可编辑器件类别。"""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._components: list[tuple[Component, int]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("器件列表")
        title.setObjectName("SectionTitle")
        header.addWidget(title)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索位号 / 注释 / 封装，例如 T21、aux_core、QFN")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        header.addWidget(self.search, 1)

        self.summary = QLabel("尚未扫描")
        self.summary.setObjectName("Hint")
        header.addWidget(self.summary)
        layout.addLayout(header)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        # 含下拉框单元格，关闭排序避免控件与行错位
        self.table.setSortingEnabled(False)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.Interactive)
        header_view.setStretchLastSection(True)
        for column, width in enumerate((110, 190, 170, 110, 112, 92, 92, 92, 66)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)

        self.placeholder = QLabel("扫描工程后，这里会列出板上所有器件。")
        self.placeholder.setObjectName("Hint")
        self.placeholder.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.placeholder)

    # ------------------------------------------------------------------
    def set_components(self, board: Board | None) -> None:
        self._components = []
        if board is not None:
            for designator, comp in board.components.items():
                pads = len(board.pads_of_component(designator))
                self._components.append((comp, pads))
        self._components.sort(key=lambda item: item[0].designator)
        self.summary.setText(f"{len(self._components)} 个器件" if self._components else "没有器件")
        self.placeholder.setVisible(not self._components)
        self._apply_filter()

    def _on_category_changed(self, comp: Component, combo: QComboBox) -> None:
        category = str(combo.currentData() or "")
        if comp.category == category:
            return
        comp.category = category
        self.changed.emit()

    def _apply_filter(self) -> None:
        keyword = self.search.text().strip().lower()
        rows = []
        for comp, pads in self._components:
            haystack = " ".join(
                filter(None, [comp.designator, comp.comment, comp.footprint])
            ).lower()
            if keyword and keyword not in haystack:
                continue
            rows.append((comp, pads))

        self.table.setRowCount(len(rows))
        for row, (comp, pads) in enumerate(rows):
            cells = [
                comp.designator,
                comp.comment or "",
                comp.footprint or "",
                "",  # 类别列用下拉框
                (comp.layer or "").replace("TOP", "顶层").replace("BOTTOM", "底层"),
                f"{comp.x:.2f}",
                f"{comp.y:.2f}",
                f"{comp.rotation:.0f}°" if getattr(comp, "rotation", 0) else "0°",
                str(pads),
            ]
            for column, text in enumerate(cells):
                if column == CATEGORY_COLUMN:
                    combo = QComboBox()
                    combo.setObjectName("CellCombo")
                    for key, label in CATEGORIES:
                        combo.addItem(label, key)
                    index = max(combo.findData(comp.category or ""), 0)
                    combo.setCurrentIndex(index)
                    combo.currentIndexChanged.connect(
                        lambda _i, c=comp, box=combo: self._on_category_changed(c, box)
                    )
                    self.table.setCellWidget(row, CATEGORY_COLUMN, combo)
                    continue
                item = QTableWidgetItem(text)
                if column >= 5:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column in (1, 2, 4):
                    item.setForeground(QBrush(QColor(MUTED)))
                if column == 8 and pads == 0:
                    item.setForeground(QBrush(QColor(WARN)))
                self.table.setItem(row, column, item)
        if keyword:
            self.summary.setText(
                f"命中 {len(rows)} / {len(self._components)} 个器件"
            )
            self.summary.setStyleSheet(f"color: {ACCENT};")
        else:
            self.summary.setText(f"{len(self._components)} 个器件")
            self.summary.setStyleSheet("")
