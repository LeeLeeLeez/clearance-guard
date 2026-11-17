"""类别矩阵默认关闭：改由器件间/同网络/隔离带/安装孔规则判定。"""

import pytest

from clearance_check.analysis import find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import Board, NetClass, NetClassification, Pad


def _board(gap: float):
    board = Board(name="matrix")
    board.add_pad(Pad(name="1", net="PRI", component="A", x=0.0, y=0.0,
                      width=1.0, height=1.0))
    board.add_pad(Pad(name="1", net="SW", component="B", x=1.0 + gap, y=0.0,
                      width=1.0, height=1.0))
    classes = {
        "PRI": NetClassification(net="PRI", net_class=NetClass.PRIMARY_CONST, voltage=0.0),
        "SW": NetClassification(net="SW", net_class=NetClass.PRIMARY_SWITCHING, voltage=0.0),
    }
    return board, classes


def test_matrix_disabled_by_default():
    board, classes = _board(0.45)
    rules = ClearanceRules(body_gap_enabled=False)
    assert rules.matrix_enabled is False
    # 0.45 ≥ 器件间 0.3，矩阵关闭后不再按 0.5 报
    assert find_violations(board, classes, rules) == []


def test_matrix_enabled_uses_matrix_value():
    board, classes = _board(0.45)
    rules = ClearanceRules(body_gap_enabled=False, matrix_enabled=True)
    violations = find_violations(board, classes, rules)
    assert violations
    assert violations[0].required_mm == pytest.approx(0.5)
    assert "类别矩阵" in violations[0].rule_name


def test_matrix_toggle_roundtrip():
    rules = ClearanceRules(matrix_enabled=True)
    restored = ClearanceRules.from_dict(rules.to_dict())
    assert restored.matrix_enabled is True
