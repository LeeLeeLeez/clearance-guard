"""Altium 工程文件 (.PrjPcb) 解析与文档定位。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_DOC_PATH_RE = re.compile(r"^\s*DocumentPath\s*=\s*(.+?)\s*$", re.IGNORECASE)


@dataclass
class AltiumProject:
    path: str
    name: str
    documents: list[str] = field(default_factory=list)

    @property
    def pcb_documents(self) -> list[str]:
        return [d for d in self.documents if d.lower().endswith(".pcbdoc")]

    @property
    def schematic_documents(self) -> list[str]:
        return [d for d in self.documents if d.lower().endswith(".schdoc")]

    @property
    def existing_pcb_documents(self) -> list[str]:
        return [d for d in self.pcb_documents if os.path.isfile(d)]


def _read_text(path: str) -> str:
    for encoding in ("utf-8-sig", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as handle:
                return handle.read()
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            raise ValueError(f"无法读取工程文件: {exc}") from exc
    return ""


def parse_prjpcb(path: str) -> AltiumProject:
    """解析 PrjPcb，返回工程中的文档列表（绝对路径）。"""
    text = _read_text(path)
    base = os.path.dirname(os.path.abspath(path))
    project = AltiumProject(
        path=os.path.abspath(path),
        name=os.path.splitext(os.path.basename(path))[0],
    )

    seen: set[str] = set()
    for line in text.splitlines():
        match = _DOC_PATH_RE.match(line)
        if not match:
            continue
        relative = match.group(1).strip().strip('"')
        relative = relative.replace("\\", os.sep).replace("/", os.sep)
        absolute = os.path.normpath(os.path.join(base, relative))
        key = absolute.lower()
        if key in seen:
            continue
        seen.add(key)
        project.documents.append(absolute)

    if not project.documents:
        # 兜底：直接扫描工程目录
        for item in sorted(Path(base).iterdir()):
            if item.suffix.lower() in (".pcbdoc", ".schdoc"):
                if str(item).lower() not in seen:
                    project.documents.append(str(item))

    return project


def find_project_file(path: str) -> str | None:
    """在给定路径（文件或目录）中定位 .PrjPcb。"""
    target = Path(path)
    if target.is_file():
        return str(target)
    if not target.is_dir():
        return None
    candidates = sorted(target.glob("*.PrjPcb")) + sorted(target.glob("*.[Pp][Rr][Jj][Pp][Cc][Bb]"))
    if candidates:
        return str(candidates[0])
    nested = sorted(target.glob("*/*.PrjPcb"))
    return str(nested[0]) if nested else None
