"""网络电压分类面板：展示自动判定结果，允许人工修正与确认。"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    QStringListModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..model import (
    NET_CLASS_LABELS,
    NetClass,
    NetClassification,
    ORDERED_NET_CLASSES,
    default_primary_ground,
    default_secondary_ground,
    named_net_candidates,
    editable_voltage_columns,
    net_class_from_label,
    normalize_voltage_fields,
    ref_ground_voltage_text,
    sync_local_voltage,
    sync_peak_voltage,
)
from ..analysis.classify import apply_follow_target
from .theme import ACCENT, CLASS_COLORS, MUTED, OK, WARN

COLUMNS = [
    "网络名",
    "电压类别",
    "电压等级 (V)",
    "下限 (V)",
    "上限 (V)",
    "参考地网络",
    "参考地电压",
    "置信度",
    "判定依据",
    "跟随网络",
]
FOLLOW_COLUMN = len(COLUMNS) - 1


class NetClassModel(QAbstractTableModel):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[NetClassification] = []
        self._by_net: dict[str, NetClassification] = {}

    # ---- Qt 接口 --------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        column = index.column()

        if role == Qt.DisplayRole:
            if column == 0:
                return item.net
            if column == 1:
                return NET_CLASS_LABELS[item.net_class]
            if column in (2, 3, 4):
                value = {2: item.voltage, 3: item.voltage_min, 4: item.voltage_max}[column]
                return "—" if value is None else f"{value:.2f}"
            if column == 5:
                return item.ref_ground or "—"
            if column == 6:
                return ref_ground_voltage_text(item.ref_ground, self._by_net)
            if column == 7:
                return "✓" if item.user_edited else f"{item.confidence:.0%}"
            if column == 8:
                return "；".join(item.reasons)
            if column == FOLLOW_COLUMN:
                return item.follow_net or "—"
        if role == Qt.EditRole and column in (2, 3, 4):
            value = {2: item.voltage, 3: item.voltage_min, 4: item.voltage_max}[column]
            return "" if value is None else f"{value:.2f}"
        if role == Qt.ForegroundRole:
            if column == 1:
                return QBrush(QColor(CLASS_COLORS.get(item.net_class.value, CLASS_COLORS["UNKNOWN"])))
            if column in (5, 6) and item.ref_ground:
                return QBrush(QColor(ACCENT))
            if column == 8:
                return QBrush(QColor(MUTED))
            if column == 7:
                if item.user_edited:
                    return QBrush(QColor(OK))
                if item.needs_review:
                    return QBrush(QColor(WARN))
        if role == Qt.BackgroundRole:
            if item.needs_review:
                return QBrush(QColor(240, 169, 59, 22))
            if item.net_class is NetClass.UNKNOWN:
                return QBrush(QColor(239, 78, 91, 26))
        if role == Qt.ToolTipRole:
            text = "\n".join(item.reasons) or item.net
            if item.ref_ground:
                text += f"\n参考地：{item.ref_ground}（{item.ref_ground_reason}）"
            if item.user_edited:
                text += f"\n已人工修改（自动判定置信度 {item.confidence:.0%}）"
            return text
        if role == Qt.TextAlignmentRole and column in (2, 3, 4, 6, 7):
            return int(Qt.AlignCenter)
        return None

    def setData(self, index: QModelIndex, value, role=Qt.EditRole) -> bool:  # noqa: N802
        if not index.isValid():
            return False
        item = self._items[index.row()]
        column = index.column()

        if role != Qt.EditRole:
            return False

        if column == FOLLOW_COLUMN:
            target = str(value or "").strip()
            if target in ("—", "（不跟随）") or target not in self._by_net:
                target = ""
            item.follow_net = target
            if target:
                apply_follow_target(self._by_net)
                item.user_edited = True
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self._items) - 1, len(COLUMNS) - 1)
            )
            self.changed.emit()
            return True

        if column == 1:
            # 传进来的是显示文案，先转成枚举再参与后续判断
            net_class = net_class_from_label(str(value))
            item.net_class = net_class
            normalize_voltage_fields(item)
            if "GND" not in item.net.upper():
                if net_class is NetClass.SECONDARY and (
                    not item.ref_ground or item.ref_ground.startswith("PGND")
                ):
                    ground = default_secondary_ground(self._by_net.keys())
                    if ground and ground != item.net:
                        item.ref_ground = ground
                        item.ref_ground_reason = "副边网络默认参考 SGND"
                elif net_class is NetClass.PRIMARY_CONST and (
                    not item.ref_ground or item.ref_ground.startswith("SGND")
                ):
                    ground = default_primary_ground(self._by_net.keys())
                    if ground and ground != item.net:
                        item.ref_ground = ground
                        item.ref_ground_reason = "原边恒定网络默认参考 PGND"
            item.user_edited = True
            sync_local_voltage(item, self._by_net)
            item.needs_review = item.net_class is NetClass.UNKNOWN or item.voltage is None
            self.dataChanged.emit(self.index(index.row(), 1), self.index(index.row(), 8))
            self.changed.emit()
            return True

        if column in (2, 3, 4):
            text = str(value).strip()
            if text in ("", "—", "-"):
                parsed = None
            else:
                try:
                    parsed = float(text)
                except ValueError:
                    return False
            if column == 2:
                item.voltage = parsed
                if item.voltage_min is None:
                    item.voltage_min = parsed
                if item.voltage_max is None:
                    item.voltage_max = parsed
                if item.net_class is not NetClass.PRIMARY_SWITCHING:
                    # 恒定电平：上下限跟随电压等级
                    normalize_voltage_fields(item)
            elif column == 3:
                item.voltage_min = parsed
            else:
                item.voltage_max = parsed
            # 上下限填反了就自动纠正
            if (item.voltage_min is not None and item.voltage_max is not None
                    and item.voltage_min > item.voltage_max):
                item.voltage_min, item.voltage_max = item.voltage_max, item.voltage_min
            sync_peak_voltage(item)
            sync_local_voltage(item, self._by_net)
            item.user_edited = True
            item.needs_review = item.net_class is NetClass.UNKNOWN or item.voltage is None
            self.dataChanged.emit(self.index(index.row(), 2), self.index(index.row(), 8))
            self.changed.emit()
            return True
        return False

    def flags(self, index: QModelIndex):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if not index.isValid():
            return base
        column = index.column()
        if column == 1 or column == FOLLOW_COLUMN:
            return base | Qt.ItemIsEditable
        if column in (2, 3, 4):
            item = self._items[index.row()]
            if (column - 2) in editable_voltage_columns(item.net_class):
                return base | Qt.ItemIsEditable
        return base

    # ---- 数据 -----------------------------------------------------
    def set_items(self, items: list[NetClassification]) -> None:
        self.beginResetModel()
        self._items = items
        self._by_net = {item.net: item for item in items}
        self.endResetModel()

    @property
    def items(self) -> list[NetClassification]:
        return self._items

    def item_at(self, row: int) -> NetClassification | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def named_nets(self) -> list[str]:
        return named_net_candidates(self._by_net.keys())

    def review_count(self) -> int:
        """低置信度 / 电压未定的网络数量。"""
        return sum(1 for item in self._items if item.needs_review)


class _ClassDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):  # noqa: N802
        if index.column() == 1:
            combo = QComboBox(parent)
            for net_class in ORDERED_NET_CLASSES:
                combo.addItem(NET_CLASS_LABELS[net_class], net_class.value)
            return combo
        if index.column() == FOLLOW_COLUMN:
            model = index.model()
            source = getattr(model, "sourceModel", lambda: model)()
            edit = QLineEdit(parent)
            edit.setPlaceholderText("输入筛选…")
            completer = QCompleter(QStringListModel(source.named_nets(), edit), edit)
            completer.setFilterMode(Qt.MatchContains)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setMaxVisibleItems(10)
            edit.setCompleter(completer)
            return edit
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):  # noqa: N802
        if index.column() == 1 and isinstance(editor, QComboBox):
            current = index.data(Qt.DisplayRole)
            position = editor.findText(current)
            editor.setCurrentIndex(max(0, position))
            return
        if index.column() == FOLLOW_COLUMN and isinstance(editor, QLineEdit):
            current = index.data(Qt.DisplayRole) or ""
            editor.setText("" if current == "—" else current)
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):  # noqa: N802
        if index.column() == 1 and isinstance(editor, QComboBox):
            model.setData(index, editor.currentText(), Qt.EditRole)
            return
        if index.column() == FOLLOW_COLUMN and isinstance(editor, QLineEdit):
            model.setData(index, editor.text().strip(), Qt.EditRole)
            return
        super().setModelData(editor, model, index)


class _NetFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""

    def set_text(self, text: str) -> None:
        self.text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent) -> bool:  # noqa: N802
        model: NetClassModel = self.sourceModel()
        item = model.item_at(source_row)
        if item is None:
            return False
        if self.text and self.text not in item.net.lower():
            return False
        return True


class NetPanel(QWidget):
    """分类结果表格 + 工具栏。"""

    changed = Signal()
    recalcRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = NetClassModel(self)
        self.proxy = _NetFilter(self)
        self.proxy.setSourceModel(self.model)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索网络名…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        toolbar.addWidget(self.search)

        toolbar.addStretch(1)

        self.summary = QLabel("尚未扫描")
        self.summary.setObjectName("Hint")
        toolbar.addWidget(self.summary)

        self.btn_recalc = QPushButton("重新自动分类")
        self.btn_recalc.setObjectName("Ghost")
        toolbar.addWidget(self.btn_recalc)
        layout.addLayout(toolbar)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.verticalHeader().setVisible(False)
        self.table.setItemDelegate(_ClassDelegate(self.table))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self.table.setColumnWidth(0, 210)
        self.table.setColumnWidth(1, 196)
        for column in (2, 3, 4):
            self.table.setColumnWidth(column, 92)
        self.table.setColumnWidth(5, 150)
        self.table.setColumnWidth(6, 172)
        self.table.setColumnWidth(7, 72)
        self.table.setColumnWidth(8, 200)
        self.table.setColumnWidth(FOLLOW_COLUMN, 150)
        layout.addWidget(self.table, 1)

        hint = QLabel(
            "提示：双击「电压类别」「电压等级」「下限/上限」可直接修改，改完点「重新校验」即可。"
            "标黄行表示自动判定置信度低或电压未定。"
        )
        hint.setObjectName("Hint")
        layout.addWidget(hint)

        self.search.textChanged.connect(self.proxy.set_text)
        self.btn_recalc.clicked.connect(self.recalcRequested.emit)
        self.model.changed.connect(self._on_changed)
        self.model.dataChanged.connect(lambda *_: self._refresh_summary())

    # ---- 对外 -----------------------------------------------------
    def set_items(self, items: list[NetClassification]) -> None:
        self.model.set_items(items)
        self._refresh_summary()

    def _on_changed(self) -> None:
        self._refresh_summary()
        self.changed.emit()

    def _refresh_summary(self) -> None:
        items = self.model.items
        if not items:
            self.summary.setText("尚未扫描")
            return
        counts: dict[str, int] = {}
        for item in items:
            counts[item.net_class.value] = counts.get(item.net_class.value, 0) + 1
        review = self.model.review_count()
        self.summary.setText(
            f"共 {len(items)} 个网络 · 原边 {counts.get('PRIMARY_CONST', 0)} / "
            f"跳变 {counts.get('PRIMARY_SWITCHING', 0)} / 副边 {counts.get('SECONDARY', 0)} "
            f"· 低置信度 {review}"
        )

    def refresh(self) -> None:
        """外部（图纸视图）改过分类后，刷新整张表。"""
        items = self.model.items
        if items:
            self.model.dataChanged.emit(
                self.model.index(0, 0),
                self.model.index(len(items) - 1, len(COLUMNS) - 1),
            )
        self._refresh_summary()

    def review_count(self) -> int:
        return self.model.review_count()

    def classification(self) -> dict[str, NetClassification]:
        return {item.net: item for item in self.model.items}
