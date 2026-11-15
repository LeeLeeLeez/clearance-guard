"""探针：确认按原理图文档读取器件（obj_query scope=doc:PATH）。"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from clearance_check.eda.mcp_client import McpStdioClient, find_server_command  # noqa: E402


def main() -> int:
    client = McpStdioClient(find_server_command(), env={"EDA_AGENT_TOOLSET": "minimal"})
    client.start(timeout=90)
    try:
        ping = client.invoke("app_ping", {}, timeout=20)
        result = ping.get("result", ping)
        print("ping:", result.get("success"), result.get("message", "")[:80], flush=True)
        if not result.get("success"):
            return 2

        docs = client.invoke("proj_list_documents", {}, timeout=60)
        entries = docs.get("result", docs)
        sheets = [d for d in entries if str(d.get("document_kind")) == "SCH"]
        print("sheets:", len(sheets), flush=True)

        for doc in sheets[:3]:
            path = doc["file_path"]
            payload = client.invoke(
                "obj_query",
                {
                    "object_type": "eSchComponent",
                    "properties": "Designator.Text,Comment.Text",
                    "scope": f"doc:{path}",
                    "limit": 200,
                },
                timeout=60,
            )
            body = payload.get("result", payload)
            objects = body.get("objects") or []
            print(f"\n{doc['file_name']}: {len(objects)} objects")
            print(json.dumps(objects[:3], ensure_ascii=False)[:600], flush=True)
            designs = [str(o.get("Designator.Text") or o.get("Designator") or "") for o in objects]
            print("designators:", designs[:12], flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
