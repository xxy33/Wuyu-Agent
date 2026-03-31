"""
Progress - 进度报告系统
提供统一的进度事件和回调机制，供工具和循环引擎报告执行进度
"""
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Awaitable
from enum import Enum


class ProgressEventType(Enum):
    """进度事件类型"""
    LLM_START = "llm_start"
    LLM_RESPONSE = "llm_response"
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    RECOVERY = "recovery"
    COMPACT = "compact"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class ProgressEvent:
    """
    进度事件

    表示执行过程中的一个事件节点，用于追踪和展示执行进度。
    """
    event_type: ProgressEventType
    tool_name: Optional[str] = None
    message: str = ""
    data: Optional[Dict[str, Any]] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "type": self.event_type.value,
            "tool_name": self.tool_name,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp,
        }


# 回调类型：同步或异步
ProgressCallback = Callable[[ProgressEvent], Any]
AsyncProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


class ProgressReporter:
    """
    进度报告器

    收集进度事件并通过回调通知订阅者。
    支持同步和异步回调。
    """

    def __init__(self):
        """初始化进度报告器"""
        self._callbacks: List[ProgressCallback] = []
        self._async_callbacks: List[AsyncProgressCallback] = []
        self._history: List[ProgressEvent] = []

    def add_callback(self, callback: ProgressCallback) -> None:
        """
        添加同步回调

        Args:
            callback: 进度回调函数
        """
        self._callbacks.append(callback)

    def add_async_callback(self, callback: AsyncProgressCallback) -> None:
        """
        添加异步回调

        Args:
            callback: 异步进度回调函数
        """
        self._async_callbacks.append(callback)

    def emit(self, event: ProgressEvent) -> None:
        """
        发射同步进度事件

        Args:
            event: 进度事件
        """
        self._history.append(event)
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception:
                pass  # 回调异常不应中断主流程

    async def emit_async(self, event: ProgressEvent) -> None:
        """
        发射异步进度事件

        Args:
            event: 进度事件
        """
        self._history.append(event)
        # 同步回调也触发
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception:
                pass
        # 异步回调
        for callback in self._async_callbacks:
            try:
                await callback(event)
            except Exception:
                pass

    def emit_tool_start(self, tool_name: str, args: Optional[Dict] = None) -> None:
        """快捷方法：报告工具开始执行"""
        self.emit(ProgressEvent(
            event_type=ProgressEventType.TOOL_START,
            tool_name=tool_name,
            message=f"开始执行工具: {tool_name}",
            data={"args": args},
        ))

    def emit_tool_result(self, tool_name: str, success: bool, data: Any = None) -> None:
        """快捷方法：报告工具执行结果"""
        self.emit(ProgressEvent(
            event_type=ProgressEventType.TOOL_RESULT,
            tool_name=tool_name,
            message=f"工具 {tool_name} 执行{'成功' if success else '失败'}",
            data={"success": success, "data": data},
        ))

    @property
    def history(self) -> List[ProgressEvent]:
        """获取事件历史"""
        return list(self._history)

    def clear_history(self) -> None:
        """清空事件历史"""
        self._history.clear()
