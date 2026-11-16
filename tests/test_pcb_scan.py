"""仅扫描 PCB：启用条件与增量分类。"""

from __future__ import annotations

import copy
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from clearance_check.model import Board, NetClass, NetClassification, Pad  # noqa: E402
from clearance_check.ui.main_window import MainWindow  # noqa: E402


class _FakeSession:
    connected = True

    def disconnect(self):
        pass


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _board(tmp_path, nets: list[str]) -> Board:
    pcb = tmp_path / "demo.PcbDoc"
    pcb.write_bytes(b"x")
    board = Board(name="pcb", source_path=str(pcb))
    for index, net in enumerate(nets):
        board.ensure_net(net)
        board.add_pad(
            Pad(name="1", net=net, component=f"R{index}", x=float(index) * 5.0, y=0.0,
                width=1.0, height=1.0)
        )
    return board


def test_button_enabled_once_scanned(app, tmp_path):
    """扫描过一次后即可随时点「仅扫描PCB」（mtime 只影响提示，不作为门槛）。"""
    board = _board(tmp_path, ["A"])
    window = MainWindow()
    window.session = _FakeSession()

    window.board = None
    window._update_pcb_button_state()
    assert not window.btn_scan_pcb.isEnabled()

    window.board = board
    window._last_pcb_mtime = window._pcb_mtime()
    window._update_pcb_button_state()
    assert window.btn_scan_pcb.isEnabled()

    # PCB 文件没变也照样能点
    window._update_pcb_button_state()
    assert window.btn_scan_pcb.isEnabled()
    window.close()


def test_pcb_scan_keeps_existing_and_classifies_new_nets(app, tmp_path):
    board = _board(tmp_path, ["A", "B"])
    window = MainWindow()
    window.session = _FakeSession()
    window.board = board
    window.classifications = {
        "A": NetClassification(net="A", net_class=NetClass.SECONDARY, voltage=5.0)
    }

    fresh = copy.deepcopy(board)
    fresh.ensure_net("C")
    fresh.add_pad(Pad(name="1", net="C", component="R9", x=30.0, y=10.0,
                      width=1.0, height=1.0))
    report = types.SimpleNamespace(
        mount_pads=[], floating_parts=[], cutouts=[], component_count=2,
        pad_count=3, net_count=3, steps=[], warnings=[],
    )
    window._on_pcb_scanned((fresh, report, 0.5))

    assert window.classifications["A"].net_class is NetClass.SECONDARY
    assert "A" in window.classifications and "B" in window.classifications
    window.close()


def test_pcb_scan_task_skips_schematic_stages(app, tmp_path):
    """扫描pcb 必须跳过网表 / 图纸 / 挖空端点三个阶段。"""
    board = _board(tmp_path, ["A"])
    window = MainWindow()
    captured = {}

    class _Session:
        connected = True

        def disconnect(self):
            pass

        def scan(self, **kwargs):
            captured.update(kwargs)
            return board, types.SimpleNamespace()

    window.session = _Session()
    window.board = board
    window._scan_pcb_task("proj.PrjPcb", lambda *a: None, lambda: False)

    assert captured["with_netlist"] is False
    assert captured["with_sheets"] is False
    assert captured["with_cutout_outlines"] is False
    window.close()


def test_pcb_scan_does_not_run_clearance(app, tmp_path):
    """扫描pcb 后只刷新布局视图，不自动重算违规。"""
    board = _board(tmp_path, ["A", "B"])
    window = MainWindow()
    window.session = _FakeSession()
    window.board = board
    window.classifications = {
        "A": NetClassification(net="A", net_class=NetClass.SECONDARY, voltage=5.0)
    }

    calls = []
    window._start_clearance = lambda: calls.append("clearance")

    report = types.SimpleNamespace(
        mount_pads=[], floating_parts=[], cutouts=[], component_count=2,
        pad_count=2, net_count=2, steps=[], warnings=[],
    )
    window._on_pcb_scanned((copy.deepcopy(board), report, 0.3))

    assert calls == []
    assert "重新校验" in window.status_label.text() or "PCB 已更新" in window.status_label.text()
    window.close()
