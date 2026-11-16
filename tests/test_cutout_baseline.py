"""扫描后挖空归属跟随配置基线（快照）里的手工值。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from clearance_check.model import Board, BoardCutout  # noqa: E402
from clearance_check.ui.main_window import MainWindow  # noqa: E402


class _Snapshot:
    def __init__(self, board):
        self.board = board


def _cutout(x, y, w=10.0, h=3.0, role=""):
    return BoardCutout(kind="slot", x=x, y=y, width=w, height=h, role=role)


def test_scan_follows_baseline_roles():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    baseline = Board(name="base")
    baseline.cutouts = [_cutout(100.0, 200.0, role="creepage")]
    window._baseline_snapshot = _Snapshot(baseline)

    scanned = Board(name="scan")
    scanned.cutouts = [
        _cutout(100.3, 200.4, role=""),
        _cutout(300.0, 100.0, role=""),
    ]

    applied = window._apply_baseline_cutout_roles(scanned)
    assert applied == 1
    assert scanned.cutouts[0].role == "creepage"
    assert scanned.cutouts[1].role == ""
    window.close()


def test_no_baseline_keeps_auto_roles():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._baseline_snapshot = None

    scanned = Board(name="scan")
    scanned.cutouts = [_cutout(10.0, 10.0, role="footprint")]

    assert window._apply_baseline_cutout_roles(scanned) == 0
    assert scanned.cutouts[0].role == "footprint"
    window.close()
