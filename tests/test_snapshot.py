"""扫描快照导出 / 导入测试。"""

from __future__ import annotations

import json

import pytest

from clearance_check.altium.demo import build_demo_board, build_demo_sheets
from clearance_check.analysis import classify_board, find_violations
from clearance_check.analysis.rules import ClearanceRules
from clearance_check.model import NetClass
from clearance_check.snapshot import (
    SNAPSHOT_FORMAT,
    SnapshotError,
    load_snapshot,
    save_snapshot,
)


@pytest.fixture()
def snapshot_file(tmp_path):
    board, pins = build_demo_board()
    classifications = classify_board(board, pins)
    sheets, paths = build_demo_sheets()

    # 模拟人工复核：确认两个网络、补一个电压、指定一个安装焊盘电位
    classifications["GATE"].confirmed = True
    classifications["GATE"].user_edited = True
    classifications["HV_SENSE"].net_class = NetClass.PRIMARY_CONST
    classifications["HV_SENSE"].voltage = 375.0
    classifications["HV_SENSE"].voltage_min = 375.0
    classifications["HV_SENSE"].voltage_max = 375.0
    board.mounts[0].net_class = NetClass.SECONDARY
    board.mounts[0].voltage = 12.0

    path = tmp_path / "demo_scan.json"
    save_snapshot(
        path,
        board=board,
        classifications=classifications,
        pins=pins,
        sheet_components=sheets,
        sheet_paths=paths,
        instance_sheets={},
        top_sheet="VPDB_main",
        project_path="(demo)",
        app_version="test",
    )
    return path, board, classifications


def test_snapshot_roundtrip_board(snapshot_file):
    path, board, _classifications = snapshot_file
    snapshot = load_snapshot(path)

    assert len(snapshot.board.components) == len(board.components)
    assert len(snapshot.board.pads) == len(board.pads)
    assert snapshot.board.nets == board.nets
    assert len(snapshot.board.mounts) == 2


def test_snapshot_keeps_manual_edits(snapshot_file):
    path, _board, _classifications = snapshot_file
    snapshot = load_snapshot(path)

    assert snapshot.classifications["GATE"].confirmed is True
    assert snapshot.classifications["GATE"].user_edited is True
    assert snapshot.classifications["HV_SENSE"].voltage == pytest.approx(375.0)
    assert snapshot.classifications["GATE"].ref_ground == "PRI_GND"
    assert snapshot.board.mounts[0].net_class is NetClass.SECONDARY
    assert snapshot.board.mounts[0].voltage == pytest.approx(12.0)


def test_snapshot_rebuilds_sheets(snapshot_file):
    path, _board, _classifications = snapshot_file
    snapshot = load_snapshot(path)

    assert snapshot.sheet_index is not None
    assert "VPDB_main" in snapshot.sheet_index.sheets
    assert "PRI_GND" in snapshot.sheet_index.shared_nets()


def test_snapshot_clearance_matches(snapshot_file):
    path, board, classifications = snapshot_file
    snapshot = load_snapshot(path)

    original = find_violations(board, classifications, ClearanceRules())
    restored = find_violations(
        snapshot.board, snapshot.classifications, ClearanceRules(), mounts=snapshot.board.mounts
    )
    assert len(restored) == len(original)
    assert {v.required_mm for v in restored} == {v.required_mm for v in original}


def test_snapshot_metadata(snapshot_file):
    path, _board, _classifications = snapshot_file
    snapshot = load_snapshot(path)
    assert snapshot.created_text
    assert snapshot.project_path == "(demo)"
    assert snapshot.app_version == "test"
    assert snapshot.reclassified is False


def test_old_classifier_snapshot_is_reclassified(tmp_path, snapshot_file):
    """判定规则升级后，旧快照导入时应自动重算，而不是沿用旧结论。"""
    from clearance_check.analysis.classify import CLASSIFIER_VERSION

    path, _board, _classifications = snapshot_file
    data = json.loads(path.read_text(encoding="utf-8"))
    data["classifier"] = CLASSIFIER_VERSION - 1
    for item in data["classifications"]:
        if item["net"] == "GATE":
            item["net_class"] = "UNKNOWN"
            item["voltage"] = None
            item["voltage_min"] = None
            item["voltage_max"] = None

    old = tmp_path / "old_rule.json"
    old.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    snapshot = load_snapshot(old)
    assert snapshot.reclassified is True
    assert snapshot.classifications["GATE"].net_class is not NetClass.UNKNOWN
    assert snapshot.classifications["GATE"].ref_ground == "PRI_GND"


def test_reject_foreign_json(tmp_path):
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    with pytest.raises(SnapshotError):
        load_snapshot(path)


def test_reject_future_version(tmp_path, snapshot_file):
    path, _board, _classifications = snapshot_file
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["format"] == SNAPSHOT_FORMAT
    data["version"] = 999
    broken = tmp_path / "future.json"
    broken.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SnapshotError):
        load_snapshot(broken)


def test_missing_file():
    with pytest.raises(SnapshotError):
        load_snapshot("Z:/definitely/not/here.json")
