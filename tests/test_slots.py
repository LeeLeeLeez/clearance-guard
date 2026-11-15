"""避距槽（板级挖空）对电气间隙 / 爬电距离的影响。"""

from __future__ import annotations

import pytest

from clearance_check.analysis import find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.analysis.slots import SlotRect, best_slot_path
from clearance_check.model import (
    Board,
    BoardCutout,
    NetClass,
    NetClassification,
    Pad,
)


def test_slot_path_detects_crossing():
    slot = SlotRect(-1.0, -1.0, 1.0, 1.0)
    # 水平穿过 2×2 的槽：入 2 mm + 绕上边 4 mm + 出 2 mm = 8 mm
    path = best_slot_path(-3.0, 0.0, 3.0, 0.0, [slot])
    assert path is not None
    length, rect = path
    assert rect == slot
    assert length == pytest.approx(8.0, abs=0.05)


def test_slot_path_ignores_non_crossing():
    slot = SlotRect(-1.0, -1.0, 1.0, 1.0)
    assert best_slot_path(-3.0, 5.0, 3.0, 5.0, [slot]) is None


def test_rule_uses_clearance_when_slot_bridges():
    rules = ClearanceRules()
    gap, rule = rules.required(NetClass.PRIMARY_CONST, NetClass.SECONDARY)
    assert gap == pytest.approx(8.0)
    assert "爬电" in rule

    gap, rule = rules.required(
        NetClass.PRIMARY_CONST, NetClass.SECONDARY, slot_bridge=True
    )
    assert gap == pytest.approx(6.0)
    assert "电气间隙" in rule


def _board_with_gap(distance: float, slot: bool) -> tuple[Board, dict]:
    board = Board(name="slot")
    board.ensure_net("PRI")
    board.ensure_net("SEC")
    board.add_pad(Pad(name="1", net="PRI", component="A", x=0.0, y=0.0, width=1.0, height=2.0))
    board.add_pad(
        Pad(name="1", net="SEC", component="B", x=distance, y=0.0, width=1.0, height=2.0)
    )
    if slot:
        # 两焊盘中间的避距槽（宽 2 mm）
        board.cutouts.append(
            BoardCutout(kind="slot", x=distance / 2, y=0.0, width=2.0, height=6.0)
        )
    classifications = {
        "PRI": NetClassification(net="PRI", net_class=NetClass.PRIMARY_CONST, voltage=400.0),
        "SEC": NetClassification(net="SEC", net_class=NetClass.SECONDARY, voltage=12.0),
    }
    return board, classifications


def test_clearance_without_slot_requires_creepage():
    board, classifications = _board_with_gap(7.0, slot=False)
    violations = find_violations(board, classifications, ClearanceRules())
    assert any(v.required_mm == pytest.approx(8.0) for v in violations)


def test_clearance_with_slot_uses_six_mm():
    board, classifications = _board_with_gap(7.0, slot=True)
    violations = find_violations(board, classifications, ClearanceRules())
    # 7 mm ≥ 电气间隙 6 mm，绕槽沿面远大于 8 mm → 不再违规
    assert not violations


def test_clearance_with_slot_still_flags_below_six():
    board, classifications = _board_with_gap(5.0, slot=True)
    violations = find_violations(board, classifications, ClearanceRules())
    assert violations
    assert violations[0].required_mm == pytest.approx(6.0)
    assert "电气间隙" in violations[0].rule_name


def test_cutout_points_survive_snapshot(tmp_path):
    from clearance_check.snapshot import load_snapshot, save_snapshot

    board, classifications = _board_with_gap(7.0, slot=True)
    board.cutouts[0].points = [(1.0, 2.0), (3.0, 2.0), (3.0, 5.0)]

    path = tmp_path / "slots.json"
    save_snapshot(
        path,
        board=board,
        classifications=classifications,
        pins=[],
        sheet_components={},
        sheet_paths={},
        instance_sheets={},
        top_sheet="",
        project_path="test",
        app_version="test",
    )
    snapshot = load_snapshot(path)
    cutout = snapshot.board.cutouts[0]
    assert cutout.kind == "slot"
    assert cutout.points == [(1.0, 2.0), (3.0, 2.0), (3.0, 5.0)]


def test_match_cutout_by_center_and_size():
    from clearance_check.ui.main_window import MainWindow

    slots = [
        BoardCutout(kind="slot", x=10.0, y=20.0, width=8.0, height=2.0),
        BoardCutout(kind="slot", x=60.0, y=20.0, width=12.0, height=4.0),
    ]
    outline = {"x": 10.1, "y": 20.05, "width": 8.02, "height": 2.01, "points": []}
    assert MainWindow._match_cutout(slots, outline) is slots[0]

    far = {"x": 200.0, "y": 20.0, "width": 8.0, "height": 2.0, "points": []}
    assert MainWindow._match_cutout(slots, far) is None
