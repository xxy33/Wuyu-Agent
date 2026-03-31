"""
Agentic Loop - 核心工具调用循环引擎
参考 Claude Code query.ts 的 queryLoop 设计

核心流程：调用LLM -> 检查tool_calls -> 执行工具 -> 追加结果 -> 继续循环
支持自动恢复（finish_reason=length时注入续写提示）、进度回调和最大轮次限制。
"""
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from swagent.llm.openai_client import OpenAIClient
from swagent.llm.base_llm import LLMResponse
from swagent.tools.tool_registry import ToolRegistry
from swagent.core.progress import (
    ProgressEvent,
    ProgressEventType,
    ProgressReporter,
)
from swagent.core.error_handler import (
    ToolExecutionError,
    classify_error,
    format_error_for_llm,
)
from swagent.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class AgenticLoopConfig:
    """循环引擎配置"""
    # 最大循环轮次
    max_turns: int = 20
    # finish_reason=length时的最大自动恢复次数
    max_length_retries: int = 3
    # 温度参数
    temperature: float = 0.7
    # 最大token
    max_tokens: int = 4096
    # 工具选择策略
    tool_choice: str = "auto"


@dataclass
class AgenticLoopResult:
    """循环引擎最终结果"""
    content: str
    messages: List[Dict[str, Any]]
    turns_used: int
    total_tokens: int = 0
    finish_reason: Optional[str] = None


def _make_event(
    event_type: str,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造事件字典"""
    return {
        "type": event_type,
        "data": data or {},
        "timestamp": time.time(),
    }


async def agentic_loop(
    client: OpenAIClient,
    messages: List[Dict[str, Any]],
    tool_registry: ToolRegistry,
    config: Optional[AgenticLoopConfig] = None,
    on_progress: Optional[Callable] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    核心Agentic循环引擎（异步生成器）

    循环调用LLM，若返回tool_calls则执行工具并将结果追加到消息列表，
    直到LLM不再请求工具调用或达到轮次上限。

    Args:
        client: OpenAI兼容LLM客户端
        messages: 初始消息列表（会被原地修改）
        tool_registry: 工具注册中心
        config: 循环配置
        on_progress: 可选的进度回调

    Yields:
        事件字典，包含type/data/timestamp字段

    事件类型:
        - llm_start: LLM调用开始
        - llm_response: 收到LLM响应
        - tool_start: 工具开始执行
        - tool_result: 工具执行完成
        - recovery: 自动恢复（length截断）
        - complete: 循环正常结束
        - error: 发生错误
    """
    config = config or AgenticLoopConfig()
    tools = tool_registry.to_openai_functions()

    turns_used = 0
    total_tokens = 0
    length_retries = 0
    final_content = ""

    while turns_used < config.max_turns:
        turns_used += 1

        # ---- 1. 调用LLM ----
        yield _make_event("llm_start", {"turn": turns_used})

        try:
            response: LLMResponse = await client.chat_with_tools(
                messages=messages,
                tools=tools,
                tool_choice=config.tool_choice,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )
        except Exception as e:
            logger.error(f"LLM调用失败 (轮次 {turns_used}): {e}")
            yield _make_event("error", {
                "turn": turns_used,
                "error": classify_error(e),
                "message": format_error_for_llm(e),
            })
            return

        total_tokens += response.total_tokens

        yield _make_event("llm_response", {
            "turn": turns_used,
            "content": response.content,
            "finish_reason": response.finish_reason,
            "has_tool_calls": response.has_tool_calls,
            "tokens": response.total_tokens,
        })

        # ---- 2. 处理finish_reason=length（输出截断）----
        if response.finish_reason == "length" and not response.has_tool_calls:
            length_retries += 1
            if length_retries <= config.max_length_retries:
                logger.warning(
                    f"输出被截断，自动恢复 ({length_retries}/{config.max_length_retries})"
                )
                # 追加助手的部分响应
                if response.content:
                    messages.append({
                        "role": "assistant",
                        "content": response.content,
                    })
                # 注入续写提示
                messages.append({
                    "role": "user",
                    "content": "你的回复被截断了，请从上次停止的地方继续。",
                })
                yield _make_event("recovery", {
                    "turn": turns_used,
                    "retry": length_retries,
                    "reason": "length_truncation",
                })
                continue
            else:
                logger.warning("自动恢复次数用尽，返回当前内容")

        # ---- 3. 无工具调用：循环结束 ----
        if not response.has_tool_calls:
            final_content = response.content or ""
            # 追加最终助手消息
            messages.append({
                "role": "assistant",
                "content": final_content,
            })
            yield _make_event("complete", {
                "turn": turns_used,
                "content": final_content,
                "total_tokens": total_tokens,
                "finish_reason": response.finish_reason,
            })
            return

        # ---- 4. 有工具调用：执行工具 ----
        # 追加带tool_calls的助手消息
        assistant_msg: Dict[str, Any] = {
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": response.tool_calls,
        }
        messages.append(assistant_msg)

        # 逐个执行工具
        for tool_call in response.tool_calls:
            tool_name = tool_call["function"]["name"]
            tool_call_id = tool_call["id"]
            raw_arguments = tool_call["function"]["arguments"]

            yield _make_event("tool_start", {
                "turn": turns_used,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
            })

            # 解析参数
            try:
                kwargs = json.loads(raw_arguments) if raw_arguments else {}
            except json.JSONDecodeError as e:
                logger.warning(f"工具 {tool_name} 参数JSON解析失败: {e}")
                error_content = json.dumps({
                    "success": False,
                    "error": f"参数解析失败: {e}",
                }, ensure_ascii=False)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": error_content,
                })
                yield _make_event("tool_result", {
                    "turn": turns_used,
                    "tool_name": tool_name,
                    "success": False,
                    "error": f"JSON解析失败: {e}",
                })
                continue

            # 执行工具
            try:
                result = await tool_registry.execute_tool(tool_name, **kwargs)
                result_content = json.dumps(result.to_dict(), ensure_ascii=False, default=str)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_content,
                })

                yield _make_event("tool_result", {
                    "turn": turns_used,
                    "tool_name": tool_name,
                    "success": result.success,
                    "data": result.data if result.success else None,
                    "error": result.error,
                })

            except Exception as e:
                logger.error(f"工具 {tool_name} 执行异常: {e}")
                error_content = json.dumps({
                    "success": False,
                    "error": format_error_for_llm(e, max_chars=2000),
                }, ensure_ascii=False)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": error_content,
                })
                yield _make_event("tool_result", {
                    "turn": turns_used,
                    "tool_name": tool_name,
                    "success": False,
                    "error": classify_error(e),
                })

        # 重置length重试计数（成功执行了工具调用）
        length_retries = 0

    # ---- 超出最大轮次 ----
    logger.warning(f"循环达到最大轮次 {config.max_turns}")
    yield _make_event("error", {
        "turn": turns_used,
        "error": "max_turns_exceeded",
        "message": f"循环已达到最大轮次限制 ({config.max_turns})",
    })


