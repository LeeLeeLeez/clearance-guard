"""离线分析缓存的设计：分类统计 + 避距违规统计。"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from clearance_check.analysis.classify import classify_board  # noqa: E402
from clearance_check.analysis.clearance import aggregate_by_component, find_violations  # noqa: E402
from clearance_check.analysis.rules import default_rules  # noqa: E402
from clearance_check.model import Board, Component, NetClass, Pad  # noqa: E402


def load_board(path: str) -> tuple[Board, list[dict]]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    raw = data["board"]
    board = Board(name=raw["name"], source_path=raw.get("source_path", ""), nets=list(raw["nets"]))
    for item in raw.get("components", []):
        board.add_component(Component(**item))
    for item in raw.get("pads", []):
        board.add_pad(Pad(**item))
    return board, data.get("pins", [])


def main() -> int:
    board, pins = load_board(os.path.join(ROOT, "dev", "live_board.json"))
    print(f"板: {board.name}  器件 {len(board.components)}  焊盘 {len(board.pads)}  网络 {len(board.nets)}")

    t0 = time.perf_counter()
    result = classify_board(board, pins)
    print(f"分类耗时 {time.perf_counter()-t0:.2f}s")
    counter = Counter(item.net_class.value for item in result.values())
    for net_class in NetClass:
        print(f"  {net_class.value:<18} {counter.get(net_class.value, 0)}")
    for name in list(result)[:12]:
        item = result[name]
        print(f"  {name:<28} {item.net_class.value:<18} {item.range_text:<16} conf={item.confidence} "
              f"| {item.reasons[0] if item.reasons else ''}")

    rules = default_rules()
    t0 = time.perf_counter()
    violations = find_violations(board, result, rules)
    print(f"\n避距计算耗时 {time.perf_counter()-t0:.2f}s，焊盘级违规 {len(violations)}")
    for violation in violations[:10]:
        print(f"  {violation.pad_a:<16} {violation.net_a:<18} -- {violation.pad_b:<16} {violation.net_b:<18} "
              f"actual {violation.actual_mm:6.3f}  need {violation.required_mm:6.3f}  deficit {violation.deficit_mm:6.3f}  [{violation.rule_name}]")

    aggregated = aggregate_by_component(violations)
    print(f"器件级违规 {len(aggregated)}")
    for item in aggregated[:10]:
        print(f"  {item.pair_label:<20} {item.net_a:<18}/{item.net_b:<18} 缺口 {item.deficit_mm:.3f} mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
