"""
AutoCompact - 自动上下文压缩系统
参考 Claude Code autoCompact.ts 设计

当上下文窗口接近token上限时，自动压缩历史消息以腾出空间。
支持两种模式：LLM摘要压缩和轻量级占位符替换（MicroCompact）。
"""
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from swagent.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CompactConfig:
    """压缩配置"""
    # token阈值：当总token超过此值时触发压缩
    token_threshold: int = 80000
    # 保留最近N条消息不压缩
    keep_recent: int = 10
    # 摘要用的系统提示
    summary_system_prompt: str = (
        "你是一个对话摘要助手。请将以下对话历史压缩为简洁的摘要，"
        "保留关键信息、决策和上下文。用中文回复。"
    )
    # 摘要最大token
    summary_max_tokens: int = 1024
    # 熔断：连续失败次数上限
    circuit_breaker_limit: int = 3


@dataclass
class CompactResult:
    """压缩结果"""
    success: bool
    original_count: int
    compacted_count: int
    compacted_messages: Optional[List[Dict[str, Any]]] = None
    tokens_saved: int = 0
    method: str = ""  # "llm_summary" 或 "micro_compact"
    error: Optional[str] = None


class AutoCompact:
    """
    自动上下文压缩系统

    跟踪token使用量，在接近上限时自动压缩历史消息。
    包含熔断机制：连续失败后跳过压缩，避免死循环。
    """

    def __init__(self, config: Optional[CompactConfig] = None):
        """
        初始化压缩系统

        Args:
            config: 压缩配置，None则使用默认值
        """
        self.config = config or CompactConfig()
        self._consecutive_failures: int = 0
        self._total_compactions: int = 0
        self._circuit_open: bool = False

    def should_compact(self, total_tokens: int) -> bool:
        """
        判断是否需要压缩

        Args:
            total_tokens: 当前总token数

        Returns:
            是否需要压缩
        """
        if self._circuit_open:
            logger.warning("压缩熔断器已打开，跳过压缩")
            return False
        return total_tokens >= self.config.token_threshold

    async def compact_with_llm(
        self,
        messages: List[Dict[str, Any]],
        llm_client: Any,
        total_tokens: int = 0,
    ) -> CompactResult:
        """
        使用LLM摘要压缩历史消息

        保留system消息和最近keep_recent条消息，对其余消息生成摘要。

        Args:
            messages: 完整消息列表
            llm_client: LLM客户端实例（需有chat方法）
            total_tokens: 当前token总数（用于估算节省量）

        Returns:
            压缩结果（包含压缩后的消息列表）
        """
        if self._circuit_open:
            return CompactResult(
                success=False,
                original_count=len(messages),
                compacted_count=len(messages),
                compacted_messages=None,
                method="llm_summary",
                error="熔断器已打开",
            )

        original_count = len(messages)

        # 分离：system消息 + 可压缩消息 + 保留的最近消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        keep_recent = self.config.keep_recent
        if len(non_system) <= keep_recent:
            return CompactResult(
                success=True,
                original_count=original_count,
                compacted_count=original_count,
                compacted_messages=None,
                method="llm_summary",
                error="消息数不足，无需压缩",
            )

        to_compress = non_system[:-keep_recent]
        to_keep = non_system[-keep_recent:]

        try:
            # 将待压缩消息格式化为文本
            conversation_text = self._format_messages_for_summary(to_compress)

            summary_messages = [
                {"role": "system", "content": self.config.summary_system_prompt},
                {"role": "user", "content": f"请压缩以下对话历史：\n\n{conversation_text}"},
            ]

            response = await llm_client.chat(
                messages=summary_messages,
                max_tokens=self.config.summary_max_tokens,
                temperature=0.3,
            )

            summary_content = response.content
            if not summary_content:
                raise ValueError("LLM返回空摘要")

            # 构建压缩后的消息列表
            summary_msg = {
                "role": "assistant",
                "content": f"[对话摘要] {summary_content}",
            }

            compacted = system_msgs + [summary_msg] + to_keep

            self._consecutive_failures = 0
            self._total_compactions += 1

            logger.info(
                f"LLM压缩完成: {original_count} -> {len(compacted)} 条消息"
            )

            return CompactResult(
                success=True,
                original_count=original_count,
                compacted_count=len(compacted),
                compacted_messages=compacted,
                tokens_saved=max(0, total_tokens - (total_tokens * len(compacted) // original_count)),
                method="llm_summary",
            )

        except Exception as e:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.config.circuit_breaker_limit:
                self._circuit_open = True
                logger.error(
                    f"压缩连续失败 {self._consecutive_failures} 次，熔断器已打开"
                )

            logger.error(f"LLM压缩失败: {e}")
            return CompactResult(
                success=False,
                original_count=original_count,
                compacted_count=original_count,
                compacted_messages=None,
                method="llm_summary",
                error=str(e),
            )

    async def micro_compact(
        self,
        messages: List[Dict[str, Any]],
    ) -> CompactResult:
        """
        轻量级压缩：用占位符替换旧的工具调用结果

        不需要LLM调用，仅替换tool角色消息的内容为简短占位符。

        Args:
            messages: 完整消息列表

        Returns:
            压缩结果（包含压缩后的消息列表）
        """
        original_count = len(messages)
        keep_recent = self.config.keep_recent

        # 找出可以替换的旧tool消息
        compacted: List[Dict[str, Any]] = []
        non_system_indices = [i for i, m in enumerate(messages) if m.get("role") != "system"]

        # 只替换keep_recent之前的tool消息
        cutoff = len(non_system_indices) - keep_recent if len(non_system_indices) > keep_recent else 0
        old_indices = set(non_system_indices[:cutoff])

        replaced_count = 0
        for i, msg in enumerate(messages):
            if i in old_indices and msg.get("role") == "tool":
                compacted.append({
                    **msg,
                    "content": "[结果已压缩]",
                })
                replaced_count += 1
            else:
                compacted.append(msg)

        logger.info(f"MicroCompact: 替换了 {replaced_count} 条工具结果")

        return CompactResult(
            success=True,
            original_count=original_count,
            compacted_count=original_count,  # 消息数量不变
            compacted_messages=compacted,
            tokens_saved=0,  # 无法精确估算
            method="micro_compact",
        )

    def reset_circuit_breaker(self) -> None:
        """手动重置熔断器"""
        self._circuit_open = False
        self._consecutive_failures = 0
        logger.info("压缩熔断器已重置")

    @property
    def is_circuit_open(self) -> bool:
        """熔断器是否打开"""
        return self._circuit_open

    @property
    def total_compactions(self) -> int:
        """总压缩次数"""
        return self._total_compactions

    @staticmethod
    def _format_messages_for_summary(messages: List[Dict[str, Any]]) -> str:
        """
        将消息列表格式化为可读文本，供摘要使用

        Args:
            messages: 消息列表

        Returns:
            格式化后的文本
        """
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if role == "tool":
                tool_id = msg.get("tool_call_id", "")
                # 截断过长的工具结果
                if len(content) > 500:
                    content = content[:500] + "...[已截断]"
                lines.append(f"[工具结果 {tool_id}]: {content}")
            elif role == "assistant" and msg.get("tool_calls"):
                tool_names = [
                    tc.get("function", {}).get("name", "unknown")
                    for tc in msg.get("tool_calls", [])
                ]
                lines.append(f"助手调用工具: {', '.join(tool_names)}")
                if content:
                    lines.append(f"助手: {content}")
            else:
                lines.append(f"{role}: {content}")

        return "\n".join(lines)
