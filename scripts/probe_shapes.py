"""探针 2：确认焊盘批量返回、文档列表、原理图网表的字段结构。"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clearance_check.eda.mcp_client import McpStdioClient, find_server_command  # noqa: E402


def main() -> int:
    client = McpStdioClient(
        find_server_command(),
        env={"EDA_AGENT_TOOLSET": "minimal", "EDA_AGENT_BACKEND": "altium"},
    )
    client.start(timeout=120)
    try:
        pads = client.invoke(
            "pcb_get_component_pads",
            {"items": [{"designator": "C38"}, {"designator": "C18"}]},
            timeout=90,
        )
        print("== component_pads ==")
        print(json.dumps(pads, ensure_ascii=False)[:2500])

        docs = client.invoke("proj_list_documents", {}, timeout=90)
        print("\n== documents ==")
        print(json.dumps(docs, ensure_ascii=False)[:2000])

        pins = client.invoke("proj_get_nets", {"limit": 12}, timeout=120)
        print("\n== netlist pins ==")
        print(json.dumps(pins, ensure_ascii=False)[:2000])

        rules = client.invoke(
            "pcb_get_rule_properties", {"name": "Clearance"}, timeout=60
        )
        print("\n== rule ==")
        print(json.dumps(rules, ensure_ascii=False)[:1200])
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
