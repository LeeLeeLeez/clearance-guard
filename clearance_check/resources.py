"""图标等静态资源路径。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def icon_path() -> Path:
    """优先用多尺寸 ICO（任务栏清晰），退回 PNG / SVG。"""
    for name in ("app_icon.ico", "app_icon.svg"):
        candidate = ASSETS / name
        if candidate.is_file():
            return candidate
    return ASSETS / "app_icon.svg"
