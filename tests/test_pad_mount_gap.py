"""器件焊盘到安装孔的最小间距规则（默认 0.4 mm）。"""

from __future__ import annotations

import pytest

from clearance_check.analysis import find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import Board, MountPoint, NetClass, NetClassification, Pad


def _scene(mount_x: float = 11.1, pad_class=NetClass.SECONDARY,
           mount_class=NetClass.SECONDARY):
    """焊盘右沿 10.5 mm；安装孔 width=0.5 → 左沿 mount_x-0.25 mm。

    默认 mount_x=11.1 → 间隙 0.35 mm：大于器件间 0.3，小于安装孔 0.4。
    """
    board = Board(name="mount-gap", outline=[(0, 0), (100, 0), (100, 50), (0, 50)])
    board.ensure_net("NET_A")
    board.add_pad(Pad(name="1", net="NET_A", component="R1", x=10.0, y=25.0,
                      width=1.0, height=1.0))
    mount = MountPoint(name="MH1", x=mount_x, y=25.0, width=0.5, height=0.5,
                       hole=0.2, net_class=mount_class, voltage=5.0)
    board.mounts.append(mount)
    classifications = {
        "NET_A": NetClassification(net="NET_A", net_class=pad_class, voltage=5.0)
    }
    return board, classifications


def test_pad_too_close_to_mount():
    board, classifications = _scene()
    violations = find_violations(board, classifications, ClearanceRules(),
                                 mounts=board.mounts)
    hits = [v for v in violations if "MH1" in (v.item_a, v.item_b)]
    assert hits
    assert hits[0].required_mm == pytest.approx(0.4)
    assert hits[0].actual_mm == pytest.approx(0.35, abs=0.02)
    assert "安装孔" in hits[0].rule_name
    assert hits[0].severity == "error"


def test_pad_far_enough_from_mount():
    # mount_x=12.0 → 间隙 1.25 mm，满足 0.4 mm
    board, classifications = _scene(mount_x=12.0)
    violations = find_violations(board, classifications, ClearanceRules(),
                                 mounts=board.mounts)
    assert not [v for v in violations if "MH1" in (v.item_a, v.item_b)]


def test_rule_can_be_disabled():
    board, classifications = _scene()
    rules = ClearanceRules(pad_mount_gap_enabled=False)
    violations = find_violations(board, classifications, rules, mounts=board.mounts)
    # 0.35 mm 满足器件间 0.3 mm，禁用安装孔规则后不应再报
    assert not [v for v in violations if "MH1" in (v.item_a, v.item_b)]


def test_stricter_isolation_rule_not_lowered():
    """原边焊盘 ↔ 副边安装孔仍按隔离带判，新规则不会把要求降到 0.4 mm。"""
    board, classifications = _scene(mount_x=11.1, pad_class=NetClass.PRIMARY_CONST,
                                    mount_class=NetClass.SECONDARY)
    violations = find_violations(board, classifications, ClearanceRules(),
                                 mounts=board.mounts)
    hits = [v for v in violations if "MH1" in (v.item_a, v.item_b)]
    assert hits
    assert max(v.required_mm for v in hits) >= 8.0


def test_rules_roundtrip_keeps_pad_mount_gap():
    rules = ClearanceRules(pad_mount_gap_mm=0.65, pad_mount_gap_enabled=False)
    data = rules.to_dict()
    assert data["pad_mount_gap_mm"] == pytest.approx(0.65)
    restored = ClearanceRules.from_dict(data)
    assert restored.pad_mount_gap_mm == pytest.approx(0.65)
    assert restored.pad_mount_gap_enabled is False


def test_run_clearance_rejects_unassigned_mount():
    """未指派网络的安装孔在校验入口直接报错，不进入避距计算。"""
    from api import result_service
    from api.scan_service import ServiceError
    from api.store import STATE
    from clearance_check.altium.demo import build_demo_board
    from clearance_check.analysis import classify_board

    board, pins = build_demo_board()
    classes = classify_board(board, pins)
    board.mounts[0].net = ""

    with STATE.lock:
        saved = (STATE.board, STATE.classifications, STATE.rules)
        STATE.board = board
        STATE.classifications = classes
        STATE.rules = ClearanceRules()
    try:
        with pytest.raises(ServiceError) as exc:
            result_service.run_clearance()
        assert board.mounts[0].name in str(exc.value)
    finally:
        with STATE.lock:
            STATE.board, STATE.classifications, STATE.rules = saved
