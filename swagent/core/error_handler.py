"""
结构化错误处理
参考 Claude Code toolErrors.ts / classifyToolError 设计

特性:
- classify_error: 遥测安全的错误分类 (不泄露代码或文件路径)
- format_error_for_llm: 截断友好的错误文本 (头尾保留, 中间省略)
- 错误恢复策略枚举
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RecoveryStrategy(Enum):
    """错误恢复策略"""
    RETRY = "retry"                    # 重试
    FALLBACK_MODEL = "fallback_model"  # 模型降级
    COMPACT = "compact"                # 压缩上下文后重试
    SKIP = "skip"                      # 跳过
    ABORT = "abort"                    # 中止


class ToolExecutionError(Exception):
    """工具执行错误"""

    def __init__(self, tool_name: str, original_error: Exception, params: dict = None):
        self.tool_name = tool_name
        self.original_error = original_error
        self.params = params or {}
        super().__init__(f"工具 '{tool_name}' 执行失败: {original_error}")


class FallbackModelError(Exception):
    """主模型失败, 需要降级"""

    def __init__(self, original_model: str, error: Exception):
        self.original_model = original_model
        self.original_error = error
        super().__init__(f"模型 '{original_model}' 失败: {error}")


class ContextOverflowError(Exception):
    """上下文超出限制"""

    def __init__(self, current_tokens: int, max_tokens: int):
        self.current_tokens = current_tokens
        self.max_tokens = max_tokens
        super().__init__(f"上下文溢出: {current_tokens}/{max_tokens} tokens")


def classify_error(error: Exception) -> str:
    """
    将异常分类为遥测安全的字符串

    规则:
    - 不包含代码内容或文件路径
    - 已知错误类型返回类名
    - 未知错误返回 'Error'

    Returns:
        遥测安全的错误分类字符串
    """
    if isinstance(error, ToolExecutionError):
        inner = classify_error(error.original_error)
        return f"ToolError:{error.tool_name}:{inner}"

    if isinstance(error, FallbackModelError):
        return f"FallbackModel:{error.original_model}"

    if isinstance(error, ContextOverflowError):
        return "ContextOverflow"

    if isinstance(error, TimeoutError):
        return "Timeout"

    if isinstance(error, ConnectionError):
        return "Connection"

    if isinstance(error, PermissionError):
        return "Permission"

    if isinstance(error, FileNotFoundError):
        return "FileNotFound"

    if isinstance(error, ValueError):
        return "ValueError"

    if isinstance(error, KeyError):
        return "KeyError"

    # 检查常见的 API 错误
    error_type = type(error).__name__
    if len(error_type) > 3 and error_type != "Exception":
        return error_type[:60]

    return "Error"


def format_error_for_llm(error: Exception, max_length: int = 10000) -> str:
    """
    格式化错误信息给 LLM 阅读

    超长错误保留头尾, 中间截断, 总长度不超过 max_length。

    Returns:
        LLM 可读的错误文本
    """
    text = str(error)

    if len(text) <= max_length:
        return text

    # 头尾各保留 40%
    head_size = int(max_length * 0.4)
    tail_size = int(max_length * 0.4)
    omitted = len(text) - head_size - tail_size

    return (
        f"{text[:head_size]}\n"
        f"\n... [省略 {omitted} 字符] ...\n\n"
        f"{text[-tail_size:]}"
    )


def suggest_recovery(error: Exception) -> RecoveryStrategy:
    """
    根据错误类型建议恢复策略

    Returns:
        推荐的 RecoveryStrategy
    """
    if isinstance(error, ContextOverflowError):
        return RecoveryStrategy.COMPACT

    if isinstance(error, FallbackModelError):
        return RecoveryStrategy.FALLBACK_MODEL

    if isinstance(error, TimeoutError):
        return RecoveryStrategy.RETRY

    if isinstance(error, ConnectionError):
        return RecoveryStrategy.RETRY

    if isinstance(error, ToolExecutionError):
        return RecoveryStrategy.SKIP

    return RecoveryStrategy.ABORT
