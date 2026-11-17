"""电压分类、规则与避距计算测试（基于内置示例工程）。"""

from __future__ import annotations

import pytest

from clearance_check.altium.demo import build_demo_board
from clearance_check.analysis import aggregate_by_component, classify_board, find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import Component, NetClass, NetClassification, Pad


@pytest.fixture(scope="module")
def demo():
    board, pins = build_demo_board()
    return board, pins, classify_board(board, pins)


def test_primary_rail_classified(demo):
    _, _, result = demo
    item = result["HV_DC_375V"]
    assert item.net_class is NetClass.PRIMARY_CONST
    assert item.voltage == pytest.approx(375.0)


def test_switching_node_has_jump_range(demo):
    _, _, result = demo
    item = result["SW_NODE"]
    assert item.net_class is NetClass.PRIMARY_SWITCHING
    assert item.voltage_min == 0.0
    assert item.voltage_max == pytest.approx(375.0)


def test_secondary_side_detected_through_transformer():
    board, pins = build_demo_board()
    result = classify_board(board, pins)
    for net in ("SEC_+12V", "SEC_GND", "SEC_+5V"):
        assert result[net].net_class is NetClass.SECONDARY
    assert result["SEC_+5V"].voltage == pytest.approx(5.0)


def test_unlabeled_net_falls_back_to_secondary(demo):
    """「未定」分类已取消：无法判明侧别的网络按副边处理，电压仍需人工补。"""
    _, _, result = demo
    item = result["NetTP1"]
    assert item.net_class is NetClass.SECONDARY
    assert item.needs_review


def test_isolation_violation_reported(demo):
    board, pins, result = demo
    violations = find_violations(board, result, ClearanceRules())
    pairs = {(v.item_a, v.item_b) for v in violations}
    assert ("T1", "D1") in pairs
    worst = max(violations, key=lambda v: v.deficit_mm)
    assert worst.required_mm == pytest.approx(8.0)
    assert worst.deficit_mm > 1.0


def test_same_component_pads_skipped_unless_isolation(demo):
    """默认不检查同器件内部焊盘，但隔离器件内部的爬电仍要报出。"""
    board, pins, result = demo
    rules = ClearanceRules()
    violations = find_violations(board, result, rules)
    for violation in violations:
        if violation.item_a == violation.item_b:
            assert rules.is_isolation_pair(violation.class_a, violation.class_b)


def test_same_component_can_be_enabled(demo):
    board, pins, result = demo
    base = find_violations(board, result, ClearanceRules())
    enabled = find_violations(board, result, ClearanceRules(check_same_component=True))
    assert len(enabled) >= len(base)
    assert any(v.item_a != v.item_b for v in enabled)


def test_aggregation_keeps_worst_per_pair(demo):
    board, pins, result = demo
    violations = find_violations(board, result, ClearanceRules())
    aggregated = aggregate_by_component(violations)
    assert len(aggregated) < len(violations)
    for item in aggregated:
        assert item.deficit_mm > 0


def test_drive_net_voltage_and_reference_ground(demo):
    """驱动网络要能从名称读出电压，并给出参考地。"""
    _board, _pins, result = demo
    gate = result["GATE"]
    assert gate.is_drive
    assert gate.ref_ground == "PRI_GND"
    assert gate.voltage == pytest.approx(20.0)


def test_voltage_suffix_forms():
    from clearance_check.analysis.classify import extract_voltages

    assert extract_voltages("18Vp-H1") == [18.0]
    assert extract_voltages("12Vs") == [12.0]
    assert extract_voltages("3V3D") == [3.3]
    assert extract_voltages("Vgs_p_H2") == []
    assert extract_voltages("H1-GND") == []


def test_assigned_mount_point_participates_in_clearance(demo):
    """指定电位的安装焊盘要参与避距检查。"""
    board, pins, result = demo
    mount = board.mounts[0]
    pad = next(p for p in board.pads if p.net == "HV_DC_375V")
    mount.x = pad.x + pad.width / 2 + 2.0
    mount.y = pad.y
    mount.width = mount.height = 4.0
    mount.net_class = NetClass.SECONDARY
    mount.voltage = 12.0
    mount.voltage_min = 12.0
    mount.voltage_max = 12.0

    violations = find_violations(board, result, ClearanceRules(), mounts=board.mounts)
    assert any(mount.name in (v.item_a, v.item_b) for v in violations)


