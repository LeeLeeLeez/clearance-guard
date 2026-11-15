"""烧录端子（半孔）豁免板框间距。"""

from __future__ import annotations

import pytest

from clearance_check.analysis import find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.eda.session import _guess_category
from clearance_check.model import Board, Component, NetClass, NetClassification, Pad


def test_guess_category_by_comment_and_footprint():
    assert _guess_category({"comment": "Program TP", "footprint": "Burn_TP"}) == "burn_terminal"
    assert _guess_category({"comment": "1uF", "footprint": "C0603"}) == ""


def _board(category: str) -> tuple[Board, dict]:
    board = Board(name="edge", outline=[(0, 0), (100, 0), (100, 50), (0, 50)])
    board.add_component(
        Component(designator="T26", comment="Program TP", footprint="Burn_TP",
                  x=0.8, y=25.0, category=category)
    )
    board.ensure_net("NET_A")
    board.add_pad(Pad(name="1", net="NET_A", component="T26", x=0.8, y=25.0,
                      width=1.0, height=1.0))
    classifications = {
        "NET_A": NetClassification(net="NET_A", net_class=NetClass.SECONDARY, voltage=5.0)
    }
    return board, classifications


def test_burn_terminal_exempt_from_board_edge():
    board, classifications = _board("burn_terminal")
    violations = find_violations(board, classifications, ClearanceRules())
    assert not [v for v in violations if v.item_b == "板框"]


def test_normal_component_still_checked():
    board, classifications = _board("")
    violations = find_violations(board, classifications, ClearanceRules())
    hits = [v for v in violations if v.item_b == "板框"]
    assert hits
    assert hits[0].required_mm == pytest.approx(0.4)
