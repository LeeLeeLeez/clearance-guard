"""内置示例工程：一个隔离反激电源板，用于离线演示完整流程。

包含：原边母线（375 V）、开关节点（0~375 V 跳变）、驱动/采样网络、
副边 12 V / 5 V 输出、变压器与光耦隔离带，以及若干刻意做出的避距违规。
"""

from __future__ import annotations

from ..model import Board, Component, MountPoint, Pad

# (位号, 注释, 封装, x, y, 旋转, 层, [(焊盘名, 网络, dx, dy, w, h, 形状)])
_PLACEMENT: list[tuple] = [
    ("J1", "接线端子 2P", "TERM-2P", 6.0, 30.0, 0.0, "TOP", [
        ("1", "HV_DC_375V", 0.0, 3.0, 2.4, 2.4, "rect"),
        ("2", "PRI_GND", 0.0, -3.0, 2.4, 2.4, "rect"),
    ]),
    ("F1", "保险丝 5A", "FUSE-1206", 13.0, 37.0, 0.0, "TOP", [
        ("1", "HV_DC_375V", -1.6, 0.0, 1.6, 1.6, "rect"),
        ("2", "FUSE_OUT", 1.6, 0.0, 1.6, 1.6, "rect"),
    ]),
    ("RV1", "压敏电阻", "MOV-14D", 13.0, 24.0, 0.0, "TOP", [
        ("1", "FUSE_OUT", 0.0, 1.6, 2.0, 2.0, "rect"),
        ("2", "PRI_GND", 0.0, -1.6, 2.0, 2.0, "rect"),
    ]),
    ("C1", "150uF 450V", "CAP-18x35", 22.0, 42.0, 0.0, "TOP", [
        ("1", "FUSE_OUT", 0.0, 2.2, 3.4, 2.0, "round"),
        ("2", "PRI_GND", 0.0, -2.2, 3.4, 2.0, "round"),
    ]),
    ("R1", "100k 1W", "R-2512", 18.0, 33.0, 0.0, "TOP", [
        ("1", "FUSE_OUT", -1.8, 0.0, 1.6, 1.6, "rect"),
        ("2", "HV_SENSE", 1.8, 0.0, 1.6, 1.6, "rect"),
    ]),
    ("R2", "10k", "R-1206", 18.0, 28.0, 0.0, "TOP", [
        ("1", "HV_SENSE", 0.0, 1.6, 1.4, 1.4, "rect"),
        ("2", "PRI_GND", 0.0, -1.6, 1.4, 1.4, "rect"),
    ]),
    ("Q1", "SiC MOSFET", "TO-247", 30.0, 30.0, 0.0, "TOP", [
        ("D", "SW_NODE", 0.0, 4.2, 1.6, 3.0, "rect"),
        ("G", "GATE", 2.4, 4.0, 1.2, 1.2, "rect"),
        ("S", "PRI_GND", 2.4, 1.4, 1.2, 1.2, "rect"),
    ]),
    ("R3", "10R 栅极电阻", "R-0805", 36.6, 34.6, 0.0, "TOP", [
        ("1", "GATE", -0.9, 0.0, 1.0, 1.4, "rect"),
        ("2", "GATE_DRV", 0.9, 0.0, 1.0, 1.4, "rect"),
    ]),
    ("R4", "0.02R 采样", "R-2512", 31.9, 37.0, 0.0, "TOP", [
        ("1", "CS_PRI", 0.0, -1.2, 1.6, 1.8, "rect"),
        ("2", "PRI_GND", 0.0, 1.2, 1.6, 1.8, "rect"),
    ]),
    ("U1", "PWM 控制器", "SOIC-8", 30.0, 18.0, 0.0, "TOP", [
        ("1", "PRI_VCC_12V", -4.4, 2.0, 1.2, 1.6, "rect"),
        ("2", "FB_PRI", -4.4, 0.7, 1.2, 1.6, "rect"),
        ("3", "CS_PRI", -4.4, -0.7, 1.2, 1.6, "rect"),
        ("4", "PRI_GND", -4.4, -2.0, 1.2, 1.6, "rect"),
        ("5", "GATE_DRV", 4.4, -2.0, 1.2, 1.6, "rect"),
        ("6", "PRI_VCC_12V", 4.4, -0.7, 1.2, 1.6, "rect"),
        ("7", "PRI_GND", 4.4, 0.7, 1.2, 1.6, "rect"),
        ("8", "FB_PRI", 4.4, 2.0, 1.2, 1.6, "rect"),
    ]),
    ("T1", "反激变压器 90W", "XFMR-EE33", 50.0, 30.0, 0.0, "TOP", [
        ("1", "FUSE_OUT", -5.0, 4.0, 3.2, 3.2, "rect"),
        ("2", "SW_NODE", -5.0, -2.0, 3.2, 3.2, "rect"),
        ("3", "SEC_RAW", 4.6, -2.0, 3.2, 3.2, "rect"),
        ("4", "SEC_GND", 4.6, 4.0, 3.2, 3.2, "rect"),
    ]),
    ("D1", "肖特基 20A", "TO-220", 54.0, 25.0, 0.0, "TOP", [
        ("A", "SEC_RAW", -1.9, 0.0, 1.8, 2.4, "rect"),
        ("K", "SEC_+12V", 1.9, 0.0, 1.8, 2.4, "rect"),
    ]),
    ("C2", "2200uF 25V", "CAP-16x25", 72.0, 32.0, 0.0, "TOP", [
        ("1", "SEC_+12V", 0.0, 2.2, 3.6, 2.0, "round"),
        ("2", "SEC_GND", 0.0, -2.2, 3.6, 2.0, "round"),
    ]),
    ("C3", "10uF 25V", "CAP-1210", 78.0, 24.0, 0.0, "TOP", [
        ("1", "SEC_+12V", 0.0, 1.5, 2.2, 1.6, "rect"),
        ("2", "SEC_GND", 0.0, -1.5, 2.2, 1.6, "rect"),
    ]),
    ("U3", "LDO 5V", "SOT-223", 85.0, 20.0, 0.0, "TOP", [
        ("1", "SEC_+12V", -3.2, 0.0, 1.6, 1.6, "rect"),
        ("2", "SEC_GND", 0.0, -3.2, 2.6, 1.2, "rect"),
        ("3", "SEC_+5V", 3.2, 0.0, 1.6, 1.6, "rect"),
    ]),
    ("R5", "22k", "R-1206", 70.0, 45.0, 0.0, "TOP", [
        ("1", "SEC_+12V", -1.5, 0.0, 1.4, 1.4, "rect"),
        ("2", "SEC_FB", 1.5, 0.0, 1.4, 1.4, "rect"),
    ]),
    ("R6", "4.7k", "R-1206", 70.0, 41.0, 0.0, "TOP", [
        ("1", "SEC_FB", 0.0, 1.5, 1.4, 1.4, "rect"),
        ("2", "SEC_GND", 0.0, -1.5, 1.4, 1.4, "rect"),
    ]),
    ("U2", "光耦 PC817", "DIP-4", 50.0, 12.0, 0.0, "TOP", [
        ("1", "FB_PRI", -4.2, 1.4, 1.6, 1.6, "rect"),
        ("2", "PRI_GND", -4.2, -1.4, 1.6, 1.6, "rect"),
        ("3", "SEC_FB", 4.2, -1.4, 1.6, 1.6, "rect"),
        ("4", "SEC_GND", 4.2, 1.4, 1.6, 1.6, "rect"),
    ]),
    ("R7", "1k", "R-0805", 42.0, 12.0, 0.0, "TOP", [
        ("1", "FB_PRI", -0.9, 0.0, 1.0, 1.2, "rect"),
        ("2", "PRI_VCC_12V", 0.9, 0.0, 1.0, 1.2, "rect"),
    ]),
    ("TP1", "测试点", "TP-1P", 40.0, 50.0, 0.0, "TOP", [
        ("1", "NetTP1", 0.0, 0.0, 2.0, 2.0, "round"),
    ]),
]

