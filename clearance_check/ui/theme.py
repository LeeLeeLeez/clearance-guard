"""深色主题（Data-Dense Dark Dashboard）。

设计语言参考 ui-ux-pro-max 的 dark-dashboard 配方：
    - 深色石板底（#0F172A）+ 分层表面（#131C2E / #1B2336），减少大面积纯黑；
    - 单一强调色（蓝 #3B82F6）负责“操作”，绿/黄/红只承担“安全/警告/危险”语义；
    - 数据密集：表头紧凑、行高 26px、数字用等宽字体；
    - 可访问性：正文对比度 ≥ 7:1，所有可交互控件有可见焦点环。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDialog,
    QMainWindow,
    QProxyStyle,
    QStyle,
)

__all__ = [
    "BG", "SURFACE", "PANEL", "CARD", "CARD_HOVER", "BORDER", "BORDER_STRONG",
    "TITLEBAR", "TEXT", "MUTED", "ACCENT", "ACCENT_DARK", "ACCENT_SOFT",
    "OK", "WARN", "DANGER", "CLASS_COLORS", "QSS", "class_color",
    "apply_dark_titlebar", "apply_theme",
]

# ---- 颜色令牌 -------------------------------------------------------------
BG = "#0F172A"            # 应用底色
SURFACE = "#131C2E"       # 表格 / 面板底
PANEL = SURFACE           # 兼容旧命名
CARD = "#1B2336"          # 卡片 / 分组
CARD_HOVER = "#22304A"
BORDER = "#26334A"        # 常规分隔线
BORDER_STRONG = "#475569"  # 明确边界（焦点、选中框）
TITLEBAR = SURFACE        # 原生标题栏：与顶栏起始色一致，窗口上下浑然一体
TEXT = "#F8FAFC"
MUTED = "#94A3B8"         # 次级文字（对比度 ≈ 7:1 on BG）
ACCENT = "#2563EB"        # 操作色（与白字对比度 5.2:1）
ACCENT_DARK = "#1D4ED8"
ACCENT_SOFT = "#1E3A8A"
OK = "#22C55E"            # 安全 / 通过
WARN = "#F59E0B"
DANGER = "#EF4444"

CLASS_COLORS = {
    "PRIMARY_CONST": "#F59E0B",
    "PRIMARY_SWITCHING": "#EF4444",
    "SECONDARY": "#2DD4BF",
    "UNKNOWN": "#64748B",
}


QSS = f"""
/* ---------- 基础排版 ---------- */
* {{
    font-family: "Microsoft YaHei UI", "Segoe UI", "Noto Sans SC", sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QWidget {{
    background: {BG};
}}
QMainWindow, QDialog {{
    background: {BG};
}}
QToolTip {{
    background: {CARD};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* ---------- 顶栏 ---------- */
/* 起点色与原生标题栏同色，标题栏 → 顶栏 → 主区是一条连续的同色系过渡 */
QFrame#Header {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {TITLEBAR}, stop:1 {BG});
    border-bottom: 1px solid {BORDER};
}}
QLabel#AppTitle {{
    font-size: 18px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#AppSubtitle {{
    color: {MUTED};
    font-size: 12px;
}}

/* ---------- 文字层级 ---------- */
QLabel#SectionTitle {{
    font-weight: 600;
    font-size: 14px;
    color: {TEXT};
}}
QLabel#Hint {{
    color: {MUTED};
    font-size: 12px;
}}
QFrame#Card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QLabel#CardValue {{
    font-family: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
    font-size: 22px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#CardTitle {{
    color: {MUTED};
    font-size: 12px;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 14px;
    min-height: 20px;
}}
QPushButton:hover {{
    background: {CARD_HOVER};
    border-color: {BORDER_STRONG};
}}
QPushButton:pressed {{
    background: #182238;
}}
QPushButton:focus {{
    border: 1px solid {ACCENT};
}}
QPushButton:disabled {{
    color: #64748B;
    background: #151D2E;
    border-color: #1E2942;
}}
QPushButton#Primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: {ACCENT_DARK};
    border-color: {ACCENT_DARK};
}}
QPushButton#Primary:pressed {{
    background: #1D4ED8;
}}
QPushButton#Primary:disabled {{
    background: #24344D;
    border-color: #24344D;
    color: #8FA1BD;
}}
QPushButton#Ghost {{
    background: transparent;
    border: 1px solid transparent;
}}
QPushButton#Ghost:hover {{
    background: {CARD};
    border-color: {BORDER};
}}
QPushButton#Danger {{
    background: transparent;
    border: 1px solid {DANGER};
    color: {DANGER};
}}
QPushButton#Danger:hover {{
    background: #3B1D24;
}}

