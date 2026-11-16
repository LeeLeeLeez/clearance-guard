"""Clearance Guard 启动入口。

    python app.py           # 正常启动
    python app.py --demo    # 启动并载入内置示例工程
    python app.py --smoke   # 启动后 2 秒自动退出（自检 / 批处理验证）
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from clearance_check.resources import icon_path
from clearance_check.ui.main_window import MainWindow
from clearance_check.ui.theme import apply_theme


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Clearance Guard")
    app.setOrganizationName("ClearanceGuard")
    app.setDesktopFileName("ClearanceGuard")
    app.setWindowIcon(QIcon(str(icon_path())))
    # Windows 任务栏按 AppUserModelID 分组，否则会显示成 python.exe 的图标
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "ClearanceGuard.ClearanceCheck.1"
            )
        except Exception:  # noqa: BLE001 - 非 Windows 或无权限时忽略
            pass
    apply_theme(app)

    window = MainWindow()
    window.show()

    if "--demo" in sys.argv:
        window._load_demo()

    # 命令行直接打开快照：ClearanceGuard.exe --snapshot <file.json>
    if "--outline" in sys.argv:
        index = sys.argv.index("--outline")
        if index + 1 < len(sys.argv):
            try:
                window._import_outline_html_inner(sys.argv[index + 1])
            except Exception:  # noqa: BLE001
                import traceback

                window._append_log("板框导入失败：" + traceback.format_exc())

    if "--snapshot" in sys.argv:
        index = sys.argv.index("--snapshot")
        if index + 1 < len(sys.argv):
            window._import_snapshot_path(sys.argv[index + 1])

    if "--smoke" in sys.argv:
        QTimer.singleShot(2000, app.quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
