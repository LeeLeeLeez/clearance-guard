"""开发期探针：验证与 eda-agent / Altium 的连接并打印关键响应结构。"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clearance_check.eda.mcp_client import McpStdioClient, find_server_command  # noqa: E402


def show(title: str, value, limit: int = 1400) -> None:
    print(f"\n===== {title} =====")
    text = json.dumps(value, ensure_ascii=False, indent=2)
    print(text[:limit] + ("\n..." if len(text) > limit else ""))


def main() -> int:
    command = find_server_command()
    print("SERVER COMMAND:", command)
    client = McpStdioClient(
        command,
        env={"EDA_AGENT_TOOLSET": "minimal", "EDA_AGENT_BACKEND": "altium"},
    )
    try:
        info = client.start(timeout=120)
        show("initialize", info)
        show("tools", [t.get("name") for t in client.list_tools()])

        for tool, args in (
            ("app_ping", {}),
            ("proj_list_open", {}),
            ("proj_get_focused", {}),
            ("proj_get_path", {}),
            ("pcb_get_nets", {}),
        ):
            try:
                show(tool, client.invoke(tool, args, timeout=60), 900)
            except Exception as exc:  # noqa: BLE001
                print(f"\n===== {tool} FAILED =====\n{exc}")
                if tool == "app_ping":
                    print("STDERR:", client.stderr_tail)
                    return 2

        try:
            comps = client.invoke("pcb_get_components", {}, timeout=90)
            show("pcb_get_components", comps, 1200)
            items = [
                {"designator": c.get("designator")}
                for c in (comps.get("components") or [])[:2]
                if c.get("designator")
            ]
            if items:
                show("pcb_get_component_pads(items)", client.invoke(
                    "pcb_get_component_pads", {"items": items}, timeout=90), 2000)
            show("proj_list_documents", client.invoke("proj_list_documents", {}, timeout=90), 1200)
            show("proj_get_nets", client.invoke(
                "proj_get_nets", {"limit": 20}, timeout=120), 1600)
        except Exception as exc:  # noqa: BLE001
            print("\nPCB/项目读取失败:", exc)

        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
