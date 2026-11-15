"""探针：只读取网表 + 各图纸器件，验证「图纸 -> 网络」聚合（不读 PCB 焊盘）。"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from clearance_check.analysis.sheets import build_sheet_index, split_common_nets  # noqa: E402
from clearance_check.eda.session import EdaAgentSession  # noqa: E402
from clearance_check.model import Board  # noqa: E402


def main() -> int:
    session = EdaAgentSession()
    print("connect:", session.connect(), flush=True)
    if not session.connected:
        print(session.last_error)
        return 2

    started = time.time()
    try:
        pins = session.netlist_pins()
        print(f"netlist pins: {len(pins)}  ({time.time()-started:.1f}s)", flush=True)
        sheets, paths, instances, top = session.sheet_layout(
            progress=lambda stage, detail: print(f"  [{time.time()-started:5.1f}s] {stage}: {detail}", flush=True)
        )
        print(f"instances: {len(instances)}  top: {top}", flush=True)
        print(f"sheets: {len(sheets)}  ({time.time()-started:.1f}s)", flush=True)
    finally:
        session.disconnect()

    board = Board(name="live")
    for record in pins:
        net = str(record.get("net") or "")
        if net:
            board.ensure_net(net)

    index = build_sheet_index(pins, sheets, board, paths, instances, top)
    print("\n" + index.summary())
    for sheet, nets in index.sheets.items():
        shared = sum(1 for net in nets if index.is_shared(net))
        print(f"  {sheet:<24} {len(nets):>4} 个网络（跨页 {shared}）  {len(sheets.get(sheet, []))} 个器件")

    buckets = split_common_nets(index, board, {})
    print(f"\ncommon: 功率侧 {len(buckets['power'])} / 控制侧 {len(buckets['control'])}")
    print("功率侧:", buckets["power"][:12])
    print("控制侧:", buckets["control"][:12])

    orphan = index.sheets.get("（未归属图纸）", [])
    if orphan:
        print(f"未归属图纸的网络 {len(orphan)} 个:", orphan[:12])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
