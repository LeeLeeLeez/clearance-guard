"""eda-agent 会话封装：把实时 Altium 设计读成本项目的数据模型。

坐标约定：eda-agent 全程使用 mil（1 mil = 0.0254 mm），本层统一换算为 mm。
"""

from __future__ import annotations

import math

import os
import subprocess
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

from ..model import Board, Component, Pad
from .mcp_client import McpError, McpStdioClient, find_server_command

MIL_TO_MM = 0.0254


class EdaAgentError(RuntimeError):
    """EDA Agent 会话级错误。"""


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    READY = "ready"
    NO_BRIDGE = "no_bridge"      # 服务起来了，但 Altium 脚本桥没响应
    ERROR = "error"


@dataclass
class LiveProject:
    name: str
    path: str
    document_count: int = 0

    @property
    def label(self) -> str:
        return f"{self.name}  ({self.path})"


@dataclass
class ScanReport:
    """一次扫描的过程记录，便于界面展示。"""

    project_path: str = ""
    board_path: str = ""
    component_count: int = 0
    pad_count: int = 0
    net_count: int = 0
    steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pins: list[dict[str, Any]] = field(default_factory=list)
    sheet_components: dict[str, list[str]] = field(default_factory=dict)
    sheet_paths: dict[str, str] = field(default_factory=dict)
    instance_sheets: dict[str, str] = field(default_factory=dict)
    top_sheet: str = ""
    mount_pads: list[dict[str, Any]] = field(default_factory=list)
    floating_parts: list[dict[str, Any]] = field(default_factory=list)
    cutouts: list[dict[str, Any]] = field(default_factory=list)


def mm(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) * MIL_TO_MM
    except (TypeError, ValueError):
        return default


_BURN_KEYWORDS = ("BURN", "PROGRAM", "PROG", "CASTELL", "HALF-HOLE", "半孔", "烧录")


def _guess_category(item: dict) -> str:
    """按注释/封装猜器件类别：烧录端子（半孔）自动标记，用户可在界面改。"""
    text = " ".join(
        str(item.get(key) or "") for key in ("comment", "footprint")
    ).upper()
    if any(keyword in text for keyword in _BURN_KEYWORDS):
        return "burn_terminal"
    return ""


