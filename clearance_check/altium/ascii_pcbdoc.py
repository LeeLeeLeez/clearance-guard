"""Altium PCB ASCII 格式（File -> Save As -> PCB ASCII）解析。

ASCII 文件由若干 `|KEY=VALUE|KEY=VALUE|...` 记录行组成，记录类型由 RECORD 字段
标识；焊盘通过 NET/COMPONENT 索引引用文件开头的网络表和器件表。
"""

from __future__ import annotations

import os
import re

from ..model import Board, Component, Pad, Track

_RECORD_RE = re.compile(r"\|([^|=]+)=([^|]*)")

_UNIT_FACTORS = {
    "mil": 0.0254,
    "mm": 1.0,
    "cm": 10.0,
    "in": 25.4,
    "inch": 25.4,
    "um": 0.001,
    "nm": 1e-6,
    "": 0.0254,  # 无单位时按 mil 处理
}

_SHAPE_MAP = {
    "0": "rect",
    "1": "round",
    "2": "rect",
    "3": "octagon",
    "8": "rect",
    "9": "round",
    "ROUND": "round",
    "CIRCLE": "round",
    "RECT": "rect",
    "RECTANGULAR": "rect",
    "OCTAGON": "octagon",
    "OCTAGONAL": "octagon",
}

_LAYER_MAP = {
    "1": "TOP",
    "32": "BOTTOM",
    "74": "MULTI",
    "TOP": "TOP",
    "TOPLAYER": "TOP",
    "BOTTOM": "BOTTOM",
    "BOTTOMLAYER": "BOTTOM",
    "MULTILAYER": "MULTI",
}


def parse_length(raw, default_unit: str = "mil") -> float:
    """解析带单位长度值（`100mil` / `2.54mm` / `0.1in` / 裸数字）。"""
    if raw is None:
        return 0.0
    s = str(raw).strip().strip('"').strip("'").replace(" ", "")
    if not s:
        return 0.0
    m = re.match(r"^([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?\d+)?)([A-Za-z]*)$", s)
    if not m:
        return 0.0
    value = float(m.group(1))
    unit = m.group(2).lower()
    factor = _UNIT_FACTORS.get(unit, _UNIT_FACTORS.get(default_unit, 0.0254))
    return value * factor


def parse_angle(raw) -> float:
    if raw is None:
        return 0.0
    try:
        return float(str(raw).strip().rstrip("deg"))
    except ValueError:
        return 0.0


