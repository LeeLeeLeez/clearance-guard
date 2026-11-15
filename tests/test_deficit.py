"""缺口小于 0.01 mm 的违规不上报。"""

from __future__ import annotations

import pytest

from clearance_check.analysis import find_violations
from clearance_check.analysis.clearance import MIN_DEFICIT_MM
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import Board, NetClass, NetClassification, Pad


def _board(edge_gap: float) -> tuple[Board, dict]:
    """两焊盘外沿之间的净间距 = edge_gap（焊盘 1×1 mm）。"""
    board = Board(name="deficit")
    board.ensure_net("A")
    board.ensure_net("B")
    board.add_pad(Pad(name="1", net="A", component="R1", x=0.0, y=0.0,
                      width=1.0, height=1.0))
    board.add_pad(Pad(name="1", net="B", component="R2", x=1.0 + edge_gap, y=0.0,
                      width=1.0, height=1.0))
    classifications = {
        "A": NetClassification(net="A", net_class=NetClass.PRIMARY_CONST, voltage=400.0),
        "B": NetClassification(net="B", net_class=NetClass.SECONDARY, voltage=5.0),
    }
    return board, classifications


def test_tiny_deficit_ignored():
    rules = ClearanceRules()
    # 要求 8 mm，实际 7.995 mm → 缺口 0.005 mm，应忽略
    board, classifications = _board(8.0 - 0.005)
    assert find_violations(board, classifications, rules) == []


def test_real_deficit_reported():
    rules = ClearanceRules()
    board, classifications = _board(8.0 - 0.05)
    violations = find_violations(board, classifications, rules)
    assert violations
    assert violations[0].required_mm - violations[0].actual_mm >= MIN_DEFICIT_MM - 1e-9


def test_threshold_value():
    assert pytest.approx(0.01) == MIN_DEFICIT_MM