def _component_bbox(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """从器件记录里取外形包围盒（优先去掉位号/注释文字的 body 框），单位 mm。"""
    raw = item.get("bbox_no_name_comment") or item.get("bbox")
    if not isinstance(raw, dict):
        return None
    try:
        x1 = mm(raw.get("x1"))
        y1 = mm(raw.get("y1"))
        x2 = mm(raw.get("x2"))
        y2 = mm(raw.get("y2"))
    except (TypeError, ValueError):
        return None
    if abs(x2 - x1) < 1e-6 and abs(y2 - y1) < 1e-6:
        return None
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def filter_cutouts(cutouts: list[dict], outline: list[tuple[float, float]], board) -> list[dict]:
    """剔除「板框本身」和「器件封装（含磁芯）内的挖空」，只留板级避距槽。"""
    kept = list(cutouts)

    # 1) 与板框外框重合 = 板框本身
    if len(outline) >= 3:
        xs = [point[0] for point in outline]
        ys = [point[1] for point in outline]
        ox0, oy0, ox1, oy1 = min(xs), min(ys), max(xs), max(ys)
        ow, oh = ox1 - ox0, oy1 - oy0
        kept = [
            cut for cut in kept
            if not (
                abs(cut["x"] - (ox0 + ox1) / 2) < 1.0
                and abs(cut["y"] - (oy0 + oy1) / 2) < 1.0
                and abs(cut["width"] - ow) < 2.0
                and abs(cut["height"] - oh) < 2.0
            )
        ]

    # 2) 器件封装内的挖空。无焊盘器件（磁芯 / 结构件）周边按 10 mm 放宽，
    #    因为它们的封装挖空常常围绕本体、超出 body 外框。
    boxes: list[tuple[float, float, float, float]] = []
    for designator, comp in board.components.items():
        box = board.component_bbox(designator)
        if not box:
            continue
        margin = 10.0 if not board.pads_of_component(designator) else 0.05
        boxes.append((box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin))
    if not boxes:
        return kept

    def inside_footprint(cut: dict) -> bool:
        x0 = cut["x"] - cut["width"] / 2
        x1 = cut["x"] + cut["width"] / 2
        y0 = cut["y"] - cut["height"] / 2
        y1 = cut["y"] + cut["height"] / 2
        for bx0, by0, bx1, by1 in boxes:
            if x0 >= bx0 and x1 <= bx1 and y0 >= by0 and y1 <= by1:
                return True
        return False

    return [cut for cut in kept if not inside_footprint(cut)]


def board_frame_cutout(cutouts: list[dict]):
    """在挖空列表里找出「板框本身」（面积最大且接近板子外框 B 的那条）。"""
    if not cutouts:
        return None
    return max(cutouts, key=lambda cut: cut["width"] * cut["height"])


def classify_cutouts(
    cutouts: list[dict],
    outline: list[tuple[float, float]],
    board,
    keep_existing: bool = True,
) -> list[dict]:
    """给每个挖空打归属标签：frame / footprint / creepage。

    - frame：与板框外框重合（板框本身）
    - footprint：落在某个器件封装外框内（无焊盘器件放宽 10 mm）
    - creepage：其余，视为增大爬电距离的避距槽
    """
    xs = [point[0] for point in outline] or [0.0]
    ys = [point[1] for point in outline] or [0.0]
    ox0, oy0, ox1, oy1 = min(xs), min(ys), max(xs), max(ys)
    ow, oh = ox1 - ox0, oy1 - oy0

    # 普通器件：外框放宽 1 mm；无焊盘器件（磁芯/结构件）：多边形挖空放宽 10 mm。
    # 圆孔不放宽——否则靠近磁芯的真实安装孔会被当成封装孔。
    tight: list[tuple[float, float, float, float]] = []
    wide: list[tuple[float, float, float, float]] = []
    copper: list[tuple[float, float, float, float]] = []
    for designator in board.components:
        box = board.component_bbox(designator)
        if not box:
            continue
        has_pads = bool(board.pads_of_component(designator))
        margin = 0.05 if has_pads else 1.0
        tight.append((box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin))
        if has_pads:
            copper.append((box[0] - 0.05, box[1] - 0.05, box[2] + 0.05, box[3] + 0.05))
        else:
            wide.append((box[0] - 10.0, box[1] - 10.0, box[2] + 10.0, box[3] + 10.0))

    result = []
    for cut in cutouts:
        item = dict(cut)
        if keep_existing and item.get("role"):
            # 人工指定过归属的挖空不再自动改写（导入快照时尤其重要）
            result.append(item)
            continue
        if len(outline) >= 3 and (
            abs(item["x"] - (ox0 + ox1) / 2) < 1.0
            and abs(item["y"] - (oy0 + oy1) / 2) < 1.0
            and abs(item["width"] - ow) < 2.0
            and abs(item["height"] - oh) < 2.0
        ):
            item["role"] = "frame"
            result.append(item)
            continue

        if item.get("kind") == "circle":
            # 圆孔：必须**整体**落在某个器件的焊盘铜箔范围内，才算封装自带孔。
            # 只查中心会把靠得很近的真实安装孔误判（例如磁芯旁边的 Ø3.5 孔）。
            x0 = item["x"] - item["width"] / 2
            x1 = item["x"] + item["width"] / 2
            y0 = item["y"] - item["height"] / 2
            y1 = item["y"] + item["height"] / 2
            inside = any(
                bx0 <= x0 and x1 <= bx1 and by0 <= y0 and y1 <= by1
                for bx0, by0, bx1, by1 in copper
            )
        else:
            # 多边形挖空：中心落在器件外框内即可（磁芯允许 10 mm 放宽）
            inside = any(
                bx0 <= item["x"] <= bx1 and by0 <= item["y"] <= by1
                for bx0, by0, bx1, by1 in wide + tight
            )
        item["role"] = "footprint" if inside else "creepage"
        result.append(item)
    return result


class EdaAgentSession:
    """启动并驱动 eda-agent MCP 服务，读写当前打开的 Altium 工程。"""

    def __init__(
        self,
        command: list[str] | None = None,
        agent_root: str | Path | None = None,
        python_exe: str = "",
        timeout: float = 60.0,
    ):
        self._command = command
        self._agent_root = agent_root
        self._python_exe = python_exe
        self._timeout = timeout
        self.client: McpStdioClient | None = None
        self.state: ConnectionState = ConnectionState.DISCONNECTED
        self.last_error: str = ""
        self.server_info: dict[str, Any] = {}
        self._keepalive: threading.Thread | None = None
        self._keepalive_stop: threading.Event | None = None

    # ---- 连接 -----------------------------------------------------
    def connect(self) -> ConnectionState:
        if self.state is ConnectionState.READY and self.client and self.client.alive:
            return self.state
        self.state = ConnectionState.CONNECTING
        command = self._command or find_server_command(self._agent_root, self._python_exe)
        env = {
            "EDA_AGENT_TOOLSET": "minimal",
            "EDA_AGENT_BACKEND": "altium",
            "EDA_AGENT_DISABLE_DASHBOARD": "1",
        }
        client = McpStdioClient(command, env=env, name="clearance-gui")
        try:
            self.server_info = client.start(timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001
            # 子进程多半是启动后立刻崩了，把它的 stderr 尾部一起带出来便于定位
            tail = client.stderr_tail.strip()
            client.close()
            self.last_error = f"{exc}\n\n{tail}" if tail else str(exc)
            self.state = ConnectionState.ERROR
            return self.state

        self.client = client
        try:
            self.ping()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.state = ConnectionState.NO_BRIDGE
            return self.state
        self.state = ConnectionState.READY
        self.last_error = ""
        self._start_keepalive()
        return self.state

    def disconnect(self) -> None:
        self._stop_keepalive()
        if self.client:
            self.client.close()
        self.client = None
        self.state = ConnectionState.DISCONNECTED

    # ---- 保活（避免 Altium 轮询 10 分钟静默自动退出） --------------
    def _start_keepalive(self, interval: float = 25.0) -> None:
        if self._keepalive is not None:
            return
        stop = threading.Event()
        self._keepalive_stop = stop

        def loop() -> None:
            while not stop.wait(interval):
                if not (self.client and self.client.alive):
                    return
                try:
                    self.client.invoke("app_ping", {}, timeout=20)
                except Exception:  # noqa: BLE001 - 保活失败不打断界面
                    pass

        self._keepalive = threading.Thread(target=loop, name="eda-keepalive", daemon=True)
        self._keepalive.start()

    def _stop_keepalive(self) -> None:
        if self._keepalive_stop is not None:
            self._keepalive_stop.set()
            self._keepalive_stop = None
        self._keepalive = None

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.alive and self.state is ConnectionState.READY

    def _call(self, tool: str, arguments: dict[str, Any] | None = None,
              timeout: float | None = None) -> Any:
        if not self.client:
            raise EdaAgentError("尚未连接 EDA Agent")
        try:
            payload = self.client.invoke(tool, arguments or {}, timeout=timeout or self._timeout)
        except McpError as exc:
            raise EdaAgentError(str(exc)) from exc
        # tool_invoke 把结果包成 {"tool": ..., "result": ...}
        if isinstance(payload, dict) and payload.get("tool") == tool and "result" in payload:
            return payload["result"]
        return payload

    # ---- 基础查询 -------------------------------------------------
    def ping(self) -> dict[str, Any]:
        return self._call("app_ping", {}, timeout=30) or {}

    def list_projects(self) -> list[LiveProject]:
        payload = self._call("proj_list_open", {}, timeout=45)
        projects = payload.get("projects", []) if isinstance(payload, dict) else payload
        out: list[LiveProject] = []
        for item in projects or []:
            if not isinstance(item, dict):
                continue
            out.append(
                LiveProject(
                    name=_text(item.get("project_name") or item.get("name")),
                    path=_text(item.get("project_path") or item.get("path")),
                    document_count=int(item.get("document_count") or 0),
                )
            )
        return out

    def focused_project(self) -> LiveProject | None:
        payload = self._call("proj_get_focused", {}, timeout=30)
        if not isinstance(payload, dict) or not payload:
            return None
        path = _text(payload.get("project_path"))
        if not path:
            return None
        return LiveProject(
            name=_text(payload.get("project_name")) or Path(path).stem,
            path=path,
            document_count=int(payload.get("document_count") or 0),
        )

    def open_project(self, project_path: str) -> LiveProject:
        payload = self._call("proj_open", {"project_path": project_path}, timeout=120)
        path = _text(payload.get("project_path") or project_path) if isinstance(payload, dict) else project_path
        return LiveProject(name=Path(path).stem, path=path)

    def list_documents(self, project_path: str = "") -> list[dict[str, Any]]:
        arguments = {"project_path": project_path} if project_path else {}
        payload = self._call("proj_list_documents", arguments, timeout=90)
        documents = payload if isinstance(payload, list) else (payload.get("documents") or [])
        return [d for d in documents if isinstance(d, dict)]

    def board_documents(self, project_path: str = "") -> list[str]:
        out: list[str] = []
        for doc in self.list_documents(project_path):
            path = _text(doc.get("file_path") or doc.get("path"))
            if path.lower().endswith(".pcbdoc"):
                out.append(path)
        return out

    def focus_board(self, board_path: str) -> None:
        self._call("pcb_focus_board", {"board_path": board_path}, timeout=60)

    def cross_probe(self, designator: str, target: str = "pcb") -> dict[str, Any]:
        """在 Altium 中跳到并高亮指定器件。"""
        return self._call(
            "proj_cross_probe", {"designator": designator, "target": target}, timeout=45
        )

    # ---- 桥接恢复 -------------------------------------------------
    def relaunch_bridge(self, timeout: float = 240.0) -> tuple[bool, str]:
        """调用 `eda-agent relaunch --restart`，启动 Altium 侧的脚本轮询。

        仅在 Altium 已打开、脚本项目已安装时有效。返回 (是否成功, 说明)。
        """
        command = self._command or find_server_command(self._agent_root, self._python_exe)
        if not command:
            return False, "找不到可运行的 eda-agent 命令"
        base = list(command)
        if "--headless" in base:
            base.remove("--headless")
        if base and base[0].lower().endswith("python.exe"):
            base = base[:3]
        try:
            completed = subprocess.run(
                base + ["relaunch", "--restart"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"启动桥接失败: {exc}"
        output = (completed.stdout or "") + (completed.stderr or "")
        ok = "the new loop answers" in output or "bridge answers" in output
        return ok, output.strip()[-800:]

    def board_nets(self) -> list[str]:
        payload = self._call("pcb_get_nets", {}, timeout=90)
        if isinstance(payload, dict):
            nets = payload.get("nets") or []
        else:
            nets = payload or []
        return [_text(n) for n in nets if _text(n).strip()]

    # ---- 读取器件 / 焊盘 ------------------------------------------
    def components(self) -> list[dict[str, Any]]:
        payload = self._call("pcb_get_components", {}, timeout=120)
        if isinstance(payload, dict):
            return [c for c in (payload.get("components") or []) if isinstance(c, dict)]
        return [c for c in (payload or []) if isinstance(c, dict)]

    def component_pads(self, designators: Iterable[str], chunk: int = 60) -> dict[str, list[dict[str, Any]]]:
        """批量读取多个器件的焊盘（一次 IPC 往返）。"""
        names = [d for d in designators if d]
        out: dict[str, list[dict[str, Any]]] = {d: [] for d in names}
        for start in range(0, len(names), chunk):
            batch = names[start:start + chunk]
            items = [{"designator": d} for d in batch]
            payload = self._call("pcb_get_component_pads", {"items": items}, timeout=180)
            for designator, pads in zip(batch, _bulk_items(payload)):
                out[designator] = _extract_pads(pads)
        return out

    def pad_shapes_by_net(self, nets: Iterable[str], limit_nets: int = 300) -> dict[tuple[float, float], str]:
        """读取焊盘外形（矩形仅按 bbox 近似，圆形/八边形可精确）。"""
        names = [n for n in nets if n][:limit_nets]
        if not names:
            return {}
        items = [{"net": n} for n in names]
        try:
            payload = self._call("pcb_get_pad_properties", {"items": items}, timeout=240)
        except EdaAgentError:
            return {}
        shapes: dict[tuple[float, float], str] = {}
        for entry in _bulk_items(payload):
            for pad in _extract_pads(entry):
                key = (round(mm(pad.get("x")), 2), round(mm(pad.get("y")), 2))
                shape = _text(pad.get("shape")).lower()
                if shape:
                    shapes[key] = _map_shape(shape)
        return shapes

    def netlist_pins(self, limit: int = 8000) -> list[dict[str, Any]]:
        """原理图编译网表（引脚名 -> 网络），用于拓扑推断。"""
        try:
            payload = self._call("proj_get_nets", {"limit": limit}, timeout=180)
        except EdaAgentError:
            return []
        if isinstance(payload, dict):
            return [p for p in (payload.get("pins") or []) if isinstance(p, dict)]
        return [p for p in (payload or []) if isinstance(p, dict)]

    def mount_pads(self, board_path: str = "",
                   min_hole_mils: float = 55.0) -> list[dict[str, Any]]:
        """读取板上的自由焊盘中的安装孔 / 安装焊盘。

        安装焊盘通常不属于任何器件、带较大的机械孔（有些还挂在某个网络上）。
        它们的电位由机械结构决定，需要在界面里人工指定。
        """
        if board_path:
            # 读取器件焊盘/网表时活动文档可能被切到原理图，这里重新聚焦 PCB
            try:
                self.focus_board(board_path)
            except EdaAgentError:
                pass
        try:
            payload = self._call(
                "obj_query",
                {
                    "object_type": "ePadObject",
                    "properties": "Net,Layer,X,Y,TopXSize,TopYSize,HoleSize,Name,InComponent",
                    "scope": "active_doc",
                    "filter": "InComponent=false",
                    "limit": 1000,
                },
                timeout=120,
            )
        except EdaAgentError:
            return []
        objects = payload.get("objects") if isinstance(payload, dict) else None
        mounts: list[dict[str, Any]] = []
        for index, obj in enumerate(objects or [], start=1):
            if not isinstance(obj, dict):
                continue
            hole_mils = _to_float(obj.get("HoleSize"))
            if hole_mils < min_hole_mils:
                continue
            size_x = _to_float(obj.get("TopXSize"))
            size_y = _to_float(obj.get("TopYSize"))
            mounts.append(
                {
                    "name": f"MH{index}",
                    "x": mm(obj.get("X")),
                    "y": mm(obj.get("Y")),
                    "width": max(size_x * MIL_TO_MM, hole_mils * MIL_TO_MM),
                    "height": max(size_y * MIL_TO_MM, hole_mils * MIL_TO_MM),
                    "hole": hole_mils * MIL_TO_MM,
                    "net": _text(obj.get("Net")),
                    "layer": _map_layer(_text(obj.get("Layer"))),
                    "source": "自由焊盘",
                }
            )
        return mounts

    @staticmethod
    def match_outline(cutouts: list[dict], outline: dict):
        """按中心 + 尺寸把读到的轮廓配到某个挖空上。"""
        best = None
        best_score = 1e9
        for cut in cutouts:
            distance = ((cut["x"] - outline["x"]) ** 2 + (cut["y"] - outline["y"]) ** 2) ** 0.5
            size_delta = abs(cut["width"] - outline["width"]) + abs(
                cut["height"] - outline["height"]
            )
            if distance > 1.0 or size_delta > 1.0:
                continue
            score = distance + size_delta * 0.5
            if score < best_score:
                best, best_score = cut, score
        return best

    @staticmethod
    def _filter_cutouts(cutouts: list[dict], outline: list[tuple[float, float]], board) -> list[dict]:
        """剔除「板框本身」和「器件封装（含磁芯）内的挖空」，只留板级避距槽。"""
        return filter_cutouts(cutouts, outline, board)

    def board_outline(self, board_path: str = "", arc_step_deg: float = 6.0) -> list[tuple[float, float]]:
        """读取板框外形（line + arc），圆弧按角度离散成多边形，单位 mm。"""
        if board_path:
            try:
                self.focus_board(board_path)
            except EdaAgentError:
                pass
        try:
            payload = self._call("pcb_get_board_outline", {}, timeout=120)
        except EdaAgentError:
            return []
        vertices = payload.get("vertices") if isinstance(payload, dict) else None
        if not vertices:
            return []

        points: list[list[tuple[float, float]]] = []
        for index, vertex in enumerate(vertices):
            x = mm(vertex.get("x"))
            y = mm(vertex.get("y"))
            if vertex.get("kind") != "arc":
                points.append([(x, y)])
                continue
            # 圆弧：从当前顶点画到下一个顶点，圆心与起止角由 AD 给出
            nxt = vertices[(index + 1) % len(vertices)]
            nx, ny = mm(nxt.get("x")), mm(nxt.get("y"))
            cx, cy = mm(vertex.get("cx")), mm(vertex.get("cy"))
            radius = math.hypot(x - cx, y - cy)
            if radius < 1e-6:
                points.append([(x, y)])
                continue
            start = math.degrees(math.atan2(y - cy, x - cx))
            end = math.degrees(math.atan2(ny - cy, nx - cx))
            sweep = (end - start) % 360.0
            steps = max(1, int(round(sweep / arc_step_deg)))
            samples = [
                (
                    cx + radius * math.cos(math.radians(start + sweep * i / steps)),
                    cy + radius * math.sin(math.radians(start + sweep * i / steps)),
                )
                for i in range(steps + 1)
            ]
            points.append(samples)

        outline: list[tuple[float, float]] = []
        for segment in points:
            for point in segment:
                if not outline or math.hypot(
                    point[0] - outline[-1][0], point[1] - outline[-1][1]
                ) > 1e-4:
                    outline.append(point)
        return outline

    def board_cutouts(self, board_path: str = "") -> list[dict[str, Any]]:
        """读取板级挖空（eRegionObject 且 Kind=board_cutout）。"""
        if board_path:
            try:
                self.focus_board(board_path)
            except EdaAgentError:
                pass
        try:
            payload = self._call(
                "obj_query",
                {
                    "object_type": "eRegionObject",
                    "properties": "Kind,BoundsLeft,BoundsBottom,BoundsRight,BoundsTop",
                    "scope": "active_doc",
                    "limit": 500,
                },
                timeout=120,
            )
        except EdaAgentError:
            return []
        objects = payload.get("objects") if isinstance(payload, dict) else None
        cutouts: list[dict[str, Any]] = []
        for obj in objects or []:
            if not isinstance(obj, dict) or obj.get("Kind") != "board_cutout":
                continue
            x1 = mm(obj.get("BoundsLeft"))
            y1 = mm(obj.get("BoundsBottom"))
            x2 = mm(obj.get("BoundsRight"))
            y2 = mm(obj.get("BoundsTop"))
            width = abs(x2 - x1)
            height = abs(y2 - y1)
            if width < 0.1 or height < 0.1:
                continue
            circular = abs(width - height) <= max(0.05, 0.02 * max(width, height))
            cutouts.append(
                {
                    "kind": "circle" if circular else "slot",
                    "x": (x1 + x2) / 2,
                    "y": (y1 + y2) / 2,
                    "width": width,
                    "height": height,
                }
            )
        # 大槽优先：避免小圆孔把大避距槽挡住
        cutouts.sort(key=lambda item: item["width"] * item["height"], reverse=True)
        return cutouts

    def cutout_outlines(
        self, board_path: str = "", page: int = 800
    ) -> list[dict[str, Any]]:
        """读取每个板级挖空的闭合轮廓顶点（mm）。

        需要遍历整块板的铜箔/区域（`pcb_get_copper` 分页），大板约 20~30 秒，
        所以只在用户点「读取轮廓坐标」时调用，不放进常规扫描。
        """
        if board_path:
            try:
                self.focus_board(board_path)
            except EdaAgentError:
                pass
        outlines: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self._call(
                "pcb_get_copper",
                {"limit": page, "offset": offset},
                timeout=300,
            )
            if not isinstance(payload, dict):
                break
            per = float(payload.get("coord_per_mil") or 10000.0)
            items = payload.get("copper") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("k") != "region" or not item.get("cutout"):
                    continue
                points = [
                    (float(x) / per * MIL_TO_MM, float(y) / per * MIL_TO_MM)
                    for x, y in (item.get("pts") or [])
                ]
                if len(points) < 3:
                    continue
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                outlines.append(
                    {
                        "points": points,
                        "x": (min(xs) + max(xs)) / 2,
                        "y": (min(ys) + max(ys)) / 2,
                        "width": max(xs) - min(xs),
                        "height": max(ys) - min(ys),
                    }
                )
            offset += len(items)
            if not items or not payload.get("more"):
                break
        return outlines

    def sheet_layout(
        self,
        project_path: str = "",
        progress: Callable[[str, str], None] | None = None,
    ) -> tuple[dict[str, list[str]], dict[str, str], dict[str, str], str]:
        """读取图纸布局。

        返回 ``(图纸->器件位号, 图纸->路径, 编译实例名->图纸, 顶层图纸)``。
        多通道设计里编译网表的位号带实例后缀（如 ``Q9_U_SR_Cout14``），
        需要用编译实例名把网络归到正确的图纸上。

        使用 ``obj_query(scope=doc:<path>)``，不会切换 Altium 当前打开的图纸。
        """
        documents = self.list_documents(project_path)
        sheets: list[tuple[str, str]] = []
        for doc in documents:
            path = _text(doc.get("file_path") or doc.get("path"))
            kind = _text(doc.get("document_kind")).upper()
            if not path:
                continue
            if kind == "SCH" or path.lower().endswith(".schdoc"):
                sheets.append((path, _text(doc.get("file_name")) or Path(path).name))

        names: dict[str, int] = {}
        result: dict[str, list[str]] = {}
        paths: dict[str, str] = {}
        for path, file_name in sheets:
            base = file_name[:-7] if file_name.lower().endswith(".schdoc") else Path(file_name).stem
            name = base or Path(path).stem
            if name in names:
                names[name] += 1
                name = f"{name} ({names[name]})"
            else:
                names[name] = 1
            if progress is not None:
                progress("图纸", f"读取 {name} 的器件…")
            try:
                payload = self._call(
                    "obj_query",
                    {
                        "object_type": "eSchComponent",
                        "properties": "Designator.Text",
                        "scope": f"doc:{path}",
                        "limit": 0,
                    },
                    timeout=90,
                )
            except EdaAgentError as exc:
                self.last_error = str(exc)
                continue
            objects = payload.get("objects") if isinstance(payload, dict) else None
            designators: list[str] = []
            seen: set[str] = set()
            for obj in objects or []:
                if not isinstance(obj, dict):
                    continue
                designator = _text(obj.get("Designator.Text") or obj.get("Designator")).strip()
                if designator and designator not in seen:
                    seen.add(designator)
                    designators.append(designator)
            result[name] = designators
            paths[name] = path

        instance_sheets: dict[str, str] = {}
        top_sheet = ""
        try:
            hierarchy = self._call(
                "proj_get_hierarchy",
                {"project_path": project_path} if project_path else {},
                timeout=180,
            )
        except EdaAgentError:
            hierarchy = {}
        if isinstance(hierarchy, dict):
            file_to_name = {Path(p).name.lower(): name for name, p in paths.items()}
            for sheet in hierarchy.get("sheets") or []:
                if not isinstance(sheet, dict):
                    continue
                file_name = _text(sheet.get("file"))
                display = file_to_name.get(file_name.lower())
                if not display:
                    continue
                for port in sheet.get("ports") or []:
                    if not isinstance(port, dict):
                        continue
                    for physical in port.get("physical") or []:
                        if not isinstance(physical, dict):
                            continue
                        instance = _text(physical.get("instance"))
                        if instance:
                            instance_sheets[instance] = display
            top_file = _text(hierarchy.get("top"))
            top_sheet = file_to_name.get(top_file.lower(), "")

        return result, paths, instance_sheets, top_sheet

    # ---- 组装模型 -------------------------------------------------
    def scan(
        self,
        project_path: str = "",
        board_path: str = "",
        with_shapes: bool = False,
        with_netlist: bool = True,
        with_sheets: bool = True,
        with_mounts: bool = True,
        progress: Callable[[str, str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> tuple[Board, ScanReport]:
        """读取当前 Altium 设计，组装成 Board。

        progress(stage, detail) 用于界面展示进度；should_stop() 返回 True 时
        在步骤之间中止（大板读取器件本身要 20~30 秒，无法中断单次调用）。
        """

        def report_progress(stage: str, detail: str = "") -> None:
            if progress is not None:
                progress(stage, detail)

        def stopped() -> bool:
            return bool(should_stop and should_stop())

        report = ScanReport(project_path=project_path, board_path=board_path)
        report_progress("工程", "读取当前 Altium 工程信息…")

        if not project_path:
            focused = self.focused_project()
            project_path = focused.path if focused else ""
        report.project_path = project_path
        if stopped():
            raise EdaAgentError("扫描已取消")

        report_progress("文档", "列出工程文档…")
        boards = self.board_documents(project_path)
        if board_path:
            boards = [board_path]
        if not boards:
            raise EdaAgentError("工程中没有找到 .PcbDoc 文档，请先在 Altium 中打开 PCB 文件")
        board_path = boards[0]
        report.board_path = board_path
        self.focus_board(board_path)
        report.steps.append(f"定位 PCB 文档: {Path(board_path).name}")
        if stopped():
            raise EdaAgentError("扫描已取消")

        board = Board(
            name=Path(board_path).stem,
            source_path=board_path,
            source_format="Altium Designer（EDA Agent 实时读取）",
        )

        report_progress("器件", "读取器件列表（大板在 Altium 侧需 20~30 秒）…")
        components = self.components()
        for item in components:
            designator = _text(item.get("designator"))
            if not designator:
                continue
            board.add_component(
                Component(
                    designator=designator,
                    comment=_text(item.get("comment")),
                    footprint=_text(item.get("footprint")),
                    x=mm(item.get("x")),
                    y=mm(item.get("y")),
                    rotation=float(item.get("rotation") or 0.0),
                    layer=_text(item.get("layer") or "TOP"),
                    bbox=_component_bbox(item),
                    category=_guess_category(item),
                )
            )
        report.component_count = len(board.components)
        report.steps.append(f"读取器件 {len(board.components)} 个")
        if stopped():
            raise EdaAgentError("扫描已取消")

        report_progress("网络", "读取网络列表…")
        net_names = self.board_nets()
        for name in net_names:
            board.ensure_net(name)

        report_progress("焊盘", f"批量读取 {len(board.components)} 个器件的焊盘…")
        pads_by_component = self.component_pads(board.components.keys())
        shapes: dict[tuple[float, float], str] = {}
        if with_shapes and net_names and not stopped():
            report_progress("焊盘外形", "读取焊盘外形（耗时较长）…")
            shapes = self.pad_shapes_by_net(net_names)

        for designator, pads in pads_by_component.items():
            for raw in pads:
                net = _text(raw.get("net"))
                if net:
                    board.ensure_net(net)
                x = mm(raw.get("x"))
                y = mm(raw.get("y"))
                width = mm(raw.get("top_x_size"), 0.0) or mm(raw.get("x_size"), 0.0)
                height = mm(raw.get("top_y_size"), 0.0) or mm(raw.get("y_size"), 0.0)
                pad = Pad(
                    name=_text(raw.get("name")),
                    net=net,
                    component=designator,
                    x=x,
                    y=y,
                    width=max(width, 0.01),
                    height=max(height, 0.01),
                    rotation=float(raw.get("rotation") or 0.0),
                    shape=shapes.get((round(x, 2), round(y, 2)), "rect"),
                    layer=_map_layer(_text(raw.get("layer"))),
                    hole=mm(raw.get("hole_size")),
                    plated=not bool(raw.get("is_smd")),
                )
                board.add_pad(pad)
        if with_mounts and not stopped():
            report_progress("安装焊盘", "识别安装孔 / 安装焊盘…")
            kept: list[Pad] = []
            for pad in board.pads:
                if not pad.net and pad.hole >= 1.5:
                    report.mount_pads.append(
                        {
                            "name": pad.component or f"MH{len(report.mount_pads) + 1}",
                            "x": pad.x,
                            "y": pad.y,
                            "width": pad.width,
                            "height": pad.height,
                            "hole": pad.hole,
                            "net": pad.net,
                            "layer": pad.layer,
                            "source": "器件焊盘",
                        }
                    )
                else:
                    kept.append(pad)
            board.pads = kept
            report.mount_pads.extend(self.mount_pads(board_path))

            # 板级挖空：圆形 → 安装孔；多边形 → 避距槽（区分电气间隙 / 爬电距离）
            report_progress("挖空", "读取板级挖空（圆形 / 避距槽）…")
            outline = self.board_outline(board_path)
            board.outline = outline
            if len(outline) >= 3:
                x0, y0, x1, y1 = board.board_bbox()
                report.steps.append(
                    f"读取板框外形 {len(outline)} 点（{x1 - x0:.2f} × {y1 - y0:.2f} mm）"
                )

            cutouts = self.board_cutouts(board_path)
            if cutouts:
                frame = None
                for cut in cutouts:
                    xs = [point[0] for point in outline] or [0.0]
                    ys = [point[1] for point in outline] or [0.0]
                    if len(outline) >= 3 and (
                        abs(cut["width"] - (max(xs) - min(xs))) < 2.0
                        and abs(cut["height"] - (max(ys) - min(ys))) < 2.0
                    ):
                        frame = cut
                        break
                cutouts = classify_cutouts(cutouts, outline, board)
                roles = [item.get("role") for item in cutouts]
                report.steps.append(
                    "挖空归属：板框 %d / 器件封装 %d / 爬电隔离 %d"
                    % (
                        roles.count("frame"),
                        roles.count("footprint"),
                        roles.count("creepage"),
                    )
                )
                # 板框挖空直接复用板框的真实轮廓（无需再单独读）
                if len(outline) >= 3:
                    for item in cutouts:
                        if item.get("role") == "frame":
                            item["points"] = list(outline)
                            break

                # 爬电隔离槽：读一次整板铜箔，取每条槽的闭合端点用于精确绘制
                creepage = [item for item in cutouts if item.get("role") == "creepage"]
                if creepage:
                    report_progress("挖空轮廓", "读取挖空闭合端点（约 20~30 秒）…")
                    for outline_item in self.cutout_outlines(board_path):
                        target = self.match_outline(creepage, outline_item)
                        if target is not None:
                            target["points"] = list(outline_item["points"])
                    report.steps.append(
                        "挖空端点：%d/%d 条已取到闭合坐标"
                        % (sum(1 for c in creepage if c.get("points")), len(creepage))
                    )

                if len(outline) < 3 and frame is not None:
                    hw, hh = frame["width"] / 2, frame["height"] / 2
                    board.outline = [
                        (frame["x"] - hw, frame["y"] - hh),
                        (frame["x"] + hw, frame["y"] - hh),
                        (frame["x"] + hw, frame["y"] + hh),
                        (frame["x"] - hw, frame["y"] + hh),
                    ]
                    report.steps.append("板框接口无数据，改用挖空外框近似板框")
            report.cutouts = cutouts
            circles = [c for c in cutouts if c["kind"] == "circle"]
            # 落在器件封装内的圆孔属于该封装自带（如 T21 的中心孔），不是安装孔
            footprint_holes = [c for c in circles if c.get("role") == "footprint"]
            circles = [c for c in circles if c.get("role") != "footprint"]
            if footprint_holes:
                report.steps.append(
                    "封装自带圆孔 %d 个未计入安装焊盘" % len(footprint_holes)
                )
            for index, cut in enumerate(circles, start=1):
                diameter = max(cut["width"], cut["height"])
                report.mount_pads.append(
                    {
                        "name": f"CH{index}",
                        "x": cut["x"],
                        "y": cut["y"],
                        "width": cut["width"],
                        "height": cut["height"],
                        "hole": diameter,
                        "net": "",
                        "layer": "MULTI",
                        "source": f"板级挖空（圆形 Ø{diameter:.2f} mm）",
                    }
                )
            slots = [c for c in cutouts if c["kind"] != "circle"]
            if cutouts:
                report.steps.append(
                    f"读取板级挖空 {len(cutouts)} 个"
                    f"（圆形安装孔 {len(circles)} / 避距槽 {len(slots)}"
                    f" / 封装内圆孔 {len(footprint_holes)}）"
                )
            if report.mount_pads:
                report.steps.append(f"识别安装焊盘 {len(report.mount_pads)} 个")

        # 无网络器件：所有焊盘都没有网络，或者干脆没有焊盘（磁芯 / 结构件）
        if not stopped():
            by_component: dict[str, list[Any]] = {}
            for pad in board.pads:
                if pad.component:
                    by_component.setdefault(pad.component, []).append(pad)
            floating: list[dict[str, Any]] = []
            for designator, comp in board.components.items():
                pads = by_component.get(designator, [])
                if pads and any(pad.net for pad in pads):
                    continue
                center = comp.bbox_center
                size = comp.bbox_size
                floating.append(
                    {
                        "designator": designator,
                        "comment": comp.comment,
                        "footprint": comp.footprint,
                        "x": center[0] if center else comp.x,
                        "y": center[1] if center else comp.y,
                        "width": size[0] if size else 0.0,
                        "height": size[1] if size else 0.0,
                        "layer": comp.layer,
                        "pad_count": len(pads) or comp.pad_count,
                    }
                )
            report.floating_parts = floating
            if floating:
                report.steps.append(f"识别无网络器件 {len(floating)} 个")

        report.pad_count = len(board.pads)
        report.steps.append(f"读取焊盘 {len(board.pads)} 个")

        if not board.pads:
            report.warnings.append("没有读到任何带网络的焊盘，请确认 PCB 已完成网络分配")

        for comp in board.components.values():
            comp.pad_count = len(board.pads_of_component(comp.designator))

        if with_netlist and not stopped():
            report_progress("网表", "读取原理图编译网表（引脚名用于拓扑推断）…")
            report.pins = self.netlist_pins()
            if report.pins:
                report.steps.append(f"载入原理图引脚 {len(report.pins)} 条")

        if with_sheets and not stopped():
            report_progress("图纸", "读取各原理图图纸的器件归属…")
            (
                report.sheet_components,
                report.sheet_paths,
                report.instance_sheets,
                report.top_sheet,
            ) = self.sheet_layout(project_path, progress=progress)
            if report.sheet_components:
                report.steps.append(
                    f"读取图纸 {len(report.sheet_components)} 张"
                    f"（编译实例 {len(report.instance_sheets)} 个）"
                )

        report.net_count = len([n for n in board.nets if n])
        report.steps.append(f"提取网络 {report.net_count} 个")
        report_progress("完成", f"器件 {report.component_count} / 焊盘 {report.pad_count} / 网络 {report.net_count}")
        return board, report


def _bulk_items(payload: Any) -> list[Any]:
    """从批量 / 单个响应里取出按顺序的结果列表。"""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "items", "batch", "responses"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]
    return []


def _extract_pads(entry: Any) -> list[dict[str, Any]]:
    if isinstance(entry, list):
        return [p for p in entry if isinstance(p, dict)]
    if not isinstance(entry, dict):
        return []
    pads = entry.get("pads")
    if isinstance(pads, list):
        return [p for p in pads if isinstance(p, dict)]
    result = entry.get("result")
    if isinstance(result, dict) and isinstance(result.get("pads"), list):
        return [p for p in result["pads"] if isinstance(p, dict)]
    if "name" in entry and ("x" in entry or "top_x_size" in entry):
        return [entry]
    return []


def _map_layer(layer: str) -> str:
    text = (layer or "").strip().lower()
    if "bottom" in text or text in ("b", "32"):
        return "BOTTOM"
    if "multi" in text or "all" in text:
        return "MULTI"
    return "TOP"


def _map_shape(shape: str) -> str:
    text = (shape or "").lower()
    if "round" in text and "rect" in text:
        return "roundrect"
    if "round" in text or "circle" in text:
        return "round"
    if "oct" in text:
        return "octagon"
    return "rect"


def default_agent_root() -> Path:
    return Path(os.environ.get("EDA_AGENT_ROOT", r"E:\AI_model\eda-agent-0.8.1"))
