"""
SWAgent 工具系统

提供Agent可调用的各种工具，支持OpenAI Function Calling和MCP两种调用方式
"""

from swagent.tools.base_tool import (
    BaseTool,
    ToolCategory,
    ToolParameter,
    ToolResult,
    ToolSchema
)
from swagent.tools.tool_registry import (
    ToolRegistry,
    get_global_registry,
    register_tool,
    register_tool_class
)
from swagent.tools.tool_executor import (
    ToolExecutor,
    ToolExecutionContext,
    AuditEntry,
    AuditLog,
)

__all__ = [
    # 基础类
    "BaseTool",
    "ToolCategory",
    "ToolParameter",
    "ToolResult",
    "ToolSchema",

    # 注册中心
    "ToolRegistry",
    "get_global_registry",
    "register_tool",
    "register_tool_class",

    # 执行器
    "ToolExecutor",
    "ToolExecutionContext",
    "AuditEntry",
    "AuditLog",
]