/* ---------- 页签 ---------- */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 12px;
    background: {SURFACE};
    top: -1px;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 9px 16px;
    margin-right: 2px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{
    color: {TEXT};
}}
QTabBar::tab:selected {{
    color: {TEXT};
    font-weight: 600;
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:disabled {{
    color: #55647C;
}}

/* ---------- 表格 ---------- */
QTableView, QTableWidget, QTreeView, QListView {{
    background: {SURFACE};
    alternate-background-color: #16203A;
    border: 1px solid {BORDER};
    border-radius: 10px;
    gridline-color: #1F2C46;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
    outline: none;
}}
QTableView::item, QTableWidget::item, QTreeView::item {{
    padding: 3px 6px;
    border: none;
}}
QTableView::item:selected, QTableWidget::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT};
}}
QTableView:focus, QTableWidget:focus {{
    border: 1px solid {BORDER_STRONG};
}}
QHeaderView {{
    background: {SURFACE};
}}
QHeaderView::section {{
    background: #17223A;
    color: {MUTED};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 7px 8px;
    font-weight: 600;
}}
QHeaderView::section:hover {{
    color: {TEXT};
}}
QTableCornerButton::section {{
    background: #17223A;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
}}

/* ---------- 输入控件 ---------- */
/* 表格单元格里的控件：收紧内边距，避免被行高裁掉 */
QComboBox#CellCombo, QLineEdit#CellEdit, QDoubleSpinBox#CellSpin {{
    padding: 0px 6px;
    border-radius: 6px;
    margin: 2px 1px;
}}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: #0D1526;
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 4px 8px;
    selection-background-color: {ACCENT};
    selection-color: #FFFFFF;
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {BORDER_STRONG};
}}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: #64748B;
    background: #111A2B;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}}
QComboBox QAbstractItemView {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}
QPlainTextEdit, QTextEdit, QTextBrowser {{
    border-radius: 10px;
}}

/* ---------- 复选 ---------- */
QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {TEXT};
    background: transparent;
}}
QCheckBox:disabled {{
    color: #64748B;
}}
/* ---------- 容器 ---------- */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 12px;
    margin-top: 16px;
    padding: 16px 14px 12px 14px;
    background: {CARD};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: {MUTED};
    font-weight: 600;
}}
QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 7px;
    background: #0D1526;
    text-align: center;
    height: 14px;
    color: {MUTED};
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 6px;
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #334155;
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {BORDER_STRONG};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #334155;
    border-radius: 4px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {BORDER_STRONG};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---------- 其它 ---------- */
