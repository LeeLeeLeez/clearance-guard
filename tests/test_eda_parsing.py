"""eda-agent 响应解析的单元测试（不连接 Altium）。"""

from __future__ import annotations

import pytest

from clearance_check.eda.session import _bulk_items, _extract_pads, _map_layer, mm


def test_mils_to_mm():
    assert mm(100) == pytest.approx(2.54)
    assert mm("1000") == pytest.approx(25.4)
    assert mm(None) == 0.0
    assert mm(None, 1.5) == 1.5


def test_map_layer():
    assert _map_layer("TopLayer") == "TOP"
    assert _map_layer("BottomLayer") == "BOTTOM"
    assert _map_layer("MultiLayer") == "MULTI"
    assert _map_layer("") == "TOP"


def test_bulk_items_reads_items_key():
    payload = {"bulk": True, "count": 2, "items": [{"designator": "R1"}, {"designator": "R2"}]}
    assert [item["designator"] for item in _bulk_items(payload)] == ["R1", "R2"]


def test_bulk_items_single_object():
    payload = {"designator": "R1", "pads": []}
    assert _bulk_items(payload) == [payload]


def test_extract_pads():
    entry = {"designator": "R1", "pads": [{"name": "1", "x": 10}], "pad_count": 1}
    pads = _extract_pads(entry)
    assert len(pads) == 1
    assert pads[0]["name"] == "1"


def test_extract_pads_from_direct_pad():
    entry = {"name": "1", "x": 10.0, "top_x_size": 60.0}
    assert len(_extract_pads(entry)) == 1


def test_extract_pads_empty():
    assert _extract_pads({}) == []
    assert _extract_pads(None) == []
