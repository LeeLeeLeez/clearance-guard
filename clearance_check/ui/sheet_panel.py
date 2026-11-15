"""网络电压分类的图纸视图（可直接编辑电压）。

结构：
    [common · 跨页网络]  [VPDB_main] [Pri_Driver] ...
        └─ 功率侧 / 控制侧 两个子 Tab

每个 Tab 内是该图纸包含的全部网络及其电压信息；出现在 2 张及以上图纸的网络
不重复显示，统一收敛到 common，再按功率侧 / 控制侧细分。

表格内可直接修改：电压类别用下拉框，电压等级 / 上下限双击编辑。
编辑的是与「全表编辑与确认」同一批 NetClassification 对象，两处始终一致；
参考地电压会随被引用网络的修改自动刷新。
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, QStringListModel, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QLineEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..analysis.classify import apply_follow_target
from ..analysis.sheets import net_side, split_common_nets
from ..model import (
    NET_CLASS_LABELS,
    NetClass,
    NetClassification,
    ORDERED_NET_CLASSES,
    SheetNetIndex,
    default_primary_ground,
    default_secondary_ground,
    named_net_candidates,
    editable_voltage_columns,
    normalize_voltage_fields,
    ref_ground_voltage_text,
    sync_local_voltage,
    sync_peak_voltage,
)
from .theme import ACCENT, CLASS_COLORS, DANGER, MUTED, OK, TEXT, WARN

COLUMNS = [
    "网络名",
    "电压类别",
    "电压等级 (V)",
    "下限 (V)",
    "上限 (V)",
    "参考地网络",
    "参考地电压",
    "置信度",
    "出现图纸",
    "判定依据",
    "跟随网络",
]
FOLLOW_COLUMN = len(COLUMNS) - 1

#: 可编辑的电压列 -> NetClassification 字段
_VOLTAGE_FIELDS = {2: "voltage", 3: "voltage_min", 4: "voltage_max"}
_ALIGN_CENTER = {2, 3, 5, 6, 7}


class _FollowEdit(QLineEdit):
    """「跟随网络」输入框：输入即按子串筛选候选，回车生效。"""

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self._provider = provider
        self.setClearButtonEnabled(True)
        self.setPlaceholderText("输入筛选…")
        self.setToolTip("输入网络名筛选，回车生效：电压、参考地会与该网络完全一致")
        self._install_completer()

    def _install_completer(self) -> None:
        names = list(self._provider())
        model = QStringListModel(names, self)
        completer = QCompleter(model, self)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setMaxVisibleItems(10)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        self.setCompleter(completer)


class SheetNetPanel(QWidget):
    """按原理图图纸分页展示并编辑网络电压。"""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._board = None
        self._classifications: dict[str, NetClassification] = {}
        self._index: SheetNetIndex | None = None
        self._pins: list[dict] = []
        self._tables: list[QTableWidget] = []
        self._updating = False
        self._pending_select: tuple[str, int] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(10)
        self.title = QLabel("按原理图分页查看网络电压")
        self.title.setObjectName("SectionTitle")
        header.addWidget(self.title)
        self.summary = QLabel("尚未扫描")
        self.summary.setObjectName("Hint")
        header.addWidget(self.summary)
        header.addStretch(1)
        self.legend = QLabel("类别下拉选择 · 电压等级 / 上下限双击编辑 · 改完点「重新校验」")
        self.legend.setObjectName("Hint")
        header.addWidget(self.legend)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        layout.addWidget(self.tabs, 1)

        self.placeholder = QLabel("扫描工程后，这里会按原理图图纸列出网络及其电压。")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("Hint")
        layout.addWidget(self.placeholder)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def set_data(
        self,
        board,
        classifications: dict[str, NetClassification],
        index: SheetNetIndex | None,
        pins: list[dict] | None = None,
    ) -> None:
        self._board = board
        self._classifications = classifications
        self._index = index
        self._pins = list(pins or [])
        self._rebuild()

    def _rebuild(self) -> None:
        state = self._capture_view_state()
        self.tabs.clear()
        self._tables = []
        index = self._index
        if index is None or not index.sheets:
            self.summary.setText("尚未扫描")
            self.placeholder.setVisible(True)
            self.tabs.setVisible(False)
            return

        self.placeholder.setVisible(False)
        self.tabs.setVisible(True)
        shared = index.shared_nets()
        self.summary.setText(
            f"{len(index.sheets)} 张图纸 · {index.net_count} 个网络 · {len(shared)} 个跨页"
        )

        self._inner_tabs = None
        common_tab = self._build_common_tab(shared)
        self.tabs.addTab(common_tab, "common · 跨页网络")

        for sheet, nets in index.sheets.items():
            local = [net for net in nets if not index.is_shared(net)]
            crossing = [net for net in nets if index.is_shared(net)]
            wrapper = QWidget()
            box = QVBoxLayout(wrapper)
            box.setContentsMargins(10, 10, 10, 10)
            box.setSpacing(8)
            caption = QLabel(
                f"<b>{sheet}</b> · 本页 {len(nets)} 个网络，其中 {len(crossing)} 个跨页网络已汇总到 common，"
                f"下面列出本页专属的 {len(local)} 个"
            )
            caption.setObjectName("Hint")
            caption.setWordWrap(True)

            title_row = QHBoxLayout()
            title_row.addWidget(caption, 1)
            btn_secondary = QPushButton("整页设为副边")
            btn_secondary.setObjectName("Ghost")
            btn_secondary.setAutoDefault(False)
            btn_secondary.setToolTip(f"把 {sheet} 上的 {len(nets)} 个网络全部设为副边（参考地默认 SGND）")
            btn_secondary.clicked.connect(
                lambda _checked=False, name=sheet: self._bulk_set_secondary(name)
            )
            title_row.addWidget(btn_secondary)
            box.addLayout(title_row)
            if crossing:
                reference = QLabel(
                    "跨页网络（见 common）：" + "、".join(sorted(crossing)[:10])
                    + ("…" if len(crossing) > 10 else "")
                )
                reference.setObjectName("Hint")
                reference.setWordWrap(True)
                reference.setStyleSheet(f"color: {MUTED};")
                box.addWidget(reference)
            box.addWidget(self._make_table(local, in_common=False, key=sheet), 1)
            self.tabs.addTab(wrapper, self._tab_title(sheet, len(local), len(nets)))
            self.tabs.setTabToolTip(
                self.tabs.count() - 1,
                f"{sheet}：本页共 {len(nets)} 个网络，其中 {len(crossing)} 个跨页网络在 common 中显示",
            )

        if not shared:
            self.tabs.setTabToolTip(0, "工程中所有网络都只出现在一张图纸内")
        self._restore_view_state(state)

    # ------------------------------------------------------------------
    # 视图状态（页签 / 侧别子页 / 滚动位置）
    # ------------------------------------------------------------------
    def _capture_view_state(self) -> dict:
        state: dict = {"outer": self.tabs.currentIndex(), "inner": 0, "scroll": {}}
        inner = getattr(self, "_inner_tabs", None)
        if inner is not None and self.tabs.currentIndex() == 0:
            state["inner"] = inner.currentIndex()
        for table in self._tables:
            key = table.property("bucket")
            if key:
                state["scroll"][key] = table.verticalScrollBar().value()
        state["select"] = self._pending_select
        self._pending_select = None
        return state

    def _restore_view_state(self, state: dict) -> None:
        if not state:
            return
        outer = state.get("outer", 0)
        if 0 <= outer < self.tabs.count():
            self.tabs.setCurrentIndex(outer)
        inner = getattr(self, "_inner_tabs", None)
        if inner is not None and outer == 0:
            index = state.get("inner", 0)
            if 0 <= index < inner.count():
                inner.setCurrentIndex(index)

        positions = state.get("scroll") or {}
        selected = state.get("select")

        def apply() -> None:
            for table in self._tables:
                key = table.property("bucket")
                if selected and key == selected[0]:
                    row = selected[1]
                    if 0 <= row < table.rowCount():
                        table.selectRow(row)
                if key in positions:
                    table.verticalScrollBar().setValue(positions[key])

        if positions or selected:
            QTimer.singleShot(0, apply)

    def refresh_all(self) -> None:
        """按当前分类结果整体重建（保留当前页签）。"""
        self._rebuild()

    def _tab_title(self, sheet: str, local: int, total: int) -> str:
        text = f"{sheet}  ({local}/{total})" if local != total else f"{sheet}  ({total})"
        return text if len(text) <= 24 else text[:23] + "…"

    def _build_common_tab(self, shared: list[str]) -> QWidget:
        wrapper = QWidget()
        box = QVBoxLayout(wrapper)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(8)

        buckets = (
            split_common_nets(self._index, self._board, self._classifications, self._pins)
            if self._index and self._board
            else {"power": [], "control": []}
        )

        caption = QLabel(
            f"同一网络出现在多张图纸时，只在 common 中显示一次。共 {len(shared)} 个跨页网络，"
            f"按用途分为功率侧 {len(buckets['power'])} 个、控制侧 {len(buckets['control'])} 个。"
        )
        caption.setObjectName("Hint")
        caption.setWordWrap(True)
        box.addWidget(caption)

        inner = QTabWidget()
        self._inner_tabs = inner
        for key, label, color in (
            ("power", "功率侧", DANGER),
            ("control", "控制侧", ACCENT),
        ):
            page = QWidget()
            page_box = QVBoxLayout(page)
            page_box.setContentsMargins(8, 8, 8, 8)
            page_box.setSpacing(6)
            table = self._make_table(buckets[key], in_common=True, key=key)
            table.setSelectionMode(QAbstractItemView.ExtendedSelection)

            target = "control" if key == "power" else "power"
            target_label = "控制侧" if target == "control" else "功率侧"

            side_row = QHBoxLayout()
            tip = QLabel(self._side_hint(key) + "　可多选后移到另一侧。")
            tip.setObjectName("Hint")
            tip.setStyleSheet(f"color: {MUTED};")
            side_row.addWidget(tip, 1)
            btn_move = QPushButton(f"移到{target_label}")
            btn_move.setObjectName("Ghost")
            btn_move.setAutoDefault(False)
            btn_move.setToolTip(f"把选中的网络移到{target_label}（人工指定，优先于自动判定）")
            btn_move.clicked.connect(
                lambda _checked=False, widget=table, side=target, label=target_label:
                self._move_selected(widget, side, label)
            )
            side_row.addWidget(btn_move)
            page_box.addLayout(side_row)
            page_box.addWidget(table, 1)
            inner.addTab(page, f"{label} ({len(buckets[key])})")
            inner.tabBar().setTabTextColor(inner.count() - 1, QColor(color))
        box.addWidget(inner, 1)
        return wrapper

    # ------------------------------------------------------------------
    # 批量操作
    # ------------------------------------------------------------------
    def _bulk_set_secondary(self, sheet: str) -> None:
        """把某张图纸上的全部网络设为副边（参考地默认 SGND）。"""
        index = self._index
        if index is None:
            return
        nets = index.sheets.get(sheet) or []
        ground = default_secondary_ground(self._classifications.keys())
        changed = 0
        for net in nets:
            item = self._classifications.get(net)
            if item is None or (ground and item.net == ground):
                continue
            if item.net_class is not NetClass.SECONDARY:
                changed += 1
            item.net_class = NetClass.SECONDARY
            normalize_voltage_fields(item)
            if not item.ref_ground and ground:
                item.ref_ground = ground
                item.ref_ground_reason = "副边网络默认参考 SGND"
            item.user_edited = True
            item.needs_review = item.voltage is None
        # 即使全部本来就是副边，也发一次信号让界面刷新并给出反馈
        self.changed.emit()

    def _move_selected(self, table: QTableWidget, side: str, label: str) -> None:
        """把选中的跨页网络人工指定到功率侧 / 控制侧。"""
        rows = sorted({index.row() for index in table.selectionModel().selectedRows()})
        nets = table.property("row_nets") or []
        targets = [nets[row] for row in rows if 0 <= row < len(nets)]
        if not targets:
            return
        for net in targets:
            item = self._classifications.get(net)
            if item is None:
                continue
            item.sheet_side = side
            item.user_edited = True
        # 重建后把光标停在同一位置，方便连续转移
        key = table.property("bucket")
        remaining = len(nets) - len(targets)
        if key and remaining > 0:
            self._pending_select = (key, min(rows[0], remaining - 1))
        self.changed.emit()

    @staticmethod
    def _side_hint(side: str) -> str:
        if side == "power":
            return "母线、接地、开关节点等功率通路；判定依据列给出具体原因。"
        return "栅极驱动、反馈、保护、通信等信号网络；判定依据列给出具体原因。"

    # ------------------------------------------------------------------
    # 表格构建
    # ------------------------------------------------------------------
    def _make_table(self, nets: list[str], in_common: bool, key: str = "") -> QTableWidget:
        table = QTableWidget(0, len(COLUMNS))
        if key:
            table.setProperty("bucket", key)
        table.setHorizontalHeaderLabels(COLUMNS)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.SelectedClicked
        )
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(30)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        table.setColumnWidth(0, 150)
        table.setColumnWidth(1, 196)
        table.setColumnWidth(2, 90)
        table.setColumnWidth(3, 78)
        table.setColumnWidth(4, 78)
        table.setColumnWidth(5, 112)
        table.setColumnWidth(6, 152)
        table.setColumnWidth(7, 58)
        table.setColumnWidth(8, 96)
        table.setColumnWidth(9, 180)
        table.setColumnWidth(FOLLOW_COLUMN, 136)

        rows = self._sorted_nets(nets)
        if not rows:
            table.setRowCount(1)
            table.setSpan(0, 0, 1, len(COLUMNS))
            empty = QTableWidgetItem("本页没有专属网络：所有网络都是跨页网络，请在上方 common Tab 中查看。")
            empty.setTextAlignment(Qt.AlignCenter)
            empty.setForeground(QBrush(QColor(MUTED)))
            table.setItem(0, 0, empty)
            return table

        table.setRowCount(len(rows))
        table.setProperty("row_nets", rows)
        table.setProperty("in_common", in_common)

        self._updating = True
        for row, net in enumerate(rows):
            for column in range(len(COLUMNS)):
                cell = QTableWidgetItem("")
                cell.setData(Qt.UserRole, net)
                if column in _VOLTAGE_FIELDS:
                    cell.setFlags(cell.flags() | Qt.ItemIsEditable)
                table.setItem(row, column, cell)

            combo = QComboBox()
            combo.setObjectName("CellCombo")
            for net_class in ORDERED_NET_CLASSES:
                combo.addItem(NET_CLASS_LABELS[net_class], net_class.value)
            combo.setToolTip("选择该网络的电压类别")
            combo.currentIndexChanged.connect(
                lambda _index, n=net, box=combo: self._on_class_changed(n, box)
            )
            table.setCellWidget(row, 1, combo)

            follow = _FollowEdit(self._follow_candidates)
            follow.setObjectName("CellEdit")
            follow.returnPressed.connect(
                lambda n=net, box=follow: self._on_follow_changed(n, box)
            )
            follow.editingFinished.connect(
                lambda n=net, box=follow: self._on_follow_changed(n, box)
            )
            table.setCellWidget(row, FOLLOW_COLUMN, follow)
            self._write_row(table, row, net)
        self._updating = False

        table.itemChanged.connect(self._on_item_changed)
        self._tables.append(table)
        return table

    def _write_row(self, table: QTableWidget, row: int, net: str) -> None:
        """按当前分类结果刷新一行的显示（不改变可编辑状态）。"""
        item = self._classifications.get(net)
        net_class = item.net_class if item else NetClass.UNKNOWN
        voltage = "—" if item is None or item.voltage is None else f"{item.voltage:.2f}"
        low = "—" if item is None or item.voltage_min is None else f"{item.voltage_min:.2f}"
        high = "—" if item is None or item.voltage_max is None else f"{item.voltage_max:.2f}"
        jump = item.range_text if item else "—"
        confidence = (
            "—" if item is None
            else ("✓" if item.user_edited else f"{item.confidence:.0%}")
        )
        ref = item.ref_ground if item else ""
        ref_voltage = ref_ground_voltage_text(ref, self._classifications)
        index = self._index
        sheets = index.sheets_of(net) if index else set()
        sheets_text = "、".join(sorted(sheets)) if len(sheets) > 1 else "本页"
        reason = "；".join(item.reasons) if item and item.reasons else "—"
        in_common = bool(table.property("in_common"))
        if in_common and self._board is not None:
            side, side_reason = net_side(net, self._board, item, self._pins)
            override = (item.sheet_side if item is not None else "").strip().lower()
            if override in ("power", "control"):
                manual = "功率侧" if override == "power" else "控制侧"
                auto = "功率侧" if side == "power" else "控制侧"
                reason = f"{reason}；人工指定为{manual}（自动判定：{auto}）"
            else:
                reason = f"{reason}；{'功率侧' if side == 'power' else '控制侧'}：{side_reason}"

        # 列顺序与 COLUMNS 对齐：类别列（1）由下拉框承担，不写文本
        mapping = {
            0: net,
            2: voltage,
            3: low,
            4: high,
            5: ref or "—",
            6: ref_voltage,
            7: confidence,
            8: sheets_text,
            9: reason,
        }

        was_updating = self._updating
        self._updating = True
        editable = editable_voltage_columns(net_class)
        for column in (2, 3, 4):
            cell = table.item(row, column)
            if cell is None:
                continue
            if (column - 2) in editable:
                cell.setFlags(cell.flags() | Qt.ItemIsEditable)
            else:
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
        for column, text in mapping.items():
            cell = table.item(row, column)
            if cell is None:
                continue
            if cell.text() != text:
                cell.setText(text)

        follow = table.cellWidget(row, FOLLOW_COLUMN)
        if isinstance(follow, _FollowEdit):
            target = item.follow_net if item is not None else ""
            if follow.text() != target:
                blocked = follow.blockSignals(True)
                follow.setText(target)
                follow.blockSignals(blocked)
            cell.setToolTip(text)
            if column in _ALIGN_CENTER:
                cell.setTextAlignment(Qt.AlignCenter)
            if column == 0:
                cell.setForeground(QBrush(QColor(TEXT)))
            if column in (5, 6) and ref:
                cell.setForeground(QBrush(QColor(ACCENT)))
            if column == 7 and item is not None:
                if item.user_edited:
                    cell.setForeground(QBrush(QColor(OK)))
                elif item.needs_review:
                    cell.setForeground(QBrush(QColor(WARN)))
            if column == 9:
                cell.setForeground(QBrush(QColor(MUTED)))
            if item is not None and item.needs_review:
                cell.setBackground(QBrush(QColor(240, 169, 59, 18)))
            else:
                cell.setBackground(QBrush(Qt.NoBrush))

        combo = table.cellWidget(row, 1)
        if isinstance(combo, QComboBox):
            position = combo.findData(net_class.value)
            if position >= 0 and combo.currentIndex() != position:
                blocked = combo.blockSignals(True)
                combo.setCurrentIndex(position)
                combo.blockSignals(blocked)
            combo.setStyleSheet(f"color: {CLASS_COLORS[net_class.value]}; font-weight: 600;")
        self._updating = was_updating

    # ------------------------------------------------------------------
    # 编辑
    # ------------------------------------------------------------------
    def _on_item_changed(self, cell: QTableWidgetItem) -> None:
        if self._updating:
            return
        column = cell.column()
        if column not in _VOLTAGE_FIELDS:
            return
        net = cell.data(Qt.UserRole)
        if not net:
            return
        if not self._apply_cell_edit(str(net), column, cell.text()):
            # 输入非法：还原显示
            item = self._classifications.get(str(net))
            value = getattr(item, _VOLTAGE_FIELDS[column], None) if item else None
            was_updating = self._updating
            self._updating = True
            cell.setText("—" if value is None else f"{value:.2f}")
            self._updating = was_updating

    def _apply_cell_edit(self, net: str, column: int, text: str) -> bool:
        """把单元格文本写回分类结果；返回是否成功。"""
        item = self._classifications.get(net)
        if item is None or column not in _VOLTAGE_FIELDS:
            return False
        if (column - 2) not in editable_voltage_columns(item.net_class):
            return False
        raw = (text or "").strip()
        if raw in ("", "—", "-"):
            value: float | None = None
        else:
            try:
                value = float(raw)
            except ValueError:
                return False

        field = _VOLTAGE_FIELDS[column]
        setattr(item, field, value)
        if column == 2:
            if item.voltage_min is None and value is not None:
                item.voltage_min = value
            if item.voltage_max is None and value is not None:
                item.voltage_max = value
            if item.net_class is not NetClass.PRIMARY_SWITCHING:
                # 恒定电平：上下限跟随电压等级
                normalize_voltage_fields(item)
        # 上下限填反了就自动纠正，避免参考地显示“范围待确认”
        if (item.voltage_min is not None and item.voltage_max is not None
                and item.voltage_min > item.voltage_max):
            item.voltage_min, item.voltage_max = item.voltage_max, item.voltage_min
        sync_peak_voltage(item)
        sync_local_voltage(item, self._classifications)
        item.user_edited = True
        item.needs_review = item.net_class is NetClass.UNKNOWN or item.voltage is None
        self._refresh_net(net)
        self.changed.emit()
        return True

    def _follow_candidates(self) -> list[str]:
        return named_net_candidates(self._classifications.keys())

    def _on_follow_changed(self, net: str, edit: QLineEdit) -> None:
        if self._updating:
            return
        item = self._classifications.get(net)
        if item is None:
            return
        try:
            text = edit.text().strip()
        except RuntimeError:  # 控件已被重建销毁
            return
        candidates = self._follow_candidates()
        if text in ("", "（不跟随）"):
            target = ""
        elif text in candidates:
            target = text
        else:
            # 只输了片段：按子串缩小范围，取前缀匹配的第一条
            lowered = text.lower()
            matches = [name for name in candidates if lowered in name.lower()]
            prefix = [name for name in matches if name.lower().startswith(lowered)]
            picked = (prefix or matches)
            if not picked:
                # 完全匹配不上：还原成当前生效的跟随对象
                blocked = edit.blockSignals(True)
                edit.setText(item.follow_net)
                edit.blockSignals(blocked)
                return
            target = picked[0]
        if target == item.follow_net:
            return
        item.follow_net = target
        if target:
            apply_follow_target(self._classifications)
            item.user_edited = True
        blocked = edit.blockSignals(True)
        edit.setText(target)
        edit.blockSignals(blocked)
        self.changed.emit()

    def _on_class_changed(self, net: str, combo: QComboBox) -> None:
        if self._updating:
            return
        self._apply_class(net, NetClass(combo.currentData()))

    def _apply_class(self, net: str, net_class: NetClass) -> None:
        item = self._classifications.get(net)
        if item is None:
            return
        item.net_class = net_class
        normalize_voltage_fields(item)
        sync_local_voltage(item, self._classifications)
        if "GND" not in item.net.upper():
            if net_class is NetClass.SECONDARY and (
                not item.ref_ground or item.ref_ground.startswith("PGND")
            ):
                ground = default_secondary_ground(self._classifications.keys())
                if ground and ground != item.net:
                    item.ref_ground = ground
                    item.ref_ground_reason = "副边网络默认参考 SGND"
            elif net_class is NetClass.PRIMARY_CONST and (
                not item.ref_ground or item.ref_ground.startswith("SGND")
            ):
                ground = default_primary_ground(self._classifications.keys())
                if ground and ground != item.net:
                    item.ref_ground = ground
                    item.ref_ground_reason = "原边恒定网络默认参考 PGND"
        item.user_edited = True
        item.needs_review = net_class is NetClass.UNKNOWN or item.voltage is None
        self._refresh_net(net)
        self.changed.emit()

    def _refresh_net(self, net: str) -> None:
        """刷新所有引用了该网络的单元格（自身 + 以它为参考地的驱动网络）。"""
        for table in self._tables:
            rows = table.property("row_nets") or []
            for row, row_net in enumerate(rows):
                if row_net == net:
                    self._write_row(table, row, row_net)
                    continue
                item = self._classifications.get(row_net)
                if item is not None and item.ref_ground == net:
                    self._write_row(table, row, row_net)

    # ------------------------------------------------------------------
    def _sorted_nets(self, nets: list[str]) -> list[str]:
        order = {net_class: position for position, net_class in enumerate(ORDERED_NET_CLASSES)}

        def key(net: str):
            item = self._classifications.get(net)
            net_class = item.net_class if item else NetClass.UNKNOWN
            return (order.get(net_class, 99), net.lower())

        return sorted(set(nets), key=key)
