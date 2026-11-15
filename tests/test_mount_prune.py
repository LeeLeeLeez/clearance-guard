"""封装内圆孔不应出现在安装焊盘里。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from clearance_check.model import Board, BoardCutout, Component, MountPoint, Pad  # noqa: E402
from clearance_check.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_footprint_hole_not_treated_as_mount(app):
    board = Board(name="holes")
    # 器件 T1：两颗焊盘夹着中心孔
    board.add_component(Component(designator="T1", comment="transformer", x=50.0, y=50.0))
    # 四颗焊盘围出铜箔区，中心孔整体落在其中
    for name, net, px, py in (
        ("1", "A", 45.0, 47.0),
        ("2", "B", 55.0, 47.0),
        ("3", "A", 45.0, 53.0),
        ("4", "B", 55.0, 53.0),
    ):
        board.add_pad(Pad(name=name, net=net, component="T1", x=px, y=py,
                          width=2.0, height=2.0))
    board.cutouts = [
        BoardCutout(kind="circle", x=50.0, y=50.0, width=4.0, height=4.0),   # 封装内孔
        BoardCutout(kind="circle", x=20.0, y=20.0, width=3.5, height=3.5),   # 独立安装孔
    ]
    board.mounts = [
        MountPoint(name="CH1", x=50.0, y=50.0, width=4.0, height=4.0, hole=4.0),
        MountPoint(name="CH2", x=20.0, y=20.0, width=3.5, height=3.5, hole=3.5),
    ]

    window = MainWindow()
    window.board = board
    window._sanitize_board_geometry(board)

    names = [(mount.name, round(mount.x, 1)) for mount in board.mounts]
    assert ("CH1", 20.0) in names          # 独立孔保留并重新编号
    assert all(x != 50.0 for _, x in names)  # 封装内孔被剔除
    window.close()


def test_circle_near_core_stays_mount():
    """磁芯旁 10 mm 内的圆形挖空仍是安装孔；多边形挖空才吃放宽。"""
    from clearance_check.eda.session import classify_cutouts

    board = Board(name="core")
    board.add_component(Component(designator="T38", comment="aux_core", x=121.0, y=225.6))
    board.components["T38"].bbox = (116.09, 222.01, 126.47, 229.27)  # 无焊盘，仅本体框

    labelled = classify_cutouts(
        [
            {"kind": "circle", "x": 116.83, "y": 238.32, "width": 3.5, "height": 3.5},
            {"kind": "slot", "x": 117.62, "y": 234.72, "width": 10.01, "height": 3.20},
        ],
        [],
        board,
    )
    roles = {item["kind"]: item["role"] for item in labelled}
    assert roles["circle"] == "creepage"   # 安装孔不能因为靠磁芯被吞掉
    assert roles["slot"] == "footprint"    # 磁芯外的槽仍算封装挖空
