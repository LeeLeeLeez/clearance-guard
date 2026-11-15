"""网络电压分类、间距规则与避距违规计算。"""

from .classify import (
    apply_follow_target,
    apply_reference_potential,
    assign_primary_references,
    assign_secondary_references,
    classify_board,
)
from .clearance import aggregate_by_component, find_violations
from .merge import MergeStats, merge_classifications, merge_mounts
from .rules import ClearanceRules, default_rules

__all__ = [
    "classify_board",
    "apply_follow_target",
    "apply_reference_potential",
    "assign_primary_references",
    "assign_secondary_references",
    "find_violations",
    "aggregate_by_component",
    "MergeStats",
    "merge_classifications",
    "merge_mounts",
    "ClearanceRules",
    "default_rules",
]
