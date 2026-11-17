"""3D 本体（ComponentBody）归属与优先判定。"""

from __future__ import annotations

from clearance_check.analysis import find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.eda.session import attach_bodies
from clearance_check.model import Board, Component, NetClass, NetClassification, Pad


def _board() -> Board:
    board = Board(name="b3d")
    board.add_component(Component(designator="Q12", comment="mos", x=10.0, y=10.0,
                                  layer="BOTTOM", bbox=(8.5, 9.0, 11.5, 11.0)))
    board.add_component(Component(designator="R8", comment="res", x=6.0, y=10.0,
                                  layer="BOTTOM", bbox=(4.5, 9.0, 7.5, 11.0)))
    board.ensure_net("A")
    board.add_pad(Pad(name="1", net="A", component="Q12", x=8.8, y=10.0,
                      width=1.0, height=0.6))
    board.add_pad(Pad(name="1", net="A", component="R8", x=7.2, y=10.0,
                      width=1.0, height=1.0))
    return board


def test_attach_bodies_by_center():
    board = _board()
    bodies = [
        {"x1": 8.6, "y1": 9.2, "x2": 11.4, "y2": 10.8, "layer": "Mechanical1"},
        {"x1": 4.6, "y1": 9.2, "x2": 7.4, "y2": 10.8, "layer": "Mechanical1"},
    ]
    matched = attach_bodies(board, bodies)
    assert matched == 2
    assert board.components["Q12"].body_bbox is not None
    assert board.components["R8"].body_bbox is not None


def test_body_overrides_pad_distance():
    """3D 本体比焊盘大，只按焊盘算会漏掉本体干涉。"""
    board = _board()
    # 焊盘相距 0.6（合格），但 3D 本体在 y 方向重叠 → 应判本体违规
    board.components["Q12"].body_bbox = (8.6, 9.2, 11.4, 10.9)
    board.components["R8"].body_bbox = (7.0, 9.1, 8.7, 10.95)
    classifications = {
        "A": NetClassification(net="A", net_class=NetClass.SECONDARY, voltage=5.0)
    }
    violations = find_violations(board, classifications, ClearanceRules())
    hits = [v for v in violations if "本体" in v.rule_name]
    assert hits, "有 3D 本体且重叠时必须报"
    assert hits[0].actual_mm == 0.0


def test_large_body_not_assigned_to_small_part():
    """旁边大器件的 3D 本体不能被塞给 0201 小电容（C80 那种误判）。"""
    board = Board(name="mis")
    board.add_component(Component(designator="C80", x=10.0, y=10.0))
    board.add_pad(Pad(name="1", net="A", component="C80", x=10.0, y=10.0,
                      width=0.6, height=0.3))
    board.add_component(Component(designator="Q1", x=5.0, y=10.0))
    board.add_pad(Pad(name="1", net="A", component="Q1", x=5.0, y=10.0,
                      width=2.0, height=2.0))

    # 本体很大、中心离 C80 只有 2 mm，但明显不属于它
    bodies = [{"x1": 7.1, "y1": 6.0, "x2": 11.7, "y2": 14.0, "layer": "Mechanical1"}]
    attach_bodies(board, bodies)

    assert board.components["C80"].body_bbox is None, "小器件不该拿到大本体"


def test_body_vs_bbox_when_one_side_has_no_3d_body():
    """一方有 3D 本体、另一方没有（如 U9 与 C106）时，按 本体 vs 占位框 判。"""
    board = Board(name="mixed")
    board.add_component(Component(designator="U9", x=10.0, y=10.0,
                                  bbox=(8.0, 8.0, 12.0, 12.0)))
    board.add_component(Component(designator="C106", x=12.1, y=12.0,
                                  bbox=(11.6, 11.6, 12.6, 12.4)))
    board.components["U9"].body_bbox = (8.2, 8.2, 12.05, 12.05)
    board.ensure_net("A")
    # 焊盘相距很远（0.8 mm 以上），只有本体会碰到
    board.add_pad(Pad(name="1", net="A", component="U9", x=9.0, y=9.0,
                      width=0.5, height=0.5))
    board.add_pad(Pad(name="1", net="A", component="C106", x=12.1, y=12.0,
                      width=0.4, height=0.4))

    classifications = {
        "A": NetClassification(net="A", net_class=NetClass.SECONDARY, voltage=5.0)
    }
    violations = find_violations(board, classifications, ClearanceRules())
    hits = [v for v in violations if "本体" in v.rule_name]
    assert hits, "本体与占位框重叠时必须报出（不能退回只看焊盘）"


