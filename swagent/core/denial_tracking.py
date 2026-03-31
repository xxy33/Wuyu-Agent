"""
DenialTracking - 权限拒绝追踪系统
跟踪工具执行中的权限拒绝次数，触发降级策略
"""
from dataclasses import dataclass, field
from typing import Dict, List
import time


@dataclass
class DenialRecord:
    """单次拒绝记录"""
    tool_name: str
    reason: str
    timestamp: float = field(default_factory=time.time)


class DenialTracker:
    """
    权限拒绝追踪器

    跟踪连续和累计的权限拒绝次数。当连续拒绝达到阈值时，
    建议切换到降级模式以避免无效的重复请求。

    Args:
        max_consecutive: 最大连续拒绝次数，超过后触发降级
        max_total: 最大累计拒绝次数，超过后触发降级
    """

    def __init__(
        self,
        max_consecutive: int = 3,
        max_total: int = 20,
    ):
        self.max_consecutive = max_consecutive
        self.max_total = max_total
        self._consecutive_denials: int = 0
        self._total_denials: int = 0
        self._history: List[DenialRecord] = []

    def record_denial(self, tool_name: str = "", reason: str = "") -> None:
        """
        记录一次权限拒绝

        Args:
            tool_name: 被拒绝的工具名称
            reason: 拒绝原因
        """
        self._consecutive_denials += 1
        self._total_denials += 1
        self._history.append(DenialRecord(
            tool_name=tool_name,
            reason=reason,
        ))

    def record_success(self) -> None:
        """记录一次成功执行，重置连续拒绝计数"""
        self._consecutive_denials = 0

    def should_fallback(self) -> bool:
        """
        判断是否应切换到降级模式

        Returns:
            当连续拒绝或累计拒绝超过阈值时返回True
        """
        return (
            self._consecutive_denials >= self.max_consecutive
            or self._total_denials >= self.max_total
        )

    @property
    def consecutive_denials(self) -> int:
        """当前连续拒绝次数"""
        return self._consecutive_denials

    @property
    def total_denials(self) -> int:
        """累计拒绝次数"""
        return self._total_denials

    @property
    def history(self) -> List[DenialRecord]:
        """拒绝历史记录"""
        return list(self._history)

    def get_stats(self) -> Dict[str, int]:
        """
        获取统计信息

        Returns:
            包含连续拒绝、累计拒绝和阈值的字典
        """
        return {
            "consecutive_denials": self._consecutive_denials,
            "total_denials": self._total_denials,
            "max_consecutive": self.max_consecutive,
            "max_total": self.max_total,
            "should_fallback": self.should_fallback(),
        }

    def reset(self) -> None:
        """重置所有计数和历史"""
        self._consecutive_denials = 0
        self._total_denials = 0
        self._history.clear()
