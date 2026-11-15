"""把当前 Altium 工程扫描结果缓存为 JSON，便于离线反复调参。"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clearance_check.eda.session import EdaAgentSession  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dev", "live_board.json")


def main() -> int:
    session = EdaAgentSession()
    print("connect:", session.connect(), flush=True)
    if not session.connected:
        print(session.last_error)
        return 2
    started = time.time()

    def progress(stage: str, detail: str) -> None:
        print(f"[{time.time()-started:6.1f}s] {stage}: {detail}", flush=True)

    try:
        board, report = session.scan(progress=progress)
    finally:
        session.disconnect()

    payload = {
        "board": {
            "name": board.name,
            "width": board.width,
            "height": board.height,
            "nets": board.nets,
            "source_path": board.source_path,
            "source_format": board.source_format,
            "components": [
                {
                    "designator": c.designator,
                    "comment": c.comment,
                    "footprint": c.footprint,
                    "x": c.x,
                    "y": c.y,
                    "rotation": c.rotation,
                    "layer": c.layer,
                }
                for c in board.components.values()
            ],
            "pads": [
                {
                    "name": p.name,
                    "net": p.net,
                    "component": p.component,
                    "x": p.x,
                    "y": p.y,
                    "width": p.width,
                    "height": p.height,
                    "rotation": p.rotation,
                    "shape": p.shape,
                    "layer": p.layer,
                    "hole": p.hole,
                }
                for p in board.pads
            ],
        },
        "pins": report.pins,
        "report": {"steps": report.steps, "warnings": report.warnings},
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    print(f"saved {OUT} ({os.path.getsize(OUT)/1024:.0f} KB) in {time.time()-started:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
