"""查看扫描快照的摘要（不连接 Altium）。

用法: python scripts/inspect_snapshot.py [快照路径]
默认使用 json/VPDB_A4_scan.json。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from clearance_check.model import NetClass  # noqa: E402
from clearance_check.snapshot import load_snapshot  # noqa: E402

DEFAULT = os.path.join(ROOT, "json", "VPDB_A4_scan.json")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    snapshot = load_snapshot(path)
    board = snapshot.board

    print(f"快照: {path}")
    print(f"生成时间: {snapshot.created_text}   工程: {snapshot.project_path}")
    print(f"板: {board.name}  {board.source_path}")
    print(
        f"器件 {len(board.components)}  焊盘 {len(board.pads)}  网络 {len(board.nets)}  "
        f"安装焊盘 {len(board.mounts)}  引脚 {len(snapshot.pins)}"
    )
    sheets = snapshot.sheet_index.sheets if snapshot.sheet_index else {}
    print(f"图纸 {len(sheets)} 张，跨页网络 {len(snapshot.sheet_index.shared_nets()) if snapshot.sheet_index else 0} 个")

    counts: dict[str, int] = {}
    for item in snapshot.classifications.values():
        counts[item.net_class.value] = counts.get(item.net_class.value, 0) + 1
    for net_class in NetClass:
        print(f"  {net_class.value:<18} {counts.get(net_class.value, 0)}")

    review = sum(1 for item in snapshot.classifications.values() if item.needs_review)
    print(f"低置信度 / 待核对 {review}")

    drives = [item for item in snapshot.classifications.values() if item.is_drive]
    print(f"\n驱动网络 {len(drives)} 个（示例 6 个）：")
    for item in sorted(drives, key=lambda d: d.net)[:6]:
        ref = snapshot.classifications.get(item.ref_ground)
        print(
            f"  {item.net:<20} {item.voltage!s:<6} -> {item.ref_ground or '—':<14} "
            f"{(ref.range_text if ref else '—')}"
        )
    for mount in board.mounts[:4]:
        print(
            f"  安装焊盘 {mount.name}: 原网络={mount.net or '—'} "
            f"归属={mount.net_class.value} {mount.voltage}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
