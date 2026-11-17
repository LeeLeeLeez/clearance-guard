"""扫描框局部刷新：只更新框内器件，框外沿用整板基线。"""

from __future__ import annotations

import copy

import api.scan_service as ss
from clearance_check.model import Board, Component, Pad


def _base() -> Board:
    board = Board(name="local")
    board.outline = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    board.add_component(Component(designator="C1", x=10.0, y=10.0, bbox=(9, 9, 11, 11)))
    board.add_component(Component(designator="C2", x=90.0, y=90.0, bbox=(89, 89, 91, 91)))
    # 本体压框、但原点在查询范围外（模拟坐标过滤漏掉），不应被误删
    board.add_component(Component(designator="C4", x=60.0, y=60.0, bbox=(48, 48, 52, 52)))
    board.add_pad(Pad(name="1", net="A", component="C1", x=10.0, y=10.0,
                      width=1.0, height=1.0))
    board.add_pad(Pad(name="1", net="B", component="C2", x=90.0, y=90.0,
                      width=1.0, height=1.0))
    return board


class _FakeSession:
    def export_coordinates(self):
        def row(d, x, y):
            return {"designator": d, "x": x / 0.0254, "y": y / 0.0254,
                    "rotation": 0.0, "layer": "TopLayer"}

        return [row("C1", 20.0, 20.0), row("C2", 90.0, 90.0), row("C3", 30.0, 30.0)]

    def component_pads(self, designators):
        out = {}
        for d in designators:
            x = {"C1": 20.0, "C3": 30.0}.get(d)
            if x is None:
                continue
            out[d] = [{
                "name": "1", "net": "A" if d == "C1" else "C",
                "x": x / 0.0254, "y": x / 0.0254,
                "top_x_size": 0.5 / 0.0254, "top_y_size": 0.5 / 0.0254,
                "rotation": 0.0, "layer": "TopLayer",
                "hole_size": 0.0, "is_smd": True,
            }]
        return out


def test_local_refresh_updates_only_box(monkeypatch):
    monkeypatch.setattr(ss, "session", lambda: _FakeSession())
    from api import result_service

    monkeypatch.setattr(
        result_service,
        "run_clearance",
        lambda: {"components": {"total": 7, "errors": 3}},
    )
    saved = (ss.STATE.board, ss.STATE.base_board, ss.STATE.task)
    try:
        ss.STATE.board = copy.deepcopy(_base())
        ss.STATE.base_board = copy.deepcopy(_base())
        ss.STATE.task = ss.TaskState(running=True, kind="scan_pcb")

        ss._refresh_region_job("", (5.0, 5.0, 50.0, 50.0))

        task = ss.STATE.task
        assert task.finished and not task.error, task.error
        board = ss.STATE.board
        assert "C1" in board.components
        assert "C3" in board.components
        assert abs(board.components["C1"].x - 20.0) < 1e-6
        assert abs(board.components["C3"].x - 30.0) < 1e-6
        # 框外器件仍保留（整板照常显示），位置沿用基线
        assert "C2" in board.components
        assert abs(board.components["C2"].x - 90.0) < 1e-6
        # 原点在查询范围外、但本体压框的器件要保留
        assert "C4" in board.components

        pads = [p for p in board.pads if p.component == "C1"]
        assert pads and abs(pads[0].x - 20.0) < 1e-6
        assert pads[0].net == "A"

        # 基线保留框外器件，供下次局部刷新使用
        assert "C2" in ss.STATE.base_board.components

        # 局部刷新后自动重新校验
        assert "已自动校验：器件级 7（ERROR 3）" in task.detail
    finally:
        ss.STATE.board, ss.STATE.base_board, ss.STATE.task = saved
