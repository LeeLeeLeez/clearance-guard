"""无网络器件（磁芯 / 结构件）的电位指定与避距参与。"""

from __future__ import annotations

import pytest

from clearance_check.analysis import find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import (
    Board,
    Component,
    FloatingPart,
    NetClass,
    NetClassification,
    Pad,
)
from clearance_check.snapshot import load_snapshot, save_snapshot


def _board() -> tuple[Board, dict[str, NetClassification]]:
    board = Board(name="floating")
    board.ensure_net("HV")
    board.add_component(Component(designator="Q1", comment="SiC", footprint="TO247"))
    board.add_pad(Pad(name="D", net="HV", component="Q1", x=0.0, y=0.0, width=1.0, height=1.0))
    board.add_component(Component(designator="T38", comment="aux_core", footprint="aux_core"))
    classifications = {
        "HV": NetClassification(
            net="HV", net_class=NetClass.PRIMARY_CONST, voltage=800.0,
            voltage_min=800.0, voltage_max=800.0,
        )
    }
    return board, classifications


def test_unassigned_floating_part_is_ignored():
    board, classifications = _board()
    part = FloatingPart(designator="T38", x=1.2, y=0.0, width=2.0, height=2.0)
    violations = find_violations(board, classifications, ClearanceRules(), parts=[part])
    assert not any("T38" in (v.item_a, v.item_b) for v in violations)


def test_assigned_floating_part_participates():
    board, classifications = _board()
    part = FloatingPart(
        designator="T38",
        x=1.2,
        y=0.0,
        width=2.0,
        height=2.0,
        net_class=NetClass.PRIMARY_CONST,
        voltage=800.0,
    )
    violations = find_violations(board, classifications, ClearanceRules(), parts=[part])

    hits = [v for v in violations if "T38" in (v.item_a, v.item_b)]
    assert hits
    assert hits[0].severity == "error"
    assert hits[0].required_mm == pytest.approx(0.3)
    assert "T38" in (hits[0].net_a, hits[0].net_b)


def test_floating_part_survives_snapshot(tmp_path):
    board, classifications = _board()
    board.floating_parts = [
        FloatingPart(
            designator="T38",
            comment="aux_core",
            x=1.2,
            y=0.0,
            net_class=NetClass.PRIMARY_CONST,
            voltage=800.0,
            voltage_min=800.0,
            voltage_max=800.0,
        )
    ]
    path = tmp_path / "floating.json"
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
    parts = snapshot.board.floating_parts
    assert len(parts) == 1
    assert parts[0].designator == "T38"
    assert parts[0].net_class is NetClass.PRIMARY_CONST
    assert parts[0].voltage == pytest.approx(800.0)
    assert parts[0].assigned


def test_component_bbox_parsed_from_bridge():
    """器件外形来自 PCB 桥接的 bbox（mils），优先用去掉位号/注释文字的 body 框。"""
    from clearance_check.eda.session import _component_bbox

    item = {
        "bbox": {"x1": 0, "y1": 0, "x2": 1000, "y2": 800, "width": 1000, "height": 800},
        "bbox_no_name_comment": {
            "x1": 4570.6, "y1": 8740.7, "x2": 4979.1, "y2": 9026.4
        },
    }
    bbox = _component_bbox(item)
    assert bbox is not None
    x1, y1, x2, y2 = bbox
    assert x2 - x1 == pytest.approx(408.5 * 0.0254, rel=1e-3)
    assert y2 - y1 == pytest.approx(285.7 * 0.0254, rel=1e-3)
    assert _component_bbox({}) is None


def test_floating_part_without_size_is_ignored():
    """桥接没读到外形时（宽高为 0）不参与避距，避免用默认尺寸产生假违规。"""
    board, classifications = _board()
    part = FloatingPart(
        designator="T38",
        x=1.2,
        y=0.0,
        width=0.0,
        height=0.0,
        net_class=NetClass.PRIMARY_CONST,
        voltage=800.0,
    )
    violations = find_violations(board, classifications, ClearanceRules(), parts=[part])
    assert not any("T38" in (v.item_a, v.item_b) for v in violations)
