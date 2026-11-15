"""器件焊盘到板框外形的最小距离规则（默认 0.4 mm）。"""

from __future__ import annotations

import pytest

from clearance_check.analysis import find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import Board, NetClass, NetClassification, Pad


def _board(pad_x: float) -> tuple[Board, dict]:
    board = Board(name="edge", outline=[(0, 0), (100, 0), (100, 50), (0, 50)])
    board.ensure_net("NET_A")
    board.add_pad(Pad(name="1", net="NET_A", component="R1", x=pad_x, y=25.0,
                      width=1.0, height=1.0))
    classifications = {
        "NET_A": NetClassification(net="NET_A", net_class=NetClass.SECONDARY, voltage=5.0)
    }
    return board, classifications


def test_pad_too_close_to_outline():
    # 焊盘左沿 = 0.8 - 0.5 = 0.3 mm，小于 0.4 mm
    board, classifications = _board(0.8)
    violations = find_violations(board, classifications, ClearanceRules())

    hits = [v for v in violations if v.item_b == "板框"]
    assert hits
    assert hits[0].required_mm == pytest.approx(0.4)
    assert hits[0].actual_mm == pytest.approx(0.3, abs=0.02)
    assert "板框" in hits[0].rule_name


def test_pad_far_enough_from_outline():
    # 焊盘左沿 = 1.5 - 0.5 = 1.0 mm，满足 0.4 mm
    board, classifications = _board(1.5)
    violations = find_violations(board, classifications, ClearanceRules())
    assert not [v for v in violations if v.item_b == "板框"]


def test_rule_can_be_disabled():
    board, classifications = _board(0.8)
    rules = ClearanceRules(board_edge_enabled=False)
    violations = find_violations(board, classifications, rules)
    assert not [v for v in violations if v.item_b == "板框"]