async def run_agentic_loop(
    client: OpenAIClient,
    messages: List[Dict[str, Any]],
    tool_registry: ToolRegistry,
    config: Optional[AgenticLoopConfig] = None,
    on_progress: Optional[Callable] = None,
) -> AgenticLoopResult:
    """
    运行Agentic循环并收集最终结果（便捷包装）

    与agentic_loop生成器不同，此函数消费所有事件并返回最终结果。
    如果提供了on_progress回调，每个事件都会传给回调。

    Args:
        client: LLM客户端
        messages: 消息列表
        tool_registry: 工具注册中心
        config: 循环配置
        on_progress: 进度回调函数

    Returns:
        AgenticLoopResult包含最终内容和完整消息历史
    """
    config = config or AgenticLoopConfig()
    final_content = ""
    turns_used = 0
    total_tokens = 0
    finish_reason = None

    async for event in agentic_loop(client, messages, tool_registry, config, on_progress):
        # 回调通知
        if on_progress:
            try:
                result = on_progress(event)
                # 支持异步回调
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                pass

        event_type = event["type"]
        event_data = event.get("data", {})

        if event_type == "complete":
            final_content = event_data.get("content", "")
            turns_used = event_data.get("turn", 0)
            total_tokens = event_data.get("total_tokens", 0)
            finish_reason = event_data.get("finish_reason")
        elif event_type == "error":
            turns_used = event_data.get("turn", 0)
            final_content = event_data.get("message", "")
            finish_reason = "error"

    return AgenticLoopResult(
        content=final_content,
        messages=messages,
        turns_used=turns_used,
        total_tokens=total_tokens,
        finish_reason=finish_reason,
    )