def test_unassigned_mount_point_detected():
    """未指派网络的安装孔要能被校验入口识别并报错，不再静默忽略。"""
    from api.result_service import unassigned_mounts
    from clearance_check.model import MountPoint

    # 默认就是 SGND，算已指派
    assert unassigned_mounts([MountPoint(name="MH1")]) == []
    # net 为空 → 未指派
    assert unassigned_mounts([MountPoint(name="MH2", net="")]) == ["MH2"]
    # 电位仍是「未定」→ 未指派
    assert unassigned_mounts([MountPoint(name="MH3", net_class=NetClass.UNKNOWN)]) == ["MH3"]
    # 停用的孔不参与校验，不报错
    assert unassigned_mounts([MountPoint(name="MH4", net="", enabled=False)]) == []


def test_demo_mount_defaults_to_sgnd(demo):
    board, _pins, _result = demo
    assert board.mounts
    assert all(m.net == "SGND" for m in board.mounts)


def test_voltage_difference_rule_raises_gap():
    rules = ClearanceRules(
        voltage_step_enabled=True, voltage_step_v=100.0, voltage_step_mm=0.5
    )
    gap, rule = rules.required(
        NetClass.PRIMARY_SWITCHING, NetClass.PRIMARY_CONST, 375.0, 0.0
    )
    # 100 V 一档，375 V 不足一档按 4 档算 → 2.0 mm
    assert gap == pytest.approx(2.0)
    assert "电压差" in rule


def test_voltage_difference_uses_100v_granularity_and_interpolates():
    rules = ClearanceRules(
        voltage_step_v=400.0, voltage_step_mm=1.3, voltage_step_granularity_v=100.0
    )
    gap, _ = rules.required(
        NetClass.PRIMARY_SWITCHING, NetClass.PRIMARY_CONST, 400.0, 0.0
    )
    assert gap == pytest.approx(1.3)

    # 1099 V → 11 档 × 0.325 mm = 3.575 mm，不再是 800 V 档的 2.6 mm
    gap, rule = rules.required(
        NetClass.PRIMARY_SWITCHING, NetClass.PRIMARY_CONST, 1099.0, 0.0
    )
    assert gap == pytest.approx(3.575)
    assert "100 V 一档" in rule

    # 按最近档位四舍五入：<50 V 向下、>=50 V 向上
    for delta, expected in ((418.0, 1.3), (449.0, 1.3), (450.0, 1.625)):
        gap, _ = rules.required(
            NetClass.PRIMARY_SWITCHING, NetClass.PRIMARY_CONST, delta, 0.0
        )
        assert gap == pytest.approx(expected), (delta, gap)


def test_rules_roundtrip(tmp_path):
    rules = ClearanceRules(isolation_gap_mm=6.5, voltage_step_mm=0.75)
    path = tmp_path / "rules.json"
    rules.save(path)
    loaded = ClearanceRules.load(path)
    assert loaded.isolation_gap_mm == pytest.approx(6.5)
    assert loaded.voltage_step_mm == pytest.approx(0.75)
    assert loaded.base_gap(NetClass.SECONDARY, NetClass.SECONDARY) == pytest.approx(0.2)


def test_zero_ohm_value_detection():
    """10Ω 及以下都按等电位直连处理（含 5mΩ 采样电阻）。"""
    from clearance_check.analysis.classify import _is_zero_ohm, _resistance_value

    assert _is_zero_ohm("0R 1/16W 0402 50V")
    assert _is_zero_ohm("0R")
    assert _is_zero_ohm("0")
    assert _is_zero_ohm("0R00")
    assert _is_zero_ohm("0R05")
    assert _is_zero_ohm("4R7")
    assert _is_zero_ohm("10R")
    assert _is_zero_ohm("10")
    assert not _is_zero_ohm("11R")
    assert not _is_zero_ohm("10K")
    assert not _is_zero_ohm("1M")
    assert not _is_zero_ohm("")
    assert _resistance_value("4R7") == pytest.approx(4.7)
    assert _resistance_value("10K") == pytest.approx(10000.0)


