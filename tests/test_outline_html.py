"""板框改为从 HTML 记录读取，并验证器件本体间距规则。"""

from __future__ import annotations

from pathlib import Path

import pytest

from clearance_check.analysis import find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import Board, Component, NetClass, NetClassification, Pad
from clearance_check.outline_html import load_outline_html, parse_svg_path

_HTML = """<!DOCTYPE html><html><body>
<svg viewBox="0 0 250 60">
<defs><pattern id="g"><path d="M10 0H0V10" fill="none"/></pattern></defs>
<path d="M 0 0 L 239.5 0 A 0.5 0.5 0 0 1 240 0.5 L 240 41 A 0.5 0.5 0 0 1 239.5 41.5
         L 0.5 41.5 A 0.5 0.5 0 0 1 0 41 Z"/>
</svg></body></html>
"""


def _write_html(tmp_path: Path) -> Path:
    target = tmp_path / "outline.html"
    target.write_text(_HTML, encoding="utf-8")
    return target


def test_parse_picks_board_path_not_grid(tmp_path):
    points = load_outline_html(_write_html(tmp_path))
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    assert max(xs) - min(xs) == pytest.approx(240.0, abs=0.01)
    assert max(ys) - min(ys) == pytest.approx(41.5, abs=0.01)
    assert len(points) > 20  # 圆角被离散成多段


def test_parse_svg_path_relative_and_close():
    points = parse_svg_path("M0 0 h10 v10 h-10 z")
    assert points[0] == (0.0, 0.0)
    assert (10.0, 10.0) in points


def _board_with_bodies(gap: float) -> tuple[Board, dict]:
    board = Board(name="body")
    board.add_component(Component(designator="U9", comment="ic", x=0.0, y=0.0,
                                  bbox=(0.0, 0.0, 5.0, 5.0)))
    board.add_component(Component(designator="C106", comment="cap", x=5.0 + gap, y=0.0,
                                  bbox=(5.0 + gap, 0.0, 8.0 + gap, 3.0)))
    board.ensure_net("A")
    board.add_pad(Pad(name="1", net="A", component="U9", x=50.0, y=50.0,
                      width=1.0, height=1.0))
    classifications = {
        "A": NetClassification(net="A", net_class=NetClass.SECONDARY, voltage=5.0)
    }
    return board, classifications


def test_body_interference_detected():
    board, classifications = _board_with_bodies(0.1)
    violations = find_violations(board, classifications, ClearanceRules())
    hits = [v for v in violations if "本体" in v.rule_name]
    assert hits, "本体间距 0.1 mm 应判违规"
    assert {hits[0].item_a, hits[0].item_b} == {"U9", "C106"}
    assert hits[0].required_mm == pytest.approx(0.3)


def test_body_gap_ok_when_far_enough():
    board, classifications = _board_with_bodies(0.5)
    violations = find_violations(board, classifications, ClearanceRules())
    assert not [v for v in violations if "本体" in v.rule_name]


def test_body_rule_can_be_disabled():
    board, classifications = _board_with_bodies(0.1)
    rules = ClearanceRules(body_gap_enabled=False)
    violations = find_violations(board, classifications, rules)
    assert not [v for v in violations if "本体" in v.rule_name]


def test_outline_is_translated_to_anchor_center(tmp_path):
    """HTML 坐标从 0,0 起画，导入后必须平移到焊盘所在位置。"""
    anchor = (100.0, 200.0, 340.0, 241.5)
    pads = [(120.0, 205.0), (200.0, 220.0), (330.0, 238.0)]
    points = load_outline_html(_write_html(tmp_path), anchor, pads)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    assert (min(xs) + max(xs)) / 2 == pytest.approx((anchor[0] + anchor[2]) / 2, abs=0.01)
    assert (min(ys) + max(ys)) / 2 == pytest.approx((anchor[1] + anchor[3]) / 2, abs=0.01)


def test_orientation_chosen_by_pads_inside():
    """三角形板框 + 只在下半部的焊盘：正方向应罩住焊盘，翻转后罩不住。"""
    from clearance_check.outline_html import orient_to_pads

    triangle = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    pads = [(8.0, 1.0), (9.0, 2.0), (8.5, 0.5)]
    anchor = (0.0, 0.0, 10.0, 10.0)
    aligned = orient_to_pads(triangle, anchor, pads)

    from clearance_check.geometry import point_in_polygon_any

    inside = sum(1 for x, y in pads if point_in_polygon_any((x, y), aligned))
    assert inside == len(pads)


def test_big_fillet_only_sweeps_quarter_circle():
    """R8 的大圆角只能扫 ~90°，不能被画成 270° 的大鼓包。"""
    from clearance_check.outline_html import parse_svg_path

    # 从 (223,19.5) 以 R8 走到 (215,27.5)：标准 90° 圆角
    data = "M 223 19.5 A 8 8 0 0 0 215 27.5"
    points = parse_svg_path(data, arc_step_deg=6.0)
    assert len(points) - 1 <= 18, f"采样段数 {len(points) - 1} 过多（疑似扫过 270°）"

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    # 90° 圆弧落在 223×19.5 - 215×27.5 这个 8×8 的方框内
    assert max(xs) <= 223.01 and min(ys) >= 19.49
    assert min(xs) >= 215.0 - 0.01 and max(ys) <= 27.51


def test_outline_version_roundtrip(tmp_path):
    """板框解析版本要随快照保存，才能识别「旧算法画的板框」。"""
    from clearance_check.model import Board, NetClassification
    from clearance_check.snapshot import load_snapshot, save_snapshot

    board = Board(name="v")
    board.outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0)]
    board.outline_version = 2

    target = tmp_path / "snap.json"
    save_snapshot(
        target,
        board=board,
        classifications={},
        pins=[],
        sheet_components={},
        sheet_paths={},
        instance_sheets={},
        top_sheet="",
    )
    loaded = load_snapshot(target, reclassify=False)
    assert loaded.board.outline_version == 2
    assert len(loaded.board.outline) == 3


def test_stale_outline_without_source_keeps_shape(tmp_path, monkeypatch):
    """没有记录来源 HTML 时，旧板框保持原样，只在日志里提示。"""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from clearance_check.model import Board
    from clearance_check.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    monkeypatch.setattr(window, "_saved_outline_source", lambda: "")
    board = Board(name="s")
    board.outline = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    board.outline_version = 0

    window._refresh_stale_outline(board)
    assert board.outline_version == 0
    assert len(board.outline) == 3
    window.close()
