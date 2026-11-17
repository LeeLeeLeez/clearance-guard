"""把历史快照里的分类配置套用到新扫描结果上。

工程改动后重新扫描，网络名通常不变。这里按网络名对齐：
同名网络沿用快照里的类别、电压、参考地、侧别与人工标记；
新出现的网络保留本次自动分类结果；快照里有、但新工程已不存在的网络忽略。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from ..model import Board, MountPoint, NetClass, NetClassification
from .classify import (
    _apply_zero_ohm_bonds,
    apply_follow_target,
    _zero_ohm_bonds,
    assign_primary_references,
    normalize_unknown_to_secondary,
)

#: 从历史快照照抄到新对象的字段
_COPY_FIELDS = (
    "net_class",
    "voltage",
    "voltage_min",
    "voltage_max",
    "confidence",
    "reasons",
    "confirmed",
    "needs_review",
    "auto_class",
    "is_drive",
    "ref_ground",
    "ref_ground_reason",
    "local_voltage_min",
    "local_voltage_max",
    "reference_applied",
    "reference_base_class",
    "user_edited",
    "sheet_side",
    "follow_net",
)

REUSE_NOTE = "沿用历史快照配置"


@dataclass
class MergeStats:
    """套用结果统计。"""

    kept: int = 0          # 同名网络，沿用快照
    added: int = 0         # 新网络，按本次自动分类
    removed: int = 0       # 快照里有、新工程已不存在
    kept_manual: int = 0   # 其中曾人工修改过的
    mounts_kept: int = 0   # 沿用电位的安装焊盘

    @property
    def summary(self) -> str:
        return (
            f"沿用 {self.kept} 个同名网络（其中人工修改 {self.kept_manual} 个），"
            f"新增 {self.added} 个按自动分类，忽略 {self.removed} 个已不存在的网络"
            + (f"，安装焊盘沿用 {self.mounts_kept} 个" if self.mounts_kept else "")
        )


def merge_classifications(
    current: dict[str, NetClassification],
    previous: dict[str, NetClassification],
    board: Board | None = None,
) -> MergeStats:
    """把 ``previous`` 里的配置写回 ``current``（就地修改）。"""
    stats = MergeStats()
    for net, item in current.items():
        old = previous.get(net)
        if old is None:
            stats.added += 1
            continue
        for field in _COPY_FIELDS:
            setattr(item, field, copy.deepcopy(getattr(old, field)))
        if REUSE_NOTE not in item.reasons:
            item.reasons.append(REUSE_NOTE)
        stats.kept += 1
        if old.user_edited:
            stats.kept_manual += 1

    stats.removed = sum(1 for net in previous if net not in current)
    normalize_unknown_to_secondary(current)
    assign_primary_references(current)
    apply_follow_target(current)
    if board is not None:
        _apply_zero_ohm_bonds(current, _zero_ohm_bonds(board))
    return stats


def merge_mounts(current: list[MountPoint], previous: list[MountPoint]) -> int:
    """按名称沿用安装焊盘的电位设置，返回匹配到的个数。"""
    old_by_name = {mount.name: mount for mount in previous}
    count = 0
    for mount in current:
        old = old_by_name.get(mount.name)
        if old is None:
            continue
        mount.net_class = (NetClass.SECONDARY if old.net_class is NetClass.UNKNOWN
                           else old.net_class)
        # 指派网络也沿用旧基线（旧数据为空时保留新扫描的默认 SGND）
        mount.net = old.net or mount.net
        mount.voltage = old.voltage
        mount.voltage_min = old.voltage_min
        mount.voltage_max = old.voltage_max
        mount.enabled = old.enabled
        mount.note = old.note or mount.note
        count += 1
    return count