def test_zero_ohm_resistor_makes_both_nets_equal(demo):
    """0Ω 电阻两端等电位：右端网络应跟随左端的类别与电压。"""
    board, pins, _ = demo
    board.add_component(
        Component(designator="R90", comment="0R 1/16W 0402", footprint="R0402")
    )
    board.add_pad(
        Pad(name="1", net="HV_DC_375V", component="R90", x=46.0, y=40.0, width=1.0, height=1.0)
    )
    board.add_pad(
        Pad(name="2", net="NetTP1", component="R90", x=47.2, y=40.0, width=1.0, height=1.0)
    )

    result = classify_board(board, pins)

    assert result["NetTP1"].net_class is result["HV_DC_375V"].net_class
    assert result["NetTP1"].voltage == pytest.approx(result["HV_DC_375V"].voltage)
    assert any("R90" in reason for reason in result["NetTP1"].reasons)
    # 0Ω 关系同时记为「跟随」，界面上直接可见
    assert result["NetTP1"].follow_net == "HV_DC_375V"


def test_low_ohm_follows_named_net_without_voltage(demo):
    """命名网络还没有电压时，自动命名的一侧也要建立跟随关系（只是不复制数值）。"""
    board, pins, _ = demo
    board.add_component(
        Component(designator="R92", comment="10R 1/16W 0402", footprint="R0402")
    )
    board.add_pad(
        Pad(name="1", net="HV_SENSE", component="R92", x=50.0, y=40.0, width=1.0, height=1.0)
    )
    board.add_pad(
        Pad(name="2", net="NetTP1", component="R92", x=51.2, y=40.0, width=1.0, height=1.0)
    )
    result = classify_board(board, pins)

    assert result["NetTP1"].follow_net == "HV_SENSE"
    assert result["HV_SENSE"].follow_net == ""
    assert result["NetTP1"].net_class is result["HV_SENSE"].net_class


def test_inductor_links_named_and_auto_net(demo):
    """电感把命名网络与自动命名网络连在一起：自动命名侧跟随命名侧。"""
    board, pins, _ = demo
    board.add_component(
        Component(
            designator="L9",
            comment="LQM21PN1R0NGRD 1uH/700mA",
            footprint="0805",
        )
    )
    board.add_pad(
        Pad(name="1", net="HV_DC_375V", component="L9", x=52.0, y=40.0, width=1.0, height=1.0)
    )
    board.add_pad(
        Pad(name="2", net="NetTP1", component="L9", x=53.2, y=40.0, width=1.0, height=1.0)
    )
    result = classify_board(board, pins)

    assert result["NetTP1"].follow_net == "HV_DC_375V"
    assert result["NetTP1"].net_class is result["HV_DC_375V"].net_class
    assert result["NetTP1"].voltage == pytest.approx(result["HV_DC_375V"].voltage)


def test_inductor_does_not_merge_switching_node():
    """电感一端是开关节点时不做等电位合并（SW 与输出不是同一电位）。"""
    board, pins = build_demo_board()
    board.add_component(
        Component(designator="L8", comment="4.7uH/3A", footprint="0805")
    )
    board.add_pad(
        Pad(name="1", net="SW_NODE", component="L8", x=54.0, y=40.0, width=1.0, height=1.0)
    )
    board.add_pad(
        Pad(name="2", net="NetTP1", component="L8", x=55.2, y=40.0, width=1.0, height=1.0)
    )
    result = classify_board(board, pins)

    assert result["NetTP1"].follow_net == ""


