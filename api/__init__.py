"""Clearance Guard 的本地 Web 服务层。

把原来 PySide6 界面里的动作（连接/扫描/分类/规则/校验/布局求解）拆成可被 HTTP 调用的
服务函数。**判定逻辑一行不改** —— 直接复用 `clearance_check.analysis` / `eda` /
`snapshot` 里已有的纯函数，这里只做「状态持有 + 编排 + 序列化」。
"""

from . import store  # noqa: F401
from . import dialog_service  # noqa: F401
from . import scan_service  # noqa: F401
from . import edit_service  # noqa: F401
from . import result_service  # noqa: F401

__all__ = ["store", "dialog_service", "scan_service", "edit_service", "result_service"]
