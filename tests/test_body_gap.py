"""器件本体间距：必须按真实焊盘形状算，不能用正方形近似。"""

from __future__ import annotations

from clearance_check.analysis import find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import Board, Component, NetClass, NetClassification, Pad


def _board(dy: float) -> tuple[Board, dict]:
    """Q12（SOT-23 长条焊盘）与 R8（1206 焊盘）上下相邻，间距由 dy 控制。"""
    board = Board(name="body")
    board.add_component(Component(designator="Q12", comment="mos", x=10.0, y=10.0,
                                  layer="BOTTOM", bbox=(8.0, 9.0, 12.0, 11.0)))
    board.add_component(Component(designator="R8", comment="res", x=6.0, y=10.0 + dy,
                                  layer="BOTTOM", bbox=(4.0, 9.0 + dy, 8.0, 11.0 + dy)))
    board.ensure_net("A")
    board.add_pad(Pad(name="3", net="A", component="Q12", x=8.2, y=10.0,
                      width=1.07, height=0.60))
    board.add_pad(Pad(name="1", net="A", component="R8", x=7.2, y=10.0 + dy,
                      width=1.75, height=1.00))
    classifications = {
        "A": NetClassification(net="A", net_class=NetClass.SECONDARY, voltage=5.0)
    }
    return board, classifications


def test_long_pads_are_not_treated_as_squares():
    """真实间隙 0.5 mm 应合格；正方形近似会算成 0.1 mm 误报。"""
    board, classifications = _board(dy=1.3)  # 焊盘边缘相距 0.5 mm（> 0.3）
    violations = find_violations(board, classifications, ClearanceRules())
    hits = [v for v in violations if "本体" in v.rule_name]
    assert not hits, f"不该报本体违规，实际 {hits[0].actual_mm if hits else None}"


def test_real_interference_still_reported():
    board, classifications = _board(dy=0.7)  # 焊盘边缘重叠 0.1 mm
    violations = find_violations(board, classifications, ClearanceRules())
    hits = [v for v in violations if "本体" in v.rule_name]
    assert hits, "焊盘重叠时必须报本体违规"
