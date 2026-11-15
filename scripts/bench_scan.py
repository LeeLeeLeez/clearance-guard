"""分步计时探针：定位扫描过程中耗时/卡住的阶段（每步都很短，避免拖住 Altium）。"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clearance_check.eda.mcp_client import McpStdioClient, find_server_command  # noqa: E402


def timed(client, tool: str, args: dict | None = None, timeout: float = 60.0):
    start = time.perf_counter()
    payload = client.invoke(tool, args or {}, timeout=timeout)
    elapsed = time.perf_counter() - start
    result = payload.get("result") if isinstance(payload, dict) and "result" in payload else payload
    print(f"  {tool:<28} {elapsed:6.2f}s", flush=True)
    return result


def main() -> int:
    client = McpStdioClient(find_server_command(), env={"EDA_AGENT_TOOLSET": "minimal"})
    client.start(timeout=90)
    try:
        print("ping:", flush=True)
        ping = timed(client, "app_ping", {}, 20)
        if not ping.get("success"):
            print("bridge down:", ping.get("message"))
            return 2

        print("project:", flush=True)
        focused = timed(client, "proj_get_focused", {}, 30)
        print("  ->", focused.get("project_path"), flush=True)

        docs = timed(client, "proj_list_documents", {}, 30)
        boards = [d["file_path"] for d in docs if str(d.get("document_kind")) == "PCB"]
        print("  -> board:", boards[:1], flush=True)

        timed(client, "pcb_focus_board", {"board_path": boards[0]}, 30)

        comps = timed(client, "pcb_get_components", {}, 90)
        components = comps.get("components") or []
        print("  -> components:", len(components), flush=True)

        nets = timed(client, "pcb_get_nets", {}, 60)
        print("  -> nets:", len(nets.get("nets") or []), flush=True)

        designators = [c.get("designator") for c in components[:5]]
        pads = timed(
            client,
            "pcb_get_component_pads",
            {"items": [{"designator": d} for d in designators]},
            60,
        )
        print("  -> pad groups:", len(pads.get("items") or []), flush=True)
        sample = (pads.get("items") or [{}])[0]
        print("  -> sample:", sample.get("designator"), sample.get("pads", [])[:2], flush=True)

        pins = timed(client, "proj_get_nets", {"limit": 100}, 90)
        print("  -> pins:", len(pins.get("pins") or []), flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
