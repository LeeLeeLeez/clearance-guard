"""结构回归：EdaAgentSession 的关键方法不能被写坏成嵌套函数。"""

from __future__ import annotations

import inspect

from clearance_check.eda import session as session_module
from clearance_check.eda.session import EdaAgentSession, discretize_outline


def test_key_methods_exist():
    for name in (
        "scan",
        "board_cutouts",
        "cutout_outlines",
        "sheet_layout",
        "mount_pads",
        "components",
    ):
        assert hasattr(EdaAgentSession, name), f"EdaAgentSession 缺少 {name}"


def test_scan_signature_flags():
    params = inspect.signature(EdaAgentSession.scan).parameters
    for flag in ("with_netlist", "with_sheets", "with_cutout_outlines"):
        assert flag in params, f"scan 缺少参数 {flag}"


def test_outline_no_longer_reads_from_bridge():
    """板框已改为 HTML 导入，类里不应再有桥接读板框的方法。"""
    assert not hasattr(EdaAgentSession, "board_outline")
    assert callable(discretize_outline)
    assert callable(session_module._pick_sweep)
