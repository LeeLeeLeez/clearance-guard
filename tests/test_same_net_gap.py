"""同网络焊盘最小间距（默认 0.2 mm）。"""

import pytest

from clearance_check.analysis import find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import Board, NetClass, NetClassification, Pad


#: 测试只关心焊盘间距，关掉本体规则（测试板的假器件没有 3D 本体）
RULES = ClearanceRules(body_gap_enabled=False)


def _board(gap: float, net_a: str = "N1", net_b: str = "N1"):
    board = Board(name="same-net")
    board.ensure_net(net_a)
    board.ensure_net(net_b)
    board.add_pad(Pad(name="1", net=net_a, component="A", x=0.0, y=0.0,
                      width=1.0, height=1.0))
    board.add_pad(Pad(name="1", net=net_b, component="B", x=1.0 + gap, y=0.0,
                      width=1.0, height=1.0))
    classes = {
        name: NetClassification(net=name, net_class=NetClass.PRIMARY_CONST, voltage=0.0)
        for name in {net_a, net_b}
    }
    return board, classes


def test_same_net_below_component_gap_is_allowed():
    board, classes = _board(0.25)
    assert find_violations(board, classes, RULES) == []


def test_same_net_below_0_2_is_flagged():
    board, classes = _board(0.15)
    violations = find_violations(board, classes, RULES)
    assert violations
    assert violations[0].required_mm == pytest.approx(0.2)
    assert "同网络" in violations[0].rule_name


def test_different_net_still_uses_component_gap():
    board, classes = _board(0.25, net_a="N1", net_b="N2")
    violations = find_violations(board, classes, RULES)
    assert violations
    assert violations[0].required_mm == pytest.approx(0.3)
    assert "器件间" in violations[0].rule_name


def test_same_net_rule_can_be_disabled():
    board, classes = _board(0.25)
    rules = ClearanceRules(body_gap_enabled=False, same_net_gap_enabled=False)
    violations = find_violations(board, classes, rules)
    assert violations
    assert violations[0].required_mm == pytest.approx(0.3)


def test_same_net_rule_roundtrip():
    rules = ClearanceRules(same_net_gap_mm=0.15, same_net_gap_enabled=False)
    restored = ClearanceRules.from_dict(rules.to_dict())
    assert restored.same_net_gap_mm == pytest.approx(0.15)
    assert restored.same_net_gap_enabled is False
