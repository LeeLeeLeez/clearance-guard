"""端到端联调：走真实界面路径连接 Altium、扫描当前工程、跑间距校验。

用法：python scripts/e2e_live.py
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from clearance_check.eda.session import ConnectionState  # noqa: E402
from clearance_check.ui.main_window import MainWindow  # noqa: E402
from clearance_check.ui.theme import apply_theme  # noqa: E402


def wait(window: MainWindow, app: QApplication, timeout: float = 300.0, label: str = "") -> None:
    started = time.time()
    while window._worker is not None and window._worker.isRunning():
        app.processEvents()
        time.sleep(0.05)
        if time.time() - started > timeout:
            raise TimeoutError(f"{label} 超时")
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)
    print(f"  {label} 完成，用时 {time.time() - started:.1f}s", flush=True)


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow()
    window.resize(1480, 940)
    window.show()
    app.processEvents()

    print("[1] 连接", flush=True)
    window._toggle_connection()
    wait(window, app, 120, "connect")
    print("  state:", window.session.state, window.status_label.text(), flush=True)
    if window.session.state is not ConnectionState.READY:
        print("  尝试 relaunch 桥接…", flush=True)
        ok, output = window.session.relaunch_bridge()
        print("  relaunch:", ok, output[-200:].replace("\n", " "), flush=True)
        window._toggle_connection()
        wait(window, app, 120, "reconnect")
    if window.session.state is not ConnectionState.READY:
        print("连接失败:", window.session.last_error, flush=True)
        return 2

    print("[2] 扫描", flush=True)
    window._scan()
    wait(window, app, 300, "scan")
    if window.board is None:
        print("扫描失败:", window.status_label.text(), flush=True)
        return 3
    print(
        f"  器件 {len(window.board.components)} 焊盘 {len(window.board.pads)} "
        f"网络 {len(window.board.nets)} 安装焊盘 {len(window.board.mounts)}",
        flush=True,
    )
    for mount in window.board.mounts[:4]:
        print(
            f"    安装焊盘 {mount.name}: ({mount.x:.1f},{mount.y:.1f}) 孔径 {mount.hole:.2f} "
            f"原网络={mount.net or '—'}",
            flush=True,
        )
    for net in ("18Vp-H1", "18Vp-H2", "12Vs", "H1-GND"):
        item = window.classifications.get(net)
        if item is None:
            continue
        ref = window.classifications.get(item.ref_ground) if item.ref_ground else None
        print(
            f"    {net}: {item.voltage} {item.range_text} 参考地={item.ref_ground or '—'} "
            f"({ref.range_text if ref else '—'})",
            flush=True,
        )

    print("[3] 间距校验", flush=True)
    window._start_clearance()
    wait(window, app, 120, "clearance")
    print(
        f"  器件对 {len(window.aggregated)} 焊盘级 {len(window.violations)}",
        flush=True,
    )
    for item in window.aggregated[:6]:
        print(
            f"    {item.designator_a} / {item.designator_b}: "
            f"{item.actual_mm:.2f} -> {item.required_mm:.2f} mm, 缺口 {item.deficit_mm:.2f}",
            flush=True,
        )

    os.makedirs(os.path.join(ROOT, "dev"), exist_ok=True)
    window.tabs.setCurrentWidget(window.violation_panel)
    app.processEvents()
    path = os.path.join(ROOT, "dev", "shot_live.png")
    window.grab().save(path)
    print("截图:", path, flush=True)

    window.session.disconnect()
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
