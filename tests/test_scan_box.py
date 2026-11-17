import pytest

from api.scan_service import ServiceError, crop_board, normalize_scan_box
from clearance_check.model import Board, Component, MountPoint, Pad, Track


def _board() -> Board:
    b = Board(name="scanbox")
    b.outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    b.add_component(Component(designator="C1", x=2.0, y=2.0, bbox=(1.0, 1.0, 3.0, 3.0)))
    b.add_component(Component(designator="C2", x=8.0, y=8.0, bbox=(7.0, 7.0, 9.0, 9.0)))
    b.add_pad(Pad(name="p1", net="A", component="C1", x=2.0, y=2.0,
                  width=1.0, height=1.0))
    b.add_pad(Pad(name="p2", net="B", component="C2", x=8.0, y=8.0,
                  width=1.0, height=1.0))
    b.mounts = [MountPoint(name="MH1", x=2.0, y=8.0, width=3.5, height=3.5,
                           hole=1.8, net="SGND")]
    b.tracks = [
        Track(net="A", x1=0.5, y1=0.5, x2=4.0, y2=0.5),
        Track(net="B", x1=6.0, y1=9.0, x2=9.0, y2=9.0),
    ]
    return b


def test_crop_keeps_only_inside():
    board = _board()
    box, clamped, bbox = normalize_scan_box({"x1": 0, "y1": 0, "x2": 5, "y2": 5}, board)
    assert box == (0.0, 0.0, 5.0, 5.0)
    assert clamped is False
    assert bbox == (0.0, 0.0, 10.0, 10.0)

    out, counts = crop_board(board, box)
    assert set(out.components) == {"C1"}
    assert [p.net for p in out.pads] == ["A"]
    assert out.mounts == []
    assert [t.net for t in out.tracks] == ["A"]
    assert out.nets == ["A"]
    assert out.outline == board.outline
    assert counts["components"] == 1 and counts["pads"] == 1


def test_crop_keeps_mount_that_intersects():
    board = _board()
    box, _, _ = normalize_scan_box({"x1": 0, "y1": 6, "x2": 5, "y2": 10}, board)
    out, _ = crop_board(board, box)
    assert [m.name for m in out.mounts] == ["MH1"]
    assert "SGND" in out.nets


def test_scan_box_clamped_to_board():
    board = _board()
    box, clamped, _ = normalize_scan_box({"x1": -5, "y1": -5, "x2": 20, "y2": 20}, board)
    assert box == (0.0, 0.0, 10.0, 10.0)
    assert clamped is True


def test_scan_box_too_small_or_outside():
    board = _board()
    with pytest.raises(ServiceError):
        normalize_scan_box({"x1": 0, "y1": 0, "x2": 0.5, "y2": 0.5}, board)
    with pytest.raises(ServiceError):
        normalize_scan_box({"x1": 20, "y1": 20, "x2": 30, "y2": 30}, board)