QStatusBar {{
    background: {SURFACE};
    color: {MUTED};
    border-top: 1px solid {BORDER};
}}
QStatusBar::item {{
    border: none;
}}
QSplitter::handle {{
    background: transparent;
}}
QSplitter::handle:hover {{
    background: {BORDER};
}}
QMenu {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 18px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {ACCENT_SOFT};
}}
QMessageBox {{
    background: {CARD};
}}
"""


def class_color(net_class_value: str) -> QColor:
    return QColor(CLASS_COLORS.get(net_class_value, CLASS_COLORS["UNKNOWN"]))


class _CheckStyle(QProxyStyle):
    """自绘复选框对勾。

    本机 DLP 会把 QSS 引用的 PNG 加密，图片型 indicator 会失效，
    所以这里用 QPainter 直接画，样式与主题一致。
    """

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802
        if element not in (
            QStyle.PE_IndicatorCheckBox,
            QStyle.PE_IndicatorItemViewItemCheck,
        ):
            return super().drawPrimitive(element, option, painter, widget)

        state = option.state
        checked = bool(state & QStyle.State_On)
        partial = bool(state & QStyle.State_NoChange)
        enabled = bool(state & QStyle.State_Enabled)
        hover = bool(state & QStyle.State_MouseOver)
        size = 16 if element == QStyle.PE_IndicatorCheckBox else 15
        rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)
        rect.setWidth(min(rect.width(), size))
        rect.setHeight(min(rect.height(), size))
        rect.moveCenter(QPointF(option.rect.center()) + QPointF(0, 0.5))

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if checked or partial:
            fill = QColor(ACCENT if enabled else "#334155")
            border = fill
        else:
            border = QColor("#475569" if enabled else "#2B3852")
            fill = QColor("#0D1526" if enabled else "#131C2E")
        if hover and enabled and not checked:
            border = QColor(ACCENT)

        painter.setPen(QPen(border, 1.0))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(rect, 4, 4)

        if checked:
            pen = QPen(QColor("#FFFFFF"))
            pen.setWidthF(1.9)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            w = rect.width()
            h = rect.height()
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(rect.left() + w * 0.24, rect.top() + h * 0.52),
                        QPointF(rect.left() + w * 0.44, rect.top() + h * 0.72),
                        QPointF(rect.left() + w * 0.78, rect.top() + h * 0.30),
                    ]
                )
            )
        elif partial:
            painter.setPen(QPen(QColor("#FFFFFF"), 1.8))
            painter.drawLine(
                QPointF(rect.left() + 3, rect.center().y()),
                QPointF(rect.right() - 3, rect.center().y()),
            )
        painter.restore()


def _colorref(color: str) -> int:
    """#RRGGBB → DWM 需要的 COLORREF（0x00BBGGRR）。"""
    value = color.lstrip("#")
    red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
    return red | (green << 8) | (blue << 16)


def apply_dark_titlebar(window) -> bool:
    """把 Windows 原生标题栏染成主题深色，成功返回 True。

    - Win10 1809+：DWMWA_USE_IMMERSIVE_DARK_MODE 启用系统深色标题栏；
    - Win11 22000+：再显式指定标题栏 / 文字 / 边框颜色，与 QSS 令牌一致。
    非 Windows 或调用失败时静默返回 False，界面表现不受影响。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = int(window.winId())
        if not hwnd:
            return False
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = (
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        set_attribute.restype = ctypes.c_long

        def _set(attribute: int, value: int) -> bool:
            data = ctypes.c_int(value)
            return (
                set_attribute(
                    wintypes.HWND(hwnd),
                    attribute,
                    ctypes.byref(data),
                    ctypes.sizeof(data),
                )
                == 0
            )

        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE（1903+），19 为早期编号
        dark = any(_set(attribute, 1) for attribute in (20, 19))
        # 颜色属性 Win11 才支持，旧系统返回 E_INVALIDARG，忽略即可
        _set(35, _colorref(TITLEBAR))   # DWMWA_CAPTION_COLOR
        _set(36, _colorref(TEXT))       # DWMWA_TEXT_COLOR
        _set(34, _colorref(BORDER))     # DWMWA_BORDER_COLOR
        return dark
    except Exception:  # noqa: BLE001 - DWM 失败不应影响启动
        return False


class _DarkTitleBarFilter(QObject):
    """窗口显示时刷新原生标题栏（主窗口、对话框、消息框一并覆盖）。"""

    def eventFilter(self, obj, event):  # noqa: N802
        if (
            event.type() == QEvent.Type.Show
            and isinstance(obj, (QMainWindow, QDialog))
            and obj.isWindow()
        ):
            apply_dark_titlebar(obj)
        return super().eventFilter(obj, event)


_TITLEBAR_FILTER: _DarkTitleBarFilter | None = None


def apply_theme(app) -> None:
    global _TITLEBAR_FILTER
    app.setStyle(_CheckStyle("Fusion"))
    app.setStyleSheet(QSS)
    if _TITLEBAR_FILTER is None:
        _TITLEBAR_FILTER = _DarkTitleBarFilter(app)
        app.installEventFilter(_TITLEBAR_FILTER)
