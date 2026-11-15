"""用缓存的器件/网表数据验证驱动网络电压与参考地判定（不扫描 PCB 焊盘）。"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from clearance_check.analysis.classify import classify_board  # noqa: E402
from clearance_check.model import Board, Component, Pad  # noqa: E402

CACHE = os.path.join(ROOT, "dev", "live_probe.json")


def main() -> int:
    if not os.path.isfile(CACHE):
        print(f"缺少缓存 {CACHE}，请先运行 scripts/probe_drive_mount.py")
        return 2
    data = json.load(open(CACHE, encoding="utf-8"))
    board = Board(name="live")
    for comp in data["components"]:
        designator = str(comp.get("designator") or "")
        if designator:
            board.add_component(Component(designator=designator, comment=str(comp.get("comment") or "")))
    for record in data["pins"]:
        net = str(record.get("net") or "")
        component = str(record.get("component") or "")
        if not net:
            continue
        board.add_pad(Pad(name=str(record.get("pin") or ""), net=net, component=component))

    results = classify_board(board, data["pins"])

    for net in ("18Vp-H1", "18Vp-H2", "18Vp-L1", "12Vs", "5Vs", "H1-GND", "L1-GND", "GATE"):
        item = results.get(net)
        if item is None:
            print(f"{net:<12} (不存在)")
            continue
        print(
            f"{net:<12} {item.net_class.value:<18} {item.voltage!s:<7} "
            f"{item.range_text:<18} drive={item.is_drive} ref={item.ref_ground or '—'}"
            f"  依据={item.ref_ground_reason}"
        )

    drives = [item for item in results.values() if item.is_drive]
    print(f"\n驱动网络 {len(drives)} 个，带参考地 {sum(1 for d in drives if d.ref_ground)} 个")
    for item in sorted(drives, key=lambda d: d.net)[:12]:
        ref = results.get(item.ref_ground)
        print(
            f"  {item.net:<20} {item.voltage!s:<6} -> {item.ref_ground or '—':<14} "
            f"{(ref.range_text if ref else '')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
