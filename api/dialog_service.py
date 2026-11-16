"""Windows 原生文件对话框（服务端弹出）。

浏览器拿不到真实文件路径，但本工具的后端跑在本机 —— 所以「另存为 / 打开」交给服务端
用 tkinter 调系统对话框：前端点按钮 → 后端弹资源管理器风格窗口 → 拿到绝对路径 → 直接读写。

⚠️ tkinter 要求**所有调用都在同一个线程**，而 HTTP 请求是各自线程的。
所以这里起一条**专用 Tk 线程**（`start()` 在服务启动时调一次）：
请求把「要弹什么」投进队列，Tk 线程弹窗、把结果放回，请求线程等结果。
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
from typing import Any

from .store import STATE, snapshot_dir, state_dir

_QUEUE: "queue.Queue[Any]" = queue.Queue()
_READY = threading.Event()
_FAILED: list[str] = []
_LOCK = threading.Lock()


# ---------------------------------------------------------------- Tk 线程
def _tk_loop() -> None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001
        _FAILED.append(f"这台机器的 Python 没有 tkinter：{exc}")
        _READY.set()
        return

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:  # noqa: BLE001
        pass
    _READY.set()

    while True:
        job = _QUEUE.get()
        if job is None:
            break
        builder, box = job
        try:
            box["value"] = builder(root, filedialog)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            box["done"].set()
    try:
        root.destroy()
    except Exception:  # noqa: BLE001
        pass


def start() -> None:
    """服务启动时调一次：拉起 Tk 线程并等它就绪（失败也不阻塞启动）。"""
    if _READY.is_set():
        return
    threading.Thread(target=_tk_loop, daemon=True, name="tk-dialogs").start()
    ok = _READY.wait(timeout=8)
    if not ok:
        _FAILED.append("tkinter 初始化超时（系统对话框可能不可用）")
    if _FAILED:
        STATE.log("文件对话框不可用：" + "；".join(_FAILED))


def available() -> bool:
    return _READY.is_set() and not _FAILED


# ---------------------------------------------------------------- 内部
def _filetypes(pairs: list[tuple[str, str]] | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for pattern in pairs or []:
        out.append(pattern)
    out.append(("所有文件", "*.*"))
    return out


def _dispatch(builder: Any, kind: str) -> dict[str, Any]:
    if not available():
        raise RuntimeError(
            "服务端的文件对话框不可用（" + ("；".join(_FAILED) or "tkinter 未就绪") + "）"
        )
    box: dict[str, Any] = {"done": threading.Event(), "value": "", "error": None}
    _QUEUE.put((builder, box))
    box["done"].wait()          # 等用户选完（前端显示 loading）
    if box["error"] is not None:
        raise RuntimeError(f"文件对话框出错：{box['error']}")

    path = str(box.get("value") or "")
    STATE.log(f"文件对话框（{kind}）：{path or '已取消'}")
    return {"ok": True, "path": path, "canceled": not path}


# ---------------------------------------------------------------- 对外
def ask_open(
    title: str = "选择文件",
    initialdir: str = "",
    initialfile: str = "",
    patterns: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    start_dir = initialdir or str(snapshot_dir())

    def build(root: Any, fd: Any) -> str:
        return fd.askopenfilename(
            parent=root,
            title=title,
            initialdir=start_dir if os.path.isdir(start_dir) else "",
            initialfile=initialfile,
            filetypes=_filetypes(patterns),
        )

    return _dispatch(build, "打开")


def ask_save(
    title: str = "另存为",
    initialdir: str = "",
    initialfile: str = "",
    patterns: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    start_dir = initialdir or str(snapshot_dir())
    default_ext = patterns[0][1].replace("*", "") if patterns else ""

    def build(root: Any, fd: Any) -> str:
        return fd.asksaveasfilename(
            parent=root,
            title=title,
            initialdir=start_dir if os.path.isdir(start_dir) else "",
            initialfile=initialfile,
            defaultextension=default_ext,
            filetypes=_filetypes(patterns),
        )

    return _dispatch(build, "另存为")


def reveal(path: str = "") -> dict[str, Any]:
    """在资源管理器里定位该文件（没有路径就打开状态目录）。"""
    target = path or str(state_dir())
    if not os.path.exists(target):
        raise RuntimeError(f"路径不存在：{target}")
    if os.path.isdir(target):
        subprocess.Popen(["explorer", os.path.normpath(target)])
    else:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
    return {"ok": True, "path": target}


# 预制过滤器（前端只传 kind 名）
FILTERS: dict[str, list[tuple[str, str]]] = {
    "snapshot": [("扫描快照", "*.json"), ("JSON", "*.json")],
    "csv": [("CSV 表格", "*.csv")],
    "project": [("Altium 工程", "*.PrjPcb"), ("所有文件", "*.*")],
    "html": [("板框记录 HTML", "*.html;*.htm"), ("所有文件", "*.*")],
}

_TITLES = {
    "snapshot": "选择扫描快照",
    "project": "选择要打开的 Altium 工程",
    "html": "选择板框记录 HTML",
}


def open_by_type(kind: str) -> dict[str, Any]:
    return ask_open(title=_TITLES.get(kind, "选择文件"), patterns=FILTERS.get(kind))


def save_by_type(kind: str, name: str = "", folder: str = "") -> dict[str, Any]:
    titles = {"snapshot": "导出扫描快照", "csv": "导出违规清单 CSV"}
    return ask_save(
        title=titles.get(kind, "另存为"),
        initialdir=folder,
        initialfile=name,
        patterns=FILTERS.get(kind),
    )