_PIN_NAMES = {
    "Q1": {"D": "D", "G": "G", "S": "S"},
    "D1": {"A": "A", "K": "K"},
    "U1": {"1": "VCC", "2": "FB", "3": "CS", "4": "GND",
           "5": "DRV", "6": "VCC", "7": "GND", "8": "COMP"},
}


def build_demo_board() -> tuple[Board, list[dict]]:
    board = Board(
        name="DEMO_Flyback_90W",
        width=100.0,
        height=62.0,
        source_path="(内置示例)",
        source_format="内置示例工程",
    )

    for designator, comment, footprint, x, y, rotation, layer, pads in _PLACEMENT:
        board.add_component(
            Component(
                designator=designator,
                comment=comment,
                footprint=footprint,
                x=x,
                y=y,
                rotation=rotation,
                layer=layer,
            )
        )
        for pad_name, net, dx, dy, width, height, shape in pads:
            board.add_pad(
                Pad(
                    name=pad_name,
                    net=net,
                    component=designator,
                    x=x + dx,
                    y=y + dy,
                    width=width,
                    height=height,
                    rotation=0.0,
                    shape=shape,
                    layer=layer,
                    hole=0.0 if shape != "round" else 0.0,
                    plated=False,
                )
            )
        board.components[designator].pad_count = len(pads)

    # 安装孔：连接机械结构，电位需要在界面中指定
    for name, x, y in (("MH1", 8.0, 8.0), ("MH2", 92.0, 54.0)):
        board.mounts.append(
            MountPoint(
                name=name,
                x=x,
                y=y,
                width=8.0,
                height=8.0,
                hole=3.2,
                net="",
                layer="MULTI",
                note="示例安装孔（M3）",
            )
        )

    pins: list[dict] = []
    for designator, _comment, _fp, _x, _y, _rot, _layer, pads in _PLACEMENT:
        names = _PIN_NAMES.get(designator, {})
        for pad_name, net, *_rest in pads:
            pins.append(
                {
                    "component": designator,
                    "pin": pad_name,
                    "pin_name": names.get(pad_name, pad_name),
                    "net": net,
                }
            )

    return board, pins


def build_demo_project_name() -> str:
    return "内置示例：隔离反激电源 90W（DEMO_Flyback_90W）"


# 示例工程的图纸划分（图纸名 -> 器件位号）
_SHEET_LAYOUT: dict[str, list[str]] = {
    "VPDB_main": ["J1", "F1", "RV1", "C1", "R1", "R2", "Q1", "T1", "D1", "C2"],
    "Pri_Driver": ["R3", "R4", "U1"],
    "Aux_Power": ["R7"],
    "SR_Cout": ["C3", "U3"],
    "Sense": ["R5", "R6", "U2", "TP1"],
}


def build_demo_sheets() -> tuple[dict[str, list[str]], dict[str, str]]:
    """返回示例工程的图纸 -> 器件映射（与真实工程的数据结构一致）。"""
    sheets = {name: list(designators) for name, designators in _SHEET_LAYOUT.items()}
    paths = {name: f"DEMO/{name}.SchDoc" for name in sheets}
    return sheets, paths
