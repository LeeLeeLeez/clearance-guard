"""真实工程扫描快照的回归测试（无需连接 Altium）。

快照放在 ``json/`` 目录下即可自动参与测试；没有快照时整组跳过。
这些断言只检查结构与关键推断，不绑定具体器件数量，换工程或重新扫描后仍可复用。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clearance_check.analysis import find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import NetClass
from clearance_check.snapshot import load_snapshot

SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "json"
CANDIDATES = sorted(SNAPSHOT_DIR.glob("*.json")) if SNAPSHOT_DIR.is_dir() else []

pytestmark = pytest.mark.skipif(not CANDIDATES, reason="json/ 下没有扫描快照")


@pytest.fixture(scope="module")
def snapshot():
    return load_snapshot(CANDIDATES[0])


def test_snapshot_is_complete(snapshot):
    assert snapshot.board.components
    assert snapshot.board.pads
    assert snapshot.board.nets
    assert snapshot.classifications
    assert snapshot.created_text


def test_every_pad_net_is_classified(snapshot):
    known = set(snapshot.classifications)
    missing = {p.net for p in snapshot.board.pads if p.net and p.net not in known}
    assert not missing, f"{len(missing)} 个焊盘网络没有分类结果，例如 {sorted(missing)[:5]}"


def test_sheet_index_matches_project(snapshot):
    index = snapshot.sheet_index
    assert index is not None and index.sheets
    # 每个网络至少归属一张图纸（未归属图纸单独一栏）
    assert index.net_count > 0
    assert len(index.shared_nets()) > 0


def test_clearance_runs_on_real_board(snapshot):
    violations = find_violations(
        snapshot.board,
        snapshot.classifications,
        ClearanceRules(),
        mounts=snapshot.board.mounts,
    )
    assert isinstance(violations, list)
    for violation in violations:
        assert violation.required_mm > 0
        assert violation.deficit_mm > 0
        assert violation.actual_mm <= violation.required_mm + 1e-6


def test_drive_nets_have_reference_ground(snapshot):
    drives = [item for item in snapshot.classifications.values() if item.is_drive]
    if not drives:
        pytest.skip("该工程没有识别到驱动网络")
    without = [item.net for item in drives if not item.ref_ground]
    assert len(without) <= max(2, len(drives) // 10), f"过多驱动网络没有参考地: {without[:5]}"

    # 参考地必须是一个真实存在的网络
    for item in drives:
        if item.ref_ground:
            assert item.ref_ground in snapshot.classifications


def test_voltage_is_parsed_from_names(snapshot):
    checked = 0
    for net, item in snapshot.classifications.items():
        upper = net.upper()
        if "18VP" in upper or upper.startswith("12VS"):
            assert item.voltage is not None, f"{net} 没有从名称中解析出电压"
            checked += 1
    if checked == 0:
        pytest.skip("该工程没有 18Vp/12Vs 这类驱动供电网络")


def test_mounts_are_detected(snapshot):
    # 有安装孔就该被识别；没有也不失败
    for mount in snapshot.board.mounts:
        assert mount.width > 0 and mount.height > 0
        assert mount.net_class in NetClass
