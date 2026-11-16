"""板框圆弧离散：90° 圆角不能被画成 270°。"""

from __future__ import annotations

import math

from clearance_check.eda.session import _ad_span, discretize_outline


def _arc_vertices() -> list[dict]:
    """10×10 方板，右下角改成 R2 圆角（AD 角度顺序与顶点顺序相反）。"""
    radius = 2.0
    cx, cy = 8.0, 2.0
    return [
        {"kind": "line", "x": 0.0, "y": 0.0},
        {"kind": "line", "x": 10.0, "y": 0.0},
        {"kind": "line", "x": 10.0, "y": 10.0},
        {"kind": "line", "x": 0.0, "y": 10.0},
        # 弧：从 (8,0) 顺时针到 (10,2)，圆心 (8,2)
        {
            "kind": "arc", "x": cx, "y": 0.0, "cx": cx, "cy": cy,
            "angle1": 90.0, "angle2": 0.0,   # AD 里起点/终点顺序可能是反的
        },
    ]


def test_ad_span_is_shortest_angle():
    assert _ad_span({"angle1": 270.0, "angle2": 90.0}) == 180.0
    assert _ad_span({"angle1": 90.0, "angle2": 0.0}) == 90.0


def test_fillet_uses_quarter_arc():
    outline = discretize_outline(_arc_vertices(), arc_step_deg=6.0)
    # 圆角只有 90°，采样点应在 (6,0)~(10,4) 附近，不会绕到板中心
    arc_points = [p for p in outline if 8.0 < p[0] < 10.5 and 0.0 <= p[1] < 4.5]
    assert len(arc_points) >= 10, "90° 圆角应产生约 16 个采样点"
    assert len(arc_points) < 30, "不应把圆角画成 270°"
    for x, y in outline:
        assert -0.01 <= x <= 10.01 and -0.01 <= y <= 10.01


def test_arc_points_stay_on_circle():
    outline = discretize_outline(_arc_vertices(), arc_step_deg=6.0)
    for x, y in outline:
        if x > 8.0 - 1e-6 and y < 2.0 + 1e-6 and (x - 8.0) ** 2 + (y - 2.0) ** 2 < 3.9:
            assert math.isclose(math.hypot(x - 8.0, y - 2.0), 2.0, abs_tol=1e-6)
