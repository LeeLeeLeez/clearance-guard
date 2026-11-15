"""图纸聚合与 common 功率侧/控制侧划分测试。"""

from __future__ import annotations

import pytest

from clearance_check.altium.demo import build_demo_board, build_demo_sheets
from clearance_check.analysis import classify_board
from clearance_check.analysis.sheets import build_sheet_index, net_side, split_common_nets
from clearance_check.model import NetClass


@pytest.fixture(scope="module")
def demo_index():
    board, pins = build_demo_board()
    sheets, paths = build_demo_sheets()
    index = build_sheet_index(pins, sheets, board, paths, {}, "VPDB_main")
    classifications = classify_board(board, pins)
    return board, pins, index, classifications


def test_sheet_tabs_follow_demo_sheets(demo_index):
    _board, _pins, index, _cls = demo_index
    assert list(index.sheets) == ["VPDB_main", "Pri_Driver", "Aux_Power", "SR_Cout", "Sense"]
    assert all(index.sheets.values()), "每张图纸都应有网络"


def test_shared_nets_aggregated(demo_index):
    _board, _pins, index, _cls = demo_index
    shared = set(index.shared_nets())
    assert {"PRI_GND", "GATE", "FB_PRI", "SEC_GND", "SEC_+12V"} <= shared
    # 单页网络不应算作跨页
    assert "SW_NODE" not in shared


def test_local_nets_excluded_from_common(demo_index):
    _board, _pins, index, _cls = demo_index
    local = [net for net in index.sheets["VPDB_main"] if not index.is_shared(net)]
    assert "SW_NODE" in local
    assert "HV_DC_375V" in local


def test_common_split_into_power_and_control(demo_index):
    board, pins, index, classifications = demo_index
    buckets = split_common_nets(index, board, classifications, pins)
    assert "PRI_GND" in buckets["power"]
    assert "SEC_GND" in buckets["power"]
    assert "GATE" in buckets["control"]
    assert "FB_PRI" in buckets["control"]
    assert not set(buckets["power"]) & set(buckets["control"])


def test_net_side_rules(demo_index):
    board, pins, index, classifications = demo_index
    side, reason = net_side("PRI_GND", board, classifications["PRI_GND"], pins)
    assert side == "power" and "接地" in reason

    side, _ = net_side("GATE", board, classifications["GATE"], pins)
    assert side == "control"

    side, _ = net_side("SW_NODE", board, classifications["SW_NODE"], pins)
    assert side == "power"


def test_instance_suffix_attribution():
    """多通道图纸的编译位号带实例后缀，应归到对应图纸。"""
    pins = [
        {"component": "Q9_U_SR_Cout3", "pin": "D", "pin_name": "D", "net": "SW_SR3"},
        {"component": "Q9_U_SR_Cout14", "pin": "D", "pin_name": "D", "net": "SW_SR14"},
        {"component": "Q1", "pin": "D", "pin_name": "D", "net": "SW_MAIN"},
    ]
    sheets = {"SR_Cout": ["Q9"], "SR_Cout_sense": ["Q9"], "VPDB_main": ["Q1"]}
    instances = {"U_SR_Cout3": "SR_Cout", "U_SR_Cout14": "SR_Cout_sense"}
    index = build_sheet_index(pins, sheets, None, {}, instances, "VPDB_main")

    assert "SW_SR3" in index.sheets["SR_Cout"]
    assert "SW_SR14" in index.sheets["SR_Cout_sense"]
    assert "SW_MAIN" in index.sheets["VPDB_main"]
    assert "SW_SR3" not in index.sheets["SR_Cout_sense"]


def test_net_classes_present(demo_index):
    _board, _pins, index, classifications = demo_index
    classes = {classifications[net].net_class for net in index.net_sheets}
    assert NetClass.PRIMARY_SWITCHING in classes
    assert NetClass.SECONDARY in classes
