"""离线渲染界面截图，用于检查布局与样式。"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from clearance_check.analysis import (  # noqa: E402
    aggregate_by_component,
    apply_reference_potential,
    find_violations,
)
from clearance_check.model import NetClass  # noqa: E402
from clearance_check.snapshot import load_snapshot  # noqa: E402
from clearance_check.ui.main_window import MainWindow  # noqa: E402
from clearance_check.ui.theme import apply_theme  # noqa: E402

OUT = os.path.join(ROOT, "dev")
SNAPSHOT = os.path.join(ROOT, "json", "VPDB_A4_scan.json")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow()
    window.resize(1480, 940)
    window.show()

    if os.path.isfile(SNAPSHOT):
        window._apply_snapshot(load_snapshot(SNAPSHOT), SNAPSHOT)
        # 用当前算法重新分类（等价于重新扫描一次），再演示参考地电压联动
        from clearance_check.analysis import classify_board

        window.classifications = classify_board(window.board, window.pins)
        window.net_panel.set_items(list(window.classifications.values()))
        window.sheet_panel.set_data(
            window.board, window.classifications, window.sheet_index, window.pins
        )
        window.sheet_panel._apply_cell_edit("H1-GND", 3, "400")
        window.sheet_panel._apply_cell_edit("H1-GND", 4, "800")
        for mount in window.board.mounts[:2]:
            mount.net_class = NetClass.SECONDARY if mount is window.board.mounts[0] else NetClass.PRIMARY_CONST
            mount.voltage = 12.0 if mount is window.board.mounts[0] else 400.0
            mount.voltage_min = mount.voltage
            mount.voltage_max = mount.voltage
        window.mount_panel.set_mounts(window.board.mounts)
    else:
        window._load_demo()
        if len(window.board.mounts) >= 2:
            first, second = window.board.mounts[0], window.board.mounts[1]
            first.net_class, first.voltage = NetClass.SECONDARY, 12.0
            first.voltage_min, first.voltage_max = 12.0, 12.0
            second.net_class, second.voltage = NetClass.PRIMARY_CONST, 400.0
            second.voltage_min, second.voltage_max = 400.0, 400.0
            window.mount_panel.set_mounts(window.board.mounts)
    app.processEvents()

    # 演示无网络器件（如磁芯 T38）：扫描结果里没有时按板上实际情况补一份
    if window.board is not None and not window.board.floating_parts:
        from clearance_check.model import FloatingPart

        by_component: dict[str, list] = {}
        for pad in window.board.pads:
            if pad.component:
                by_component.setdefault(pad.component, []).append(pad)
        for designator, comp in window.board.components.items():
            pads = by_component.get(designator, [])
            if pads and any(pad.net for pad in pads):
                continue
            center = comp.bbox_center
            size = comp.bbox_size
            window.board.floating_parts.append(
                FloatingPart(
                    designator=designator,
                    comment=comp.comment,
                    footprint=comp.footprint,
                    x=center[0] if center else comp.x,
                    y=center[1] if center else comp.y,
                    width=size[0] if size else 0.0,
                    height=size[1] if size else 0.0,
                    pad_count=len(pads),
                )
            )
        window.floating_panel.set_parts(window.board.floating_parts)

    apply_reference_potential(window.classifications)
    window.net_panel.refresh()
    window.sheet_panel.refresh_all()

    rules = window.rules_panel.rules()
    window.violations = find_violations(
        window.board,
        window.classifications,
        rules,
        mounts=window.board.mounts,
        parts=window.board.floating_parts,
    )
    window.aggregated = aggregate_by_component(window.violations)
    window.violation_panel.set_results(window.violations, window.aggregated)
    window.board_view.set_data(window.board, window.classifications, window.violations)
    window._update_overview()
    app.processEvents()

    def shot(name: str) -> None:
        app.processEvents()
        path = os.path.join(OUT, name)
        window.grab().save(path)
        print("saved", path, os.path.getsize(path) // 1024, "KB")

    window.tabs.setCurrentWidget(window.tabs.widget(0))
    shot("shot_overview.png")

    window.tabs.setCurrentWidget(window.overview_tabs)
    window.overview_tabs.setCurrentWidget(window.component_panel)
    shot("shot_components.png")

    window.tabs.setCurrentWidget(window.overview_tabs)
    window.overview_tabs.setCurrentWidget(window.net_book)
    window.net_book.setCurrentWidget(window.sheet_panel)
    window.sheet_panel.tabs.setCurrentIndex(0)
    shot("shot_nets_common.png")

    window.sheet_panel.tabs.setCurrentIndex(2)
    shot("shot_nets_sheet.png")

    window.net_book.setCurrentWidget(window.net_panel)
    shot("shot_nets.png")

    window.tabs.setCurrentWidget(window.rules_panel)
    shot("shot_rules.png")

    window.tabs.setCurrentWidget(window.overview_tabs)
    window.overview_tabs.setCurrentWidget(window.mount_panel)
    shot("shot_mounts.png")

    window.tabs.setCurrentWidget(window.overview_tabs)
    window.overview_tabs.setCurrentWidget(window.floating_panel)
    shot("shot_floating.png")

    window.tabs.setCurrentWidget(window.slot_panel)
    shot("shot_slots.png")

    window.tabs.setCurrentWidget(window.violation_panel)
    shot("shot_violations.png")
    for index in range(window.tabs.count()):
        if window.tabs.tabText(index) == "布局视图":
            window.tabs.setCurrentIndex(index)
            break
    shot("shot_board.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
