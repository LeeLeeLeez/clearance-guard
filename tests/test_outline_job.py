"""「读取轮廓坐标」按中心/尺寸把轮廓匹配到避距槽。"""

import api.edit_service as es
from api.store import TaskState
from clearance_check.eda.session import EdaAgentSession
from clearance_check.model import Board, BoardCutout


class _FakeSession:
    def cutout_outlines(self, board_path=""):
        return [{
            "x": 10.0, "y": 10.0, "width": 2.0, "height": 4.0,
            "points": [(9.0, 8.0), (11.0, 8.0), (11.0, 12.0), (9.0, 12.0)],
        }]

    match_outline = staticmethod(EdaAgentSession.match_outline)


def test_outline_job_matches_dict_outline_to_cutout():
    board = Board(name="outline")
    cut = BoardCutout(kind="slot", x=10.0, y=10.0, width=2.0, height=4.0,
                      role="creepage")
    board.cutouts = [cut]

    saved = (es.STATE.board, es.STATE.session, es.STATE.task)
    try:
        es.STATE.board = board
        es.STATE.session = _FakeSession()
        es.STATE.task = TaskState(running=True, kind="outlines")
        es._outline_job("")
        assert es.STATE.task.finished and not es.STATE.task.error
        assert len(cut.points) == 4
        assert cut.points[0] == (9.0, 8.0)
    finally:
        es.STATE.board, es.STATE.session, es.STATE.task = saved
