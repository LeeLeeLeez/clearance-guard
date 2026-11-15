"""在真实 Altium 工程上验证「套用历史快照配置」。

流程：连接 -> 扫描当前工程 -> 自动分类 -> 套用快照 -> 打印统计与抽样 -> 跑一次避距。
"""

from __future__ import annotations

import os
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from clearance_check.analysis import (  # noqa: E402
    aggregate_by_component,
    apply_reference_potential,
    assign_secondary_references,
    classify_board,
    find_violations,
    merge_classifications,
    merge_mounts,
)
from clearance_check.analysis.rules import ClearanceRules  # noqa: E402
from clearance_check.eda.session import EdaAgentSession  # noqa: E402
from clearance_check.snapshot import load_snapshot  # noqa: E402

SNAPSHOT = os.path.join(ROOT, "json", "VPDB_A4_scan_20261113_112107.json")


def main() -> int:
    started = time.time()
    snapshot = load_snapshot(SNAPSHOT, reclassify=False)
    print(f"快照：{os.path.basename(SNAPSHOT)}  生成于 {snapshot.created_text}")
    print(f"  板 {snapshot.board.name}  网络 {len(snapshot.classifications)}  "
          f"安装焊盘 {len(snapshot.board.mounts)}", flush=True)

    session = EdaAgentSession()
    print("\n[1] 连接 Altium …", flush=True)
    print("   ", session.connect(), flush=True)

    print("[2] 扫描当前工程（不读图纸，约 1~2 分钟）…", flush=True)
    t0 = time.time()
    board, report = session.scan(
        with_netlist=True,
        with_sheets=False,
        progress=lambda stage, detail: print(f"    {stage}: {detail}", flush=True),
    )
    print(f"    扫描完成 {time.time() - t0:.1f}s：器件 {len(board.components)} "
          f"焊盘 {len(board.pads)} 网络 {len(board.nets)} 安装焊盘 {len(board.mounts)}",
          flush=True)

    print("[3] 自动分类 …", flush=True)
    t0 = time.time()
    fresh = classify_board(board, report.pins)
    before = Counter(item.net_class.value for item in fresh.values())
    print(f"    {time.time() - t0:.2f}s  {dict(before)}", flush=True)

    print("[4] 套用快照配置 …", flush=True)
    stats = merge_classifications(fresh, snapshot.classifications)
    stats.mounts_kept = merge_mounts(board.mounts, snapshot.board.mounts)
    assign_secondary_references(fresh)
    apply_reference_potential(fresh)
    after = Counter(item.net_class.value for item in fresh.values())
    print(f"    {stats.summary}", flush=True)
    print(f"    套用后类别分布 {dict(after)}", flush=True)

    for net in ("12Vs", "5Vs", "3V3D", "18Vp-H1", "SGND", "PGND"):
        item = fresh.get(net)
        if item is None:
            continue
        print(f"    {net:<10} {item.net_class.value:<18} {item.range_text:<16} "
              f"ref={item.ref_ground or '—':<10} edited={item.user_edited}", flush=True)

    added = [net for net in fresh if net not in snapshot.classifications]
    print(f"    新增网络 {len(added)} 个：{added[:8]}", flush=True)

    print("[5] 间距校验 …", flush=True)
    violations = find_violations(board, fresh, ClearanceRules(), mounts=board.mounts)
    print(f"    违规 {len(violations)} 条焊盘级 / {len(aggregate_by_component(violations))} 个器件对",
          flush=True)
    print(f"总耗时 {time.time() - started:.1f}s", flush=True)
    session.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