def test_mirror_body_swap_is_corrected():
    """正反面镜像、几乎重叠的两颗器件被贪心对调本体时，按组内偏移共识纠正。"""
    from clearance_check.eda.session import _fix_body_offset_outliers

    board = Board(name="mirror")
    xs = (10.0, 20.0, 30.0)
    for i, x in enumerate(xs):
        board.add_component(Component(designator=f"QB{i}", x=x, y=10.0,
                                      layer="BOTTOM", rotation=270.0,
                                      footprint="TO252", bbox=(x - 2, 8, x + 2, 12)))
        board.add_component(Component(designator=f"QT{i}", x=x, y=10.025,
                                      layer="TOP", rotation=90.0,
                                      footprint="TO252", bbox=(x - 2, 8, x + 2, 12)))

    body_list = []
    for x in xs:
        body_list.append({"x1": x - 1.6, "y1": 9.88 - 1.6, "x2": x + 1.6, "y2": 9.88 + 1.6})
        body_list.append({"x1": x - 1.6, "y1": 10.145 - 1.6,
                          "x2": x + 1.6, "y2": 10.145 + 1.6})

    assigned = {}
    for i, x in enumerate(xs):
        assigned[f"QB{i}"] = (0.0, (x - 1.6, 9.88 - 1.6, x + 1.6, 9.88 + 1.6))
        assigned[f"QT{i}"] = (0.0, (x - 1.6, 10.145 - 1.6, x + 1.6, 10.145 + 1.6))
    # 制造一次镜像对调（模拟贪心把两颗的本体互换）
    assigned["QB1"], assigned["QT1"] = assigned["QT1"], assigned["QB1"]

    fixed = _fix_body_offset_outliers(board, assigned, body_list)
    assert fixed >= 1

    for i in range(3):
        qb, qt = board.components[f"QB{i}"], board.components[f"QT{i}"]
        box_b = assigned[f"QB{i}"][1]
        box_t = assigned[f"QT{i}"][1]
        cy_b = (box_b[1] + box_b[3]) / 2
        cy_t = (box_t[1] + box_t[3]) / 2
        assert abs(cy_b - qb.y + 0.12) < 1e-6, (i, cy_b, qb.y)
        assert abs(cy_t - qt.y - 0.12) < 1e-6, (i, cy_t, qt.y)


def test_symmetric_body_shared_by_mirror_pair():
    """对称封装的正反面镜像件共用同一个 3D 本体框（去重后只剩一个也要共享）。"""
    from clearance_check.eda.session import _share_symmetric_bodies

    board = Board(name="share")
    for d, layer in (("U15", "BOTTOM"), ("U16", "TOP")):
        board.add_component(Component(designator=d, x=10.0, y=10.0, layer=layer,
                                      footprint="WSON8", rotation=180.0,
                                      bbox=(8.5, 8.5, 11.5, 11.5)))
    box = (9.0, 9.0, 11.0, 11.0)
    assigned = {"U15": (0.0, box)}
    added = _share_symmetric_bodies(board, assigned)
    assert added == 1
    assert assigned["U16"][1] == box


def test_symmetric_share_requires_same_footprint():
    """同位置但不同封装的小件，不该继承大器件的本体。"""
    from clearance_check.eda.session import _share_symmetric_bodies

    board = Board(name="noshar")
    board.add_component(Component(designator="U15", x=10.0, y=10.0, layer="BOTTOM",
                                  footprint="WSON8", rotation=180.0,
                                  bbox=(8.5, 8.5, 11.5, 11.5)))
    board.add_component(Component(designator="C9", x=10.0, y=10.0, layer="TOP",
                                  footprint="C0402", rotation=0.0,
                                  bbox=(9.5, 9.5, 10.5, 10.5)))
    box = (9.0, 9.0, 11.0, 11.0)
    assigned = {"U15": (0.0, box)}
    _share_symmetric_bodies(board, assigned)
    assert "C9" not in assigned
