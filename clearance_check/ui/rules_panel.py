"""间距规则配置面板：类别矩阵 + 隔离带 + 电压差增量。"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..analysis.rules import ClearanceRules, default_matrix
from ..model import NET_CLASS_SHORT_LABELS, ORDERED_NET_CLASSES
from .theme import CLASS_COLORS, MUTED


def _spin(value: float, minimum: float = 0.0, maximum: float = 30.0) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setDecimals(2)
    box.setSingleStep(0.05)
    box.setRange(minimum, maximum)
    box.setValue(value)
    box.setSuffix(" mm")
    box.setAlignment(Qt.AlignCenter)
    box.setFixedWidth(104)
    return box


class RulesPanel(QWidget):
    rulesChanged = Signal()
    runRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._spins: dict[tuple, QDoubleSpinBox] = {}
        self._loading = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(14)

        # ---- 类别矩阵 ---------------------------------------------
        matrix_group = QGroupBox("电压类别间距矩阵（焊盘铜箔外沿到外沿）")
        grid = QGridLayout(matrix_group)
        grid.setSpacing(8)

        for column, net_class in enumerate(ORDERED_NET_CLASSES, start=1):
            label = QLabel(NET_CLASS_SHORT_LABELS[net_class])
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(
                f"color: {CLASS_COLORS[net_class.value]}; font-weight: 600;"
            )
            grid.addWidget(label, 0, column)

        for row, row_class in enumerate(ORDERED_NET_CLASSES, start=1):
            label = QLabel(NET_CLASS_SHORT_LABELS[row_class])
            label.setStyleSheet(f"color: {CLASS_COLORS[row_class.value]}; font-weight: 600;")
            grid.addWidget(label, row, 0)
            for column, column_class in enumerate(ORDERED_NET_CLASSES, start=1):
                box = _spin(1.0)
                box.valueChanged.connect(
                    lambda value, a=row_class, b=column_class: self._on_cell_changed(a, b, value)
                )
                grid.addWidget(box, row, column)
                self._spins[(row_class, column_class)] = box

        note = QLabel(
            "原边↔副边取矩阵值与「隔离带最小间距」中的较大者；电压差规则按两侧峰值电压差另行叠加。"
        )
        note.setWordWrap(True)
        note.setObjectName("Hint")
        grid.addWidget(note, len(ORDERED_NET_CLASSES) + 1, 0, 1, len(ORDERED_NET_CLASSES) + 1)
        for column in range(1, len(ORDERED_NET_CLASSES) + 1):
            grid.setColumnMinimumWidth(column, 108)
        grid.setColumnStretch(len(ORDERED_NET_CLASSES) + 2, 1)
        grid.setRowStretch(len(ORDERED_NET_CLASSES) + 2, 1)
        outer.addWidget(matrix_group, 3)

        # ---- 全局规则 ---------------------------------------------
        right = QVBoxLayout()
        right.setSpacing(12)

        global_group = QGroupBox("隔离与增量规则")
        form = QGridLayout(global_group)
        form.setSpacing(8)

        form.addWidget(QLabel("隔离带爬电距离（沿面，原边↔副边）"), 0, 0)
        self.isolation = _spin(8.0, 0.0, 30.0)
        form.addWidget(self.isolation, 0, 1)

        form.addWidget(QLabel("隔离带电气间隙（跨避距槽）"), 1, 0)
        self.isolation_clearance = _spin(6.0, 0.0, 30.0)
        self.isolation_clearance.setToolTip(
            "原副边之间被板级挖空（避距槽）隔开、且绕槽沿面长度达标时，按这个直线距离判"
        )
        form.addWidget(self.isolation_clearance, 1, 1)

        form.addWidget(QLabel("焊盘距板框最小间距"), 2, 0)
        self.board_edge_gap = _spin(0.4, 0.0, 5.0)
        self.board_edge_gap.setToolTip("器件焊盘（铜箔外沿）到板框外形的最小距离")
        form.addWidget(self.board_edge_gap, 2, 1)

        form.addWidget(QLabel("器件本体最小间距（3D 本体）"), 3, 0)
        self.body_gap = _spin(0.3, 0.0, 5.0)
        self.body_gap.setToolTip("按机械层 / 3D 外形投影计算，干涉或过近都会报错")
        form.addWidget(self.body_gap, 3, 1)

        form.addWidget(QLabel("器件间最小间距（与网络无关）"), 4, 0)
        self.component_gap = _spin(0.3, 0.0, 5.0)
        form.addWidget(self.component_gap, 4, 1)

        self.step_enabled = QCheckBox("按电压差增加间距")
        form.addWidget(self.step_enabled, 5, 0, 1, 2)

        form.addWidget(QLabel("每"), 6, 0)
        step_row = QHBoxLayout()
        self.step_v = QDoubleSpinBox()
        self.step_v.setRange(1, 5000)
        self.step_v.setValue(100)
        self.step_v.setSuffix(" V 增加")
        self.step_mm = QDoubleSpinBox()
        self.step_mm.setRange(0.0, 10.0)
        self.step_mm.setDecimals(2)
        self.step_mm.setSingleStep(0.1)
        self.step_mm.setValue(0.5)
        self.step_mm.setSuffix(" mm")
        step_row.addWidget(self.step_v)
        step_row.addWidget(self.step_mm)
        step_row.addStretch(1)
        form.addLayout(step_row, 6, 1)

        self.cross_class_only = QCheckBox("只检查不同电压类别之间（忽略同类网络）")
        self.check_same_component = QCheckBox("检查同一器件内部焊盘（保留隔离器件内部爬电）")
        self.include_tracks = QCheckBox("走线也参与检查（需要读取走线，较慢）")
        self.component_gap_enabled = QCheckBox("启用器件间最小间距检查")
        for index, box in enumerate(
            (
                self.component_gap_enabled,
                self.cross_class_only,
                self.check_same_component,
                self.include_tracks,
            ),
            start=7,
        ):
            form.addWidget(box, index, 0, 1, 2)
        form.setColumnStretch(1, 1)
        right.addWidget(global_group)

        action_group = QGroupBox("规则文件")
        actions = QHBoxLayout(action_group)
        self.btn_default = QPushButton("恢复默认")
        self.btn_save = QPushButton("导出规则…")
        self.btn_load = QPushButton("导入规则…")
        for button in (self.btn_default, self.btn_save, self.btn_load):
            actions.addWidget(button)
        actions.addStretch(1)
        right.addWidget(action_group)

        self.explain = QLabel(
            "示例：隔离带 8.00 mm + 电压差规则 375 V→0 V（每 100 V +0.5 mm）时，"
            "原边 375 V 与副边 0 V 之间要求 8.00 mm；若实测 6.40 mm，则缺口 1.60 mm。"
        )
        self.explain.setWordWrap(True)
        self.explain.setObjectName("Hint")
        self.explain.setStyleSheet(f"color: {MUTED};")
        right.addWidget(self.explain)

        run_row = QHBoxLayout()
        self.run_hint = QLabel("规则确认后，点这里按当前分类重新计算避距")
        self.run_hint.setObjectName("Hint")
        self.run_hint.setStyleSheet(f"color: {MUTED};")
        self.btn_run = QPushButton("重新校验")
        self.btn_run.setObjectName("Primary")
        self.btn_run.setMinimumWidth(120)
        run_row.addWidget(self.run_hint)
        run_row.addStretch(1)
        run_row.addWidget(self.btn_run)
        right.addLayout(run_row)
        right.addStretch(1)
        outer.addLayout(right, 2)

        # ---- 信号 -------------------------------------------------
        self.btn_default.clicked.connect(self._restore_defaults)
        self.btn_save.clicked.connect(self._save_rules)
        self.btn_load.clicked.connect(self._load_rules)
        self.btn_run.clicked.connect(self.runRequested.emit)
        for widget in (
            self.isolation,
            self.isolation_clearance,
            self.board_edge_gap,
            self.body_gap,
            self.component_gap,
            self.step_v,
            self.step_mm,
        ):
            widget.valueChanged.connect(self._emit_changed)
        for widget in (self.step_enabled, self.component_gap_enabled, self.cross_class_only,
                       self.check_same_component, self.include_tracks):
            widget.toggled.connect(self._emit_changed)

        self.set_rules(ClearanceRules())

    # ---- 数据 -----------------------------------------------------
    def set_rules(self, rules: ClearanceRules) -> None:
        self._loading = True
        for (a, b), box in self._spins.items():
            box.setValue(rules.base_gap(a, b))
        self.isolation.setValue(rules.isolation_gap_mm)
        self.isolation_clearance.setValue(rules.isolation_clearance_mm)
        self.board_edge_gap.setValue(rules.board_edge_gap_mm)
        self.body_gap.setValue(rules.body_gap_mm)
        self.component_gap.setValue(rules.component_gap_mm)
        self.component_gap_enabled.setChecked(rules.component_gap_enabled)
        self.step_enabled.setChecked(rules.voltage_step_enabled)
        self.step_v.setValue(rules.voltage_step_v)
        self.step_mm.setValue(rules.voltage_step_mm)
        self.cross_class_only.setChecked(rules.cross_class_only)
        self.check_same_component.setChecked(rules.check_same_component)
        self.include_tracks.setChecked(rules.include_tracks)
        self._loading = False
        self._emit_changed()

    def rules(self) -> ClearanceRules:
        matrix = default_matrix()
        for key, box in self._spins.items():
            matrix[key] = round(box.value(), 3)
        return ClearanceRules(
            matrix=matrix,
            isolation_gap_mm=round(self.isolation.value(), 3),
            isolation_clearance_mm=round(self.isolation_clearance.value(), 3),
            board_edge_gap_mm=round(self.board_edge_gap.value(), 3),
            body_gap_mm=round(self.body_gap.value(), 3),
            component_gap_mm=round(self.component_gap.value(), 3),
            component_gap_enabled=self.component_gap_enabled.isChecked(),
            voltage_step_enabled=self.step_enabled.isChecked(),
            voltage_step_v=round(self.step_v.value(), 2),
            voltage_step_mm=round(self.step_mm.value(), 2),
            cross_class_only=self.cross_class_only.isChecked(),
            check_same_component=self.check_same_component.isChecked(),
            include_tracks=self.include_tracks.isChecked(),
        )

    # ---- 槽 -------------------------------------------------------
    def _on_cell_changed(self, a, b, value: float) -> None:
        if self._loading:
            return
        mirror = self._spins.get((b, a))
        if mirror is not None and abs(mirror.value() - value) > 1e-9:
            self._loading = True
            mirror.setValue(value)
            self._loading = False
        self._emit_changed()

    def _emit_changed(self) -> None:
        if not self._loading:
            self.rulesChanged.emit()

    def _restore_defaults(self) -> None:
        self.set_rules(ClearanceRules())

    def _save_rules(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出间距规则", "clearance_rules.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            self.rules().save(path)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "已导出", f"规则已保存到\n{path}")

    def _load_rules(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入间距规则", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            rules = ClearanceRules.load(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self.set_rules(rules)
