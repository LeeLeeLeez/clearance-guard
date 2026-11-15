"""板级布局视图：按电压类别给焊盘着色，并标出避距违规。"""

from __future__ import annotations

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QSizePolicy,
)

from ..geometry import pad_polygon
from ..model import Board, NetClassification, Violation
from .theme import BORDER, WARN, class_color, CLASS_COLORS, DANGER, MUTED


class BoardView(QGraphicsView):
    """可缩放/平移的板视图。"""

    violationPicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor("#0B1220")))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(320)

        self._board: Board | None = None
        self._show_error = True
        self._show_warning = True
        self._classifications: dict[str, NetClassification] = {}
        self._violations: list[Violation] = []
        self._markers: list[dict] = []
        self._labels: list[QGraphicsSimpleTextItem] = []
        self._show_labels = True
        self._show_top = True
        self._show_bottom = True
        self._selected = -1
        self._user_zoomed = False

    # ---- 数据 -----------------------------------------------------
    def set_data(
        self,
        board: Board,
        classifications: dict[str, NetClassification],
        violations: list[Violation],
    ) -> None:
        self._board = board
        self._classifications = classifications
        self._violations = violations
        self._selected = -1
        self._rebuild()

    def set_severity_filter(self, show_error: bool, show_warning: bool) -> None:
        """独立控制 ERROR / WARNING 违规标记的显示。"""
        self._show_error = bool(show_error)
        self._show_warning = bool(show_warning)
        self._rebuild()

    def set_layer_visible(self, top: bool, bottom: bool) -> None:
        self._show_top, self._show_bottom = top, bottom
        self._rebuild()

    def set_labels_visible(self, visible: bool) -> None:
        self._show_labels = visible
        for label in self._labels:
            label.setVisible(visible)

    # ---- 绘制 -----------------------------------------------------
    def _rebuild(self) -> None:
        self._scene.clear()
        self._markers.clear()
        self._labels.clear()
        board = self._board
        if board is None:
            return

        x0, y0, x1, y1 = board.board_bbox()
        width, height = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
        outline = QRectF(x0, -y1, width, height)
        if len(board.outline) >= 3:
            # 真实板框（含弧段，已离散）：填充 + 描边，替代矩形底
            polygon = QPolygonF([QPointF(px, -py) for px, py in board.outline])
            shape = self._scene.addPolygon(
                polygon,
                QPen(QColor("#3B5A8A"), 0.35),
                QBrush(QColor("#111B2E")),
            )
            shape.setZValue(-10)
        else:
            self._scene.addRect(
                outline,
                QPen(QColor(BORDER), 0.25),
                QBrush(QColor("#111B2E")),
            )
            hint = self._scene.addSimpleText("板框外形未读取 —— 重新扫描工程后显示真实板形")
            hint.setBrush(QBrush(QColor(MUTED)))
            # 场景单位是 mm，字号要按毫米给，否则会巨大
            font = QFont("Microsoft YaHei UI")
            font.setPointSizeF(2.4)
            hint.setFont(font)
            hint.setPos(x0 + 2.0, -y1 + 2.0)

        # 板级挖空：器件封装（灰）/ 爬电隔离（绿）/ 圆形孔（深）
        cutout_colors = {
            "footprint": "#475569",
            "creepage": "#22C55E",
            "frame": "#334155",
            "": "#475569",
        }
        for cutout in getattr(board, "cutouts", []) or []:
            color = QColor(cutout_colors.get(getattr(cutout, "role", ""), "#475569"))
            if getattr(cutout, "is_circle", False):
                self._scene.addEllipse(
                    QRectF(cutout.x - cutout.width / 2, -(cutout.y + cutout.height / 2),
                           cutout.width, cutout.height),
                    QPen(color, 0.2),
                    QBrush(QColor("#0B1220")),
                )
                continue
            points = getattr(cutout, "points", None) or []
            if len(points) >= 3:
                polygon = QPolygonF([QPointF(px, -py) for px, py in points])
                self._scene.addPolygon(
                    polygon, QPen(color, 0.22), QBrush(QColor("#0B1220"))
                )
            else:
                rect = QRectF(cutout.x - cutout.width / 2, -(cutout.y + cutout.height / 2),
                              cutout.width, cutout.height)
                self._scene.addRect(rect, QPen(color, 0.22), QBrush(QColor("#0B1220")))

        # 安装焊盘：外径圆环 + 内径孔（CH* 来自挖空，MH* 来自焊盘，统一在这里画）
        for mount in getattr(board, "mounts", []) or []:
            outer = max(float(getattr(mount, "width", 0) or 0),
                        float(getattr(mount, "height", 0) or 0)) / 2.0
            if outer <= 0:
                continue
            hole = float(getattr(mount, "hole", 0) or 0) / 2.0
            cx, cy = mount.x, -mount.y
            assigned = bool(getattr(mount, "assigned", False))
            net_class = getattr(mount, "net_class", None)
            ring = (
                class_color(net_class.value)
                if assigned and net_class is not None
                else QColor("#94A3B8")
            )
            ring.setAlpha(170)
            item = self._scene.addEllipse(
                QRectF(cx - outer, cy - outer, outer * 2, outer * 2),
                QPen(ring, 0.18),
                QBrush(QColor(ring.red(), ring.green(), ring.blue(), 60)),
            )
            item.setToolTip(
                f"{mount.name}　外径 {outer * 2:.2f} mm / 内径 {hole * 2:.2f} mm"
            )
            if hole > 0:
                self._scene.addEllipse(
                    QRectF(cx - hole, cy - hole, hole * 2, hole * 2),
                    QPen(QColor("#0B1220"), 0.08),
                    QBrush(QColor("#0B1220")),
                )

        # 器件包围盒与位号
        for designator, comp in board.components.items():
            bbox = board.component_bbox(designator)
            if not bbox:
                continue
            bx0, by0, bx1, by1 = bbox
            rect = QRectF(bx0, -by1, max(bx1 - bx0, 0.4), max(by1 - by0, 0.4))
            color = QColor("#33507A") if comp.layer.upper().startswith("TOP") else QColor("#4A3A6B")
            self._scene.addRect(rect, QPen(color, 0.12, Qt.DashLine), QBrush(Qt.NoBrush))

            if self._show_labels and (len(board.components) <= 260 or self._in_violation(designator)):
                label = QGraphicsSimpleTextItem(designator)
                label.setBrush(QBrush(QColor(MUTED)))
                font = QFont("Consolas", 7)
                label.setFont(font)
                label.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations, True)
                label.setPos(rect.left(), rect.bottom())
                label.setToolTip(designator)
                self._scene.addItem(label)
                self._labels.append(label)

        # 焊盘
        for pad in board.pads:
            if not pad.net:
                continue
            layer = (pad.layer or "TOP").upper()
            if layer.startswith("BOTTOM") and not self._show_bottom:
                continue
            if layer.startswith("TOP") and not self._show_top:
                continue
            if layer.startswith("MULTI") and not (self._show_top or self._show_bottom):
                continue
            net_class = self._classifications.get(pad.net)
            value = net_class.net_class.value if net_class else "UNKNOWN"
            base = QColor(CLASS_COLORS.get(value, CLASS_COLORS["UNKNOWN"]))
            fill = QColor(base)
            fill.setAlpha(105)
            pen = QPen(QColor(base).darker(120), 0.05)
            polygon = QPolygonF([QPointF(px, -py) for px, py in pad_polygon(pad)])
            self._scene.addPolygon(polygon, pen, QBrush(fill))
            if pad.hole > 0:
                radius = pad.hole / 2.0
                self._scene.addEllipse(
                    QRectF(pad.x - radius, -pad.y - radius, radius * 2, radius * 2),
                    QPen(QColor(BORDER), 0.05), QBrush(QColor("#0B1220")),
                )

        # 违规标记
        for index, violation in enumerate(self._violations):
            severity = (violation.severity or "error").lower()
            if severity == "warning" and not self._show_warning:
                continue
            if severity != "warning" and not self._show_error:
                continue
            point = QPointF(violation.x, -violation.y)
            color = WARN if severity == "warning" else DANGER
            pen = QPen(QColor(color), 0.18 if index == self._selected else 0.12, Qt.DashLine)
            self._scene.addLine(
                QLineF(
                    violation.ax, -violation.ay,
                    violation.bx, -violation.by,
                ),
                pen,
            )
            radius = 0.6 + min(violation.deficit_mm, 3.0) * 0.25
            ellipse = self._scene.addEllipse(
                QRectF(point.x() - radius, point.y() - radius, radius * 2, radius * 2),
                QPen(QColor(color), 0.2),
                QBrush(QColor(245, 158, 11, 70) if severity == "warning" else QColor(239, 78, 91, 70)),
            )
            ellipse.setToolTip(
                f"{violation.pad_a} ↔ {violation.pad_b}\n"
                f"实测 {violation.actual_mm:.2f} mm / 要求 {violation.required_mm:.2f} mm"
            )
            ellipse.setData(0, index)
            self._markers.append({"index": index, "item": ellipse, "point": point,
                                  "violation": violation})

        self._scene.setSceneRect(QRectF(x0 - 4, -y1 - 4, width + 8, height + 8))
        self.fit_view()

    def _in_violation(self, designator: str) -> bool:
        return any(
            designator in (v.item_a, v.item_b) for v in self._violations[:400]
        )

    # ---- 交互 -----------------------------------------------------
    def fit_view(self) -> None:
        rect = self._scene.sceneRect()
        if rect.isNull():
            return
        self.resetTransform()
        self.fitInView(rect, Qt.KeepAspectRatio)
        if self.transform().m11() > 14:
            self.scale(14 / self.transform().m11(), 14 / self.transform().m11())
        self._user_zoomed = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._user_zoomed:
            self.fit_view()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._user_zoomed and self._board is not None:
            self.fit_view()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._user_zoomed = True
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        current = self.transform().m11()
        target = current * factor
        if 0.05 <= target <= 200:
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            nearest = None
            best = 1e9
            for marker in self._markers:
                distance = (marker["point"] - scene_pos).manhattanLength()
                if distance < best:
                    best, nearest = distance, marker
            if nearest is not None and best < 6.0 / max(self.transform().m11(), 1e-6) + 0.8:
                self.select_violation(nearest["index"])
                event.accept()
                return
        super().mousePressEvent(event)

    def select_violation(self, index: int) -> None:
        self._selected = index
        for marker in self._markers:
            item = marker["item"]
            if marker["index"] == index:
                item.setPen(QPen(QColor(DANGER), 0.3))
                item.setBrush(QBrush(QColor(239, 78, 91, 130)))
                self.centerOn(marker["point"])
            else:
                item.setPen(QPen(QColor(DANGER), 0.2))
                item.setBrush(QBrush(QColor(239, 78, 91, 70)))
        if 0 <= index < len(self._violations):
            self.violationPicked.emit(index)

    def center_on_component(self, designator: str) -> None:
        if not self._board:
            return
        bbox = self._board.component_bbox(designator)
        if not bbox:
            return
        bx0, by0, bx1, by1 = bbox
        rect = QRectF(bx0 - 4, -by1 - 4, (bx1 - bx0) + 8, (by1 - by0) + 8)
        self.fitInView(rect, Qt.KeepAspectRatio)
        if self.transform().m11() > 20:
            self.scale(20 / self.transform().m11(), 20 / self.transform().m11())

    def color_legend(self) -> list[tuple[str, str]]:
        return [
            ("原边·恒定电平", CLASS_COLORS["PRIMARY_CONST"]),
            ("原边·跳变电平", CLASS_COLORS["PRIMARY_SWITCHING"]),
            ("副边", CLASS_COLORS["SECONDARY"]),
            ("未定", CLASS_COLORS["UNKNOWN"]),
        ]
