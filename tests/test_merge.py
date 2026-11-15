"""历史快照配置套用到新扫描结果的测试。"""

from __future__ import annotations

import pytest

from clearance_check.altium.demo import build_demo_board
from clearance_check.analysis import classify_board, merge_classifications, merge_mounts
from clearance_check.model import MountPoint, NetClass, NetClassification, Pad


@pytest.fixture()
def demo():
    board, pins = build_demo_board()
    return board, pins, classify_board(board, pins)


def test_same_name_keeps_previous_config(demo):
    board, pins, previous = demo
    previous["GATE"].net_class = NetClass.SECONDARY
    previous["GATE"].voltage = 99.0
    previous["GATE"].voltage_min = 99.0
    previous["GATE"].voltage_max = 99.0
    previous["GATE"].ref_ground = "SGND"
    previous["GATE"].sheet_side = "control"
    previous["GATE"].user_edited = True

    fresh = classify_board(board, pins)
    stats = merge_classifications(fresh, previous)

    assert fresh["GATE"].net_class is NetClass.SECONDARY
    assert fresh["GATE"].voltage == pytest.approx(99.0)
    assert fresh["GATE"].ref_ground == "SGND"
    assert fresh["GATE"].sheet_side == "control"
    assert fresh["GATE"].user_edited is True
    assert stats.kept == len(previous)
    assert stats.added == 0
    assert stats.removed == 0
    assert stats.kept_manual >= 1


def test_new_net_keeps_auto_classification(demo):
    board, pins, previous = demo
    board.ensure_net("NEW_NET")
    board.add_pad(
        Pad(name="1", net="NEW_NET", component="R1", x=40.0, y=40.0, width=1.0, height=1.0)
    )
    fresh = classify_board(board, pins)

    stats = merge_classifications(fresh, previous)

    assert "NEW_NET" in fresh
    assert stats.added >= 1
    assert fresh["NEW_NET"].net_class is NetClass.UNKNOWN or fresh["NEW_NET"].needs_review


def test_missing_net_is_counted(demo):
    _board, _pins, fresh = demo
    previous = {"GONE": NetClassification(net="GONE", net_class=NetClass.SECONDARY)}
    previous.update({net: item for net, item in fresh.items()})
    stats = merge_classifications(fresh, previous)
    assert stats.removed == 1


def test_mounts_keep_potential():
    current = [MountPoint(name="MH1"), MountPoint(name="MH2")]
    previous = [
        MountPoint(name="MH1", net_class=NetClass.SECONDARY, voltage=12.0, enabled=True),
        MountPoint(name="MH9", net_class=NetClass.PRIMARY_CONST, voltage=400.0),
    ]
    kept = merge_mounts(current, previous)
    assert kept == 1
    assert current[0].net_class is NetClass.SECONDARY
    assert current[0].voltage == pytest.approx(12.0)
    # 未匹配到的安装焊盘保持默认（副边、无电压）
    assert current[1].net_class is NetClass.SECONDARY
    assert current[1].voltage is None
