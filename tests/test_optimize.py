"""矩形选区最小移动量优化。"""

from __future__ import annotations

import pytest

from clearance_check.analysis.optimize import optimize_area
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import Board, Component, NetClass, NetClassification, Pad


def _board() -> tuple[Board, dict]:
    board = Board(name="opt", outline=[(0, 0), (60, 0), (60, 40), (0, 40)])
    board.ensure_net("A")
    board.ensure_net("B")
    board.add_component(Component(designator="R1", comment="", x=20.0, y=20.0))
    board.add_component(Component(designator="R2", comment="", x=20.6, y=20.0))
    board.add_pad(Pad(name="1", net="A", component="R1", x=20.0, y=20.0,
                      width=1.0, height=1.0))
    board.add_pad(Pad(name="1", net="B", component="R2", x=20.6, y=20.0,
                      width=1.0, height=1.0))
    classifications = {
        # 同类（副边）→ 只要求矩阵间距，移动 0.2 mm 级即可验证“最小移动量”
        "A": NetClassification(net="A", net_class=NetClass.SECONDARY, voltage=5.0),
        "B": NetClassification(net="B", net_class=NetClass.SECONDARY, voltage=5.0),
    }
    return board, classifications


def test_conflicting_components_get_moved():
    board, classifications = _board()
    rules = ClearanceRules()
    plans = optimize_area(board, classifications, rules, (10, 10, 35, 30),
                          step=0.5, max_shift=6.0)

    moved = [plan for plan in plans if plan.moved]
    assert moved, "应该至少移动一个器件"
    assert all(plan.shift_mm > 0 for plan in moved)
    # 移动量要小（隔离带 8 mm，两焊盘贴在一起）
    assert min(plan.shift_mm for plan in moved) <= 2.0


def test_component_without_violation_stays():
    board, classifications = _board()
    # 把 R2 挪远，R1 那边就没有冲突了
    board.pads_of_component("R2")[0].x = 45.0
    plans = optimize_area(board, classifications, ClearanceRules(), (10, 10, 30, 30),
                          step=0.5, max_shift=4.0)
    assert all(not plan.moved for plan in plans)


def test_outside_area_not_touched():
    board, classifications = _board()
    plans = optimize_area(board, classifications, ClearanceRules(), (40, 30, 55, 38),
                          step=0.5, max_shift=4.0)
    assert plans == []


def test_moved_component_stays_inside_board():
    """靠近板边的器件被优化后必须完整留在板框内。"""
    from clearance_check.geometry import point_in_polygon_any

    board = Board(name="edge", outline=[(0, 0), (60, 0), (60, 40), (0, 40)])
    board.ensure_net("A")
    board.ensure_net("B")
    board.add_component(Component(designator="R1", comment="", x=58.6, y=20.0))
    board.add_component(Component(designator="R2", comment="", x=58.6, y=21.0))
    board.add_pad(Pad(name="1", net="A", component="R1", x=58.6, y=20.0,
                      width=1.0, height=1.0))
    board.add_pad(Pad(name="1", net="B", component="R2", x=58.6, y=21.0,
                      width=1.0, height=1.0))
    classifications = {
        "A": NetClassification(net="A", net_class=NetClass.SECONDARY, voltage=5.0),
        "B": NetClassification(net="B", net_class=NetClass.SECONDARY, voltage=5.0),
    }

    plans = optimize_area(board, classifications, ClearanceRules(), (50, 10, 59.9, 30))
    outline = board.outline
    for pad in board.pads:
        from clearance_check.geometry import pad_polygon

        for vertex in pad_polygon(pad):
            assert point_in_polygon_any(vertex, outline), f"{pad.component} 出板框"
    assert any(plan.moved for plan in plans)


def test_outside_candidate_is_rejected():
    """直接把器件挪到板外的候选必须被判为冲突（无解）。"""
    from clearance_check.analysis.optimize import _conflicts, _items_of, _shift

    board = Board(name="edge2", outline=[(0, 0), (20, 0), (20, 20), (0, 20)])
    board.ensure_net("A")
    board.add_component(Component(designator="R1", comment="", x=19.4, y=10.0))
    board.add_pad(Pad(name="1", net="A", component="R1", x=19.4, y=10.0,
                      width=1.0, height=1.0))
    classifications = {
        "A": NetClassification(net="A", net_class=NetClass.SECONDARY, voltage=5.0)
    }
    items = _items_of(board, [], [])
    moving = [_shift(items[0], 3.0, 0.0)]   # 向右 3 mm，焊盘已越过 x=20
    assert _conflicts(moving, [], board, classifications, ClearanceRules(), False) is True

    inside = [_shift(items[0], -1.0, 0.0)]  # 向左 1 mm，仍在板内
    assert _conflicts(inside, [], board, classifications, ClearanceRules(), False) is False


def test_default_shift_cap_is_one_mm():
    """默认位移上限 1 mm：所有可行解的位移都不能超过它。"""
    board = Board(name="cap", outline=[(0, 0), (80, 0), (80, 60), (0, 60)])
    board.ensure_net("A")
    board.ensure_net("B")
    board.add_component(Component(designator="R1", comment="", x=30.0, y=30.0))
    board.add_component(Component(designator="R2", comment="", x=30.4, y=30.0))
    board.add_pad(Pad(name="1", net="A", component="R1", x=30.0, y=30.0,
                      width=1.0, height=1.0))
    board.add_pad(Pad(name="1", net="B", component="R2", x=30.4, y=30.0,
                      width=1.0, height=1.0))
    classifications = {
        "A": NetClassification(net="A", net_class=NetClass.SECONDARY, voltage=5.0),
        "B": NetClassification(net="B", net_class=NetClass.SECONDARY, voltage=5.0),
    }
    plans = optimize_area(board, classifications, ClearanceRules(), (20, 20, 40, 40))
    for plan in plans:
        assert plan.shift_mm <= 1.0 + 1e-9, f"{plan.designator} 位移 {plan.shift_mm:.2f} mm"
