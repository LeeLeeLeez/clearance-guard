"""「跟随网络」功能测试：自动命名识别、跟随复制、快照保存。"""

from __future__ import annotations

import pytest

from clearance_check.altium.demo import build_demo_board
from clearance_check.analysis import apply_follow_target, classify_board
from clearance_check.model import is_auto_named, named_net_candidates
from clearance_check.snapshot import load_snapshot, save_snapshot


def test_auto_named_detection():
    assert is_auto_named("NetD10_1")
    assert is_auto_named("NetJP3_2")
    assert is_auto_named("NetP1_15")
    assert is_auto_named("NetJP10_U_SR_Cout13_1")
    assert not is_auto_named("12Vs")
    assert not is_auto_named("SGND")
    assert not is_auto_named("Vin_Bus")
    assert not is_auto_named("18Vp-H1")


def test_named_candidates_exclude_auto():
    names = ["NetD10_1", "SGND", "12Vs", "NetC56_1"]
    assert named_net_candidates(names) == ["12Vs", "SGND"]


def test_follow_copies_class_voltage_and_reference():
    board, pins = build_demo_board()
    result = classify_board(board, pins)

    target = result["GATE"]
    target.net_class = type(target.net_class).SECONDARY
    target.voltage = 7.5
    target.voltage_min = 7.5
    target.voltage_max = 7.5
    target.ref_ground = "SGND"

    follower = result["C1_2"] if "C1_2" in result else next(iter(result.values()))
    follower.follow_net = "GATE"
    apply_follow_target(result)

    assert follower.net_class is target.net_class
    assert follower.voltage == pytest.approx(7.5)
    assert follower.ref_ground == "SGND"
    assert any("跟随 GATE" in reason for reason in follower.reasons)


def test_follow_target_missing_is_cleared():
    board, pins = build_demo_board()
    result = classify_board(board, pins)
    item = next(iter(result.values()))
    item.follow_net = "不存在的网络"
    apply_follow_target(result)
    assert item.follow_net == ""


def test_follow_survives_snapshot(tmp_path):
    board, pins = build_demo_board()
    result = classify_board(board, pins)
    item = next(value for name, value in result.items() if name != "GATE")
    item.follow_net = "GATE"

    path = tmp_path / "follow.json"
    save_snapshot(
        path,
        board=board,
        classifications=result,
        pins=pins,
        sheet_components={},
        sheet_paths={},
        instance_sheets={},
        top_sheet="",
        project_path="demo",
        app_version="test",
    )
    snapshot = load_snapshot(path)
    assert snapshot.classifications[item.net].follow_net == "GATE"


def test_zero_ohm_never_locks_named_net():
    """0Ω 关系里，自动命名的网络只能跟随有命名网络，不能反过来锁住它。"""
    from clearance_check.model import Component, Pad

    board, pins = build_demo_board()
    board.add_component(Component(designator="R91", comment="0R 1/16W", footprint="R0402"))
    board.add_pad(
        Pad(name="1", net="HV_DC_375V", component="R91", x=48.0, y=40.0, width=1.0, height=1.0)
    )
    board.add_pad(
        Pad(name="2", net="NetTP1", component="R91", x=49.2, y=40.0, width=1.0, height=1.0)
    )
    result = classify_board(board, pins)

    assert result["NetTP1"].follow_net == "HV_DC_375V"
    assert result["HV_DC_375V"].follow_net == ""


def test_named_net_cannot_follow_auto_named():
    board, pins = build_demo_board()
    result = classify_board(board, pins)
    named = result["GATE"]
    named.follow_net = "NetTP1"       # 自动命名的网络
    apply_follow_target(result)
    assert named.follow_net == ""
