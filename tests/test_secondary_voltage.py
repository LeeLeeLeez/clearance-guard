"""副边 ↔ 副边不考虑电压差增量。"""

import pytest

from clearance_check.analysis import find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import Board, NetClass, NetClassification, Pad


def test_secondary_pair_ignores_voltage_step():
    rules = ClearanceRules(
        voltage_step_v=400.0, voltage_step_mm=1.3, voltage_step_granularity_v=100.0
    )
    gap, rule = rules.required(NetClass.SECONDARY, NetClass.SECONDARY, 1099.0, 0.0)
    assert gap == pytest.approx(0.0)
    assert "电压差" not in rule


def test_secondary_pair_keeps_matrix_when_enabled():
    rules = ClearanceRules(
        matrix_enabled=True,
        voltage_step_v=400.0,
        voltage_step_mm=1.3,
        voltage_step_granularity_v=100.0,
    )
    gap, _ = rules.required(NetClass.SECONDARY, NetClass.SECONDARY, 1099.0, 0.0)
    assert gap == pytest.approx(0.2)  # 默认矩阵 副边/副边


def _board(gap: float):
    board = Board(name="secondary")
    board.add_pad(Pad(name="1", net="S1", component="A", x=0.0, y=0.0,
                      width=1.0, height=1.0))
    board.add_pad(Pad(name="1", net="S2", component="B", x=1.0 + gap, y=0.0,
                      width=1.0, height=1.0))
    classes = {
        "S1": NetClassification(net="S1", net_class=NetClass.SECONDARY, voltage=1099.0),
        "S2": NetClassification(net="S2", net_class=NetClass.SECONDARY, voltage=0.0),
    }
    return board, classes


def test_secondary_pair_does_not_raise_gap_by_voltage():
    board, classes = _board(0.35)
    rules = ClearanceRules(
        body_gap_enabled=False,
        voltage_step_v=400.0,
        voltage_step_mm=1.3,
        voltage_step_granularity_v=100.0,
    )
    # 若按电压差会要求 3.575；副边↔副边不参与，只按器件间 0.3 → 0.35 通过
    assert find_violations(board, classes, rules) == []
