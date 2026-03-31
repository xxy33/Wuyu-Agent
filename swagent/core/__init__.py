"""
SWAgent 核心模块

包含Agent基类、消息系统、上下文管理、通信协议等核心组件
"""

# 阶段1+2+3：已实现的模块
from swagent.core.message import (
    Message,
    MessageType,
    MessageContent,
    ContentType,
    ThinkResult,
    ActionResult
)
from swagent.core.context import (
    ContextManager,
    ContextScope,
    ExecutionContext
)
from swagent.core.base_agent import (
    BaseAgent,
    AgentConfig,
    AgentState
)
from swagent.core.communication import (
    MessageBus,
    AgentCommunicator,
    CommunicationPattern,
    RateLimitConfig,
    RateLimiter,
    TurnManager
)
from swagent.core.orchestrator import (
    Orchestrator,
    TaskDefinition,
    TaskResult,
    OrchestrationMode
)
from swagent.core.hooks import (
    HookEvent,
    HookAction,
    HookDecision,
    HookContext,
    HookDefinition,
    HookRegistry,
)
from swagent.core.error_handler import (
    ToolExecutionError,
    FallbackModelError,
    ContextOverflowError,
    RecoveryStrategy,
    classify_error,
    format_error_for_llm,
)
from swagent.core.denial_tracking import DenialTracker
from swagent.core.progress import ProgressEvent, ProgressEventType, ProgressReporter
from swagent.core.session_storage import SessionStorage
from swagent.core.layered_settings import LayeredSettings, SettingSource
from swagent.core.subagent import SubagentContext, create_subagent_context

__all__ = [
    # 消息系统
    "Message", "MessageType", "MessageContent", "ContentType",
    "ThinkResult", "ActionResult",
    # 上下文管理
    "ContextManager", "ContextScope", "ExecutionContext",
    # Agent 基类
    "BaseAgent", "AgentConfig", "AgentState",
    # 通信系统
    "MessageBus", "AgentCommunicator", "CommunicationPattern",
    "RateLimitConfig", "RateLimiter", "TurnManager",
    # 编排系统
    "Orchestrator", "TaskDefinition", "TaskResult", "OrchestrationMode",
    # Hook 系统
    "HookEvent", "HookAction", "HookDecision",
    "HookContext", "HookDefinition", "HookRegistry",
    # 错误处理
    "ToolExecutionError", "FallbackModelError", "ContextOverflowError",
    "RecoveryStrategy", "classify_error", "format_error_for_llm",
    # 拒绝追踪
    "DenialTracker",
    # 进度报告
    "ProgressEvent", "ProgressEventType", "ProgressReporter",
    # 会话持久化
    "SessionStorage",
    # 分层配置
    "LayeredSettings", "SettingSource",
    # 子 Agent
    "SubagentContext", "create_subagent_context",
]