def test_diode_makes_auto_net_follow_named_end():
    """二极管一端连有命名网络时，自动命名的另一端跟随它。"""
    board, pins = build_demo_board()
    board.add_component(
        Component(designator="D9", comment="MBR2H200SF 200V/2A", footprint="SMA")
    )
    board.add_pad(
        Pad(name="1", net="HV_DC_375V", component="D9", x=56.0, y=40.0, width=1.0, height=1.0)
    )
    board.add_pad(
        Pad(name="2", net="NetTP1", component="D9", x=57.2, y=40.0, width=1.0, height=1.0)
    )
    result = classify_board(board, pins)

    assert result["NetTP1"].follow_net == "HV_DC_375V"
    assert result["NetTP1"].net_class is result["HV_DC_375V"].net_class
    assert result["NetTP1"].voltage == pytest.approx(result["HV_DC_375V"].voltage)


def test_diode_between_named_nets_is_not_merged():
    """两端都有命名（如整流管接地与输出）时不做跟随，避免把 0 V 并到输出电压上。"""
    board, pins = build_demo_board()
    board.add_component(Component(designator="D8", comment="1N4148", footprint="SOD123"))
    board.add_pad(
        Pad(name="1", net="PRI_GND", component="D8", x=58.0, y=40.0, width=1.0, height=1.0)
    )
    board.add_pad(
        Pad(name="2", net="SEC_GND", component="D8", x=59.2, y=40.0, width=1.0, height=1.0)
    )
    result = classify_board(board, pins)

    assert result["SEC_GND"].follow_net == ""
    assert result["PRI_GND"].follow_net == ""


def test_component_gap_applies_regardless_of_net():
    """不同器件之间 0.3 mm 起判，同一网络也照样检查，级别为 error。"""
    from clearance_check.model import Board

    board = Board(name="gap")
    board.ensure_net("SAME_NET")
    board.add_component(Component(designator="R77", comment="10k", footprint="0402"))
    board.add_component(Component(designator="R78", comment="10k", footprint="0402"))
    board.add_pad(Pad(name="1", net="SAME_NET", component="R77", x=0.0, y=0.0, width=1.0, height=1.0))
    board.add_pad(Pad(name="1", net="SAME_NET", component="R78", x=1.1, y=0.0, width=1.0, height=1.0))
    classifications = {
        "SAME_NET": NetClassification(
            net="SAME_NET", net_class=NetClass.PRIMARY_CONST, voltage=24.0,
            voltage_min=24.0, voltage_max=24.0,
        )
    }
    violations = find_violations(board, classifications, ClearanceRules())

    hits = [v for v in violations if {v.item_a, v.item_b} == {"R77", "R78"}]
    assert hits, "同一网络的不同器件焊盘也应检查"
    assert hits[0].severity == "error"
    assert hits[0].required_mm == pytest.approx(0.3)
    assert hits[0].actual_mm == pytest.approx(0.1, abs=0.01)


def test_same_component_violation_is_warning():
    """同一器件内部（隔离器件爬电）的违规标记为 warning。"""
    from clearance_check.model import Board

    board = Board(name="inner")
    board.ensure_net("PRI")
    board.ensure_net("SEC")
    board.add_component(Component(designator="U9", comment="", footprint="SOIC"))
    board.add_pad(Pad(name="1", net="PRI", component="U9", x=0.0, y=0.0, width=1.0, height=1.0))
    board.add_pad(Pad(name="8", net="SEC", component="U9", x=1.2, y=0.0, width=1.0, height=1.0))
    classifications = {
        "PRI": NetClassification(net="PRI", net_class=NetClass.PRIMARY_CONST, voltage=375.0),
        "SEC": NetClassification(net="SEC", net_class=NetClass.SECONDARY, voltage=12.0),
    }
    violations = find_violations(board, classifications, ClearanceRules())

    assert violations
    assert all(v.severity == "warning" for v in violations)


def test_classify_only_new_nets(demo):
    """有配置基线时只判新增网络：返回集合只含指定网络，结论与全量一致。"""
    board, pins, _ = demo
    full = classify_board(board, pins)
    subset = classify_board(board, pins, only_nets={"GATE"})

    assert set(subset) == {"GATE"}
    assert subset["GATE"].net_class is full["GATE"].net_class
