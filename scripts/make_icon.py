"""把 assets/app_icon.svg 渲染成 PNG / ICO（标题栏、任务栏、打包都用它）。"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG = os.path.join(ROOT, "assets", "app_icon.svg")
PNG = os.path.join(ROOT, "assets", "app_icon.png")
ICO = os.path.join(ROOT, "assets", "app_icon.ico")

SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> int:
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PIL import Image

    app = QGuiApplication(sys.argv)
    renderer = QSvgRenderer(SVG)
    if not renderer.isValid():
        print("SVG 无效:", SVG)
        return 1

    frames = {}
    for size in SIZES:
        image = QImage(QSize(size, size), QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        renderer.render(painter)
        painter.end()
        path = os.path.join(ROOT, "assets", f"app_icon_{size}.png")
        image.save(path)
        frames[size] = Image.open(path).convert("RGBA")
        os.remove(path)

    frames[256].save(PNG)
    frames[256].save(ICO, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"PNG {os.path.getsize(PNG) // 1024} KB, ICO {os.path.getsize(ICO) // 1024} KB")
    print("sizes:", SIZES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
