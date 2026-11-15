"""探针：查看驱动网络（18Vp/12Vs）的连接关系与安装孔/安装焊盘情况。"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from clearance_check.eda.session import EdaAgentSession  # noqa: E402

CACHE = os.path.join(ROOT, "dev", "live_probe.json")


def main() -> int:
    session = EdaAgentSession()
    print("connect:", session.connect(), flush=True)
    if not session.connected:
        print(session.last_error)
        return 2
    try:
        if os.path.isfile(CACHE):
            data = json.load(open(CACHE, encoding="utf-8"))
            components, pins = data["components"], data["pins"]
            print("cache loaded")
        else:
            components = session.components()
            pins = session.netlist_pins()
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            json.dump({"components": components, "pins": pins}, open(CACHE, "w", encoding="utf-8"),
                      ensure_ascii=False)
    finally:
        session.disconnect()

    print(f"components {len(components)}  pins {len(pins)}")

    net_pins: dict[str, list[tuple[str, str]]] = {}
    for record in pins:
        net = str(record.get("net") or "")
        net_pins.setdefault(net, []).append(
            (str(record.get("component") or ""), str(record.get("pin_name") or ""))
        )

    for net in sorted(net_pins):
        if "18Vp" in net or "12Vs" in net or net in ("H1-GND", "L1-GND", "PGND", "SGND"):
            print(f"\n{net}: {net_pins[net][:10]}")

    print("\n-- 含 mount/hole 的器件 --")
    for comp in components:
        text = f"{comp.get('comment','')} {comp.get('footprint','')}".upper()
        designator = str(comp.get("designator") or "")
        if any(key in text for key in ("MOUNT", "HOLE", "M3", "M2.5", "M4", "NPTH", "STANDOFF")):
            print(" ", designator, "|", comp.get("comment"), "|", comp.get("footprint"))

    print("\n-- 前缀 H/MH 的器件 --")
    for comp in components:
        designator = str(comp.get("designator") or "")
        if designator.upper().startswith(("H", "MH", "MTH")):
            print(" ", designator, "|", comp.get("comment"), "|", comp.get("footprint"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
