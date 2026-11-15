"""几何计算测试。"""

from __future__ import annotations

import math

import pytest

from clearance_check.geometry import (
    capsule_polygon,
    pad_polygon,
    point_in_polygon,
    polygon_distance,
)
from clearance_check.model import Pad


def _square(cx: float, cy: float, size: float):
    return [
        (cx - size / 2, cy - size / 2),
        (cx + size / 2, cy - size / 2),
        (cx + size / 2, cy + size / 2),
        (cx - size / 2, cy + size / 2),
    ]


def test_polygon_distance_side_by_side():
    left = _square(0.0, 0.0, 1.0)
    right = _square(1.5, 0.0, 1.0)
    assert polygon_distance(left, right) == pytest.approx(0.5)


def test_polygon_distance_diagonal():
    a = _square(0.0, 0.0, 1.0)
    b = _square(2.0, 2.0, 1.0)
    # 两角点相距 (1.0, 1.0)
    assert polygon_distance(a, b) == pytest.approx(math.sqrt(2), rel=1e-6)


def test_polygon_overlap_is_zero():
    a = _square(0.0, 0.0, 2.0)
    b = _square(0.5, 0.5, 2.0)
    assert polygon_distance(a, b) == 0.0


def test_polygon_containment_is_zero():
    outer = _square(0.0, 0.0, 4.0)
    inner = _square(0.0, 0.0, 1.0)
    assert point_in_polygon((0.0, 0.0), outer)
    assert polygon_distance(outer, inner) == 0.0


def test_pad_polygon_dimensions():
    pad = Pad(name="1", x=10.0, y=20.0, width=2.0, height=1.0, rotation=0.0)
    xs = [p[0] for p in pad_polygon(pad)]
    ys = [p[1] for p in pad_polygon(pad)]
    assert min(xs) == pytest.approx(9.0)
    assert max(xs) == pytest.approx(11.0)
    assert min(ys) == pytest.approx(19.5)
    assert max(ys) == pytest.approx(20.5)


def test_pad_rotation_swaps_extents():
    pad = Pad(name="1", x=0.0, y=0.0, width=4.0, height=1.0, rotation=90.0)
    xs = [p[0] for p in pad_polygon(pad)]
    ys = [p[1] for p in pad_polygon(pad)]
    assert (max(xs) - min(xs)) == pytest.approx(1.0, rel=1e-6)
    assert (max(ys) - min(ys)) == pytest.approx(4.0, rel=1e-6)


def test_capsule_distance():
    a = capsule_polygon(0.0, 0.0, 10.0, 0.0, 1.0)
    b = capsule_polygon(0.0, 2.0, 10.0, 2.0, 1.0)
    assert polygon_distance(a, b) == pytest.approx(1.0, rel=1e-3)
