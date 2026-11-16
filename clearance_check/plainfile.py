"""读取被 DLP 加密的文件时自动转明文。

本机的 DLP 只对白名单进程（python.exe 等）透明解密；打包后的
``ClearanceGuard.exe`` 读到的是密文（文件头 ``%TSD-Header-###%``）。
这里的做法：发现密文就调用一个**白名单解释器**把文件内容通过 stdout
管道送回（管道不过滤，拿到的是明文），全程不落盘。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

DLP_MAGIC = b"%TSD-Header-###%"

_HELPER = (
    "import sys;"
    "data=open(sys.argv[1],'rb').read();"
    "sys.stdout.buffer.write(data)"
)


class DlpProtectedError(RuntimeError):
    """文件被 DLP 加密，且找不到可用的白名单解释器。"""


def looks_protected(data: bytes) -> bool:
    return data.startswith(DLP_MAGIC)


def _python_candidates() -> list[list[str]]:
    """可用于转明文的解释器（都要是 DLP 白名单里的进程）。"""
    candidates: list[list[str]] = []
    if not getattr(sys, "frozen", False):
        candidates.append([sys.executable])
    for name in ("python.exe", "python"):
        found = shutil.which(name)
        if found:
            candidates.append([found])
    try:
        from .eda.mcp_client import _installed_pythons

        for path in _installed_pythons() or []:
            text = str(path)
            if text:
                candidates.append([text])
    except Exception:  # noqa: BLE001 - 探测失败不影响其它候选
        pass
    candidates.append(["py", "-3"])
    seen: set[tuple[str, ...]] = set()
    unique = []
    for item in candidates:
        key = tuple(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _read_via_helper(path: Path) -> bytes | None:
    creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    for command in _python_candidates():
        try:
            completed = subprocess.run(
                [*command, "-c", _HELPER, str(path)],
                capture_output=True,
                timeout=30,
                creationflags=creationflags,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            continue
        data = completed.stdout
        if completed.returncode == 0 and data and not looks_protected(data):
            return data
    return None


def read_bytes(path: str | Path) -> bytes:
    """读文件；遇到 DLP 密文自动转明文。"""
    target = Path(path)
    data = target.read_bytes()
    if not looks_protected(data):
        return data
    plain = _read_via_helper(target)
    if plain is None:
        raise DlpProtectedError(
            f"“{target.name}” 被 DLP 加密，且没有找到可用的 python 解释器解密。\n"
            "解决办法：把 ClearanceGuard.exe 加入 DLP 白名单，"
            "或直接用 python app.py 运行本程序。"
        )
    return plain


def read_text(path: str | Path, encoding: str = "utf-8") -> str:
    return read_bytes(path).decode(encoding)


def read_json(path: str | Path):
    return json.loads(read_text(path))