def _parse_records(text: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        pairs: dict[str, str] = {}
        for match in _RECORD_RE.finditer(line):
            pairs[match.group(1).strip().upper()] = match.group(2).strip()
        if pairs:
            records.append(pairs)
    return records


def _first(rec: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = rec.get(key)
        if value not in (None, ""):
            return value
    return ""


def parse_ascii_pcbdoc(text: str, path: str = "") -> Board:
    records = _parse_records(text)
    if not records:
        raise ValueError("文件中没有识别到任何 PCB ASCII 记录")

    # ---- 网络表 / 器件表 -----------------------------------------
    net_names: list[str] = []
    for rec in records:
        if rec.get("RECORD", "").upper() == "NET":
            name = _first(rec, "NAME", "NETNAME", "NET")
            if name:
                net_names.append(name)

    comp_designators: list[str] = []
    comp_records: list[dict[str, str]] = []
    for rec in records:
        if rec.get("RECORD", "").upper() == "COMPONENT":
            dsg = _first(rec, "SOURCEDESIGNATOR", "DESIGNATOR", "NAME", "IDENTIFIER")
            if not dsg:
                dsg = f"COMP{len(comp_designators) + 1}"
            if dsg in comp_designators:  # 位号重复时加后缀，避免覆盖
                dsg = f"{dsg}#{len(comp_designators) + 1}"
            comp_designators.append(dsg)
            comp_records.append(rec)

    def resolve_net(value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        if value.lstrip("-").isdigit() and net_names:
            idx = int(value)
            if 0 <= idx < len(net_names):
                return net_names[idx]
        return value

    def resolve_component(value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        if value.lstrip("-").isdigit() and comp_designators:
            idx = int(value)
            if 0 <= idx < len(comp_designators):
                return comp_designators[idx]
        return value

    board = Board(
        name=os.path.splitext(os.path.basename(path))[0] if path else "PCB",
        source_path=path,
        source_format="Altium PcbDoc (ASCII)",
    )
    for name in net_names:
        board.ensure_net(name)

    # ---- 器件 ----------------------------------------------------
    for idx, (dsg, rec) in enumerate(zip(comp_designators, comp_records)):
        comp = Component(
            designator=dsg,
            comment=_first(rec, "COMMENT", "SOURCECOMMENT", "VALUE"),
            footprint=_first(rec, "PATTERN", "FOOTPRINT", "SOURCELIBREFERENCE"),
            x=parse_length(_first(rec, "X", "COMPONENTX")),
            y=parse_length(_first(rec, "Y", "COMPONENTY")),
            rotation=parse_angle(_first(rec, "ROTATION")),
            layer=_LAYER_MAP.get(_first(rec, "LAYER").upper(), "TOP"),
            height=parse_length(_first(rec, "HEIGHT", "OVERALLHEIGHT")),
        )
        board.add_component(comp)

    # ---- 焊盘 ----------------------------------------------------
    for rec in records:
        if rec.get("RECORD", "").upper() != "PAD":
            continue
        comp = resolve_component(rec.get("COMPONENT", ""))
        net = resolve_net(rec.get("NET", ""))

        size_x = _first(rec, "TOPXSIZE", "XSIZE", "SIZE", "WIDTH", "TOPX")
        size_y = _first(rec, "TOPYSIZE", "YSIZE", "SIZE", "HEIGHT", "TOPY")
        if not size_x and not size_y:
            # 某些导出只给 SIZE（圆焊盘）
            size_x = size_y = _first(rec, "SIZE")

        pad = Pad(
            name=_first(rec, "NAME", "PADNAME", "DESIGNATOR"),
            net=net,
            component=comp,
            x=parse_length(_first(rec, "X", "TOPX", "PADX")),
            y=parse_length(_first(rec, "Y", "TOPY", "PADY")),
            width=max(parse_length(size_x), 0.001),
            height=max(parse_length(size_y), 0.001),
            rotation=parse_angle(_first(rec, "ROTATION")),
            shape=_SHAPE_MAP.get(_first(rec, "SHAPE").upper(), "rect"),
            layer=_LAYER_MAP.get(_first(rec, "LAYER").upper(), "TOP"),
            hole=parse_length(_first(rec, "HOLESIZE", "HOLEWIDTH")),
            plated=_first(rec, "PLATED").upper() not in ("FALSE", "0"),
        )
        board.add_pad(pad)

    # ---- 走线 ----------------------------------------------------
    for rec in records:
        if rec.get("RECORD", "").upper() != "TRACK":
            continue
        net = resolve_net(rec.get("NET", ""))
        if not net:
            continue
        board.tracks.append(
            Track(
                net=net,
                component=resolve_component(rec.get("COMPONENT", "")),
                layer=_LAYER_MAP.get(_first(rec, "LAYER").upper(), "TOP"),
                x1=parse_length(rec.get("X1")),
                y1=parse_length(rec.get("Y1")),
                x2=parse_length(rec.get("X2")),
                y2=parse_length(rec.get("Y2")),
                width=max(parse_length(rec.get("WIDTH")), 0.05),
            )
        )

    _finalize(board, records)
    return board


def _finalize(board: Board, records: list[dict[str, str]]) -> None:
    """补齐板框尺寸与统计信息。"""
    for rec in records:
        if rec.get("RECORD", "").upper() == "BOARD":
            board.height = parse_length(_first(rec, "HEIGHT"))
            board.width = parse_length(_first(rec, "WIDTH"))
            break
    if board.width <= 0 or board.height <= 0:
        x0, y0, x1, y1 = board.board_bbox()
        board.width = max(board.width, x1 - x0)
        board.height = max(board.height, y1 - y0)

    for comp in board.components.values():
        comp.pad_count = len(board.pads_of_component(comp.designator))

    if not board.nets:
        board.warnings.append("未在文件中找到网络记录，网络信息可能不完整")
