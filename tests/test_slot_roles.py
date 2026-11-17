"""避距槽角色：兼容旧写法 slot，并排除整板板框。"""

from clearance_check.analysis.clearance import _creepage_slots
from clearance_check.model import Board, BoardCutout


def _board() -> Board:
    board = Board(name="slots")
    board.outline = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
    board.cutouts = [
        # 整板大小，即使被误标成 creepage 也不能当槽
        BoardCutout(kind="slot", x=50.0, y=25.0, width=100.0, height=50.0,
                    role="creepage"),
        # 旧前端写法 slot
        BoardCutout(kind="slot", x=10.0, y=10.0, width=10.0, height=3.0,
                    role="slot"),
        BoardCutout(kind="slot", x=20.0, y=20.0, width=5.0, height=2.0,
                    role="footprint"),
        BoardCutout(kind="circle", x=30.0, y=30.0, width=3.0, height=3.0,
                    role="creepage"),
    ]
    return board


def test_creepage_slots_skips_board_frame_and_accepts_slot_alias():
    slots = _creepage_slots(_board())
    assert len(slots) == 1
    assert (slots[0].x1, slots[0].y1, slots[0].x2, slots[0].y2) == (
        5.0, 8.5, 15.0, 11.5,
    )


def test_creepage_slots_keeps_legacy_empty_role():
    """旧快照里没打标签的槽仍按避距槽处理（历史行为）。"""
    board = _board()
    board.cutouts = [
        BoardCutout(kind="slot", x=10.0, y=10.0, width=10.0, height=3.0, role=""),
    ]
    assert len(_creepage_slots(board)) == 1


def test_objects_cutouts_hide_circles_and_keep_original_index():
    """圆形挖孔已作为安装焊盘显示，槽列表不重复列出；下标保持原数组位置。"""
    from api import edit_service
    from api.store import STATE
    from clearance_check.model import BoardCutout

    board = Board(name="list")
    board.cutouts = [
        BoardCutout(kind="slot", x=1.0, y=1.0, width=10.0, height=3.0, role="frame"),
        BoardCutout(kind="circle", x=5.0, y=5.0, width=3.5, height=3.5, role="creepage"),
        BoardCutout(kind="slot", x=2.0, y=2.0, width=2.0, height=1.0, role="creepage"),
    ]
    saved = STATE.board
    try:
        STATE.board = board
        rows = edit_service.objects()["cutouts"]
        assert [r["index"] for r in rows] == [0, 2]
        assert all(r["kind"] != "circle" for r in rows)
    finally:
        STATE.board = saved
