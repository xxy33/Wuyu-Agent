"""
Hook 系统 - 工具执行生命周期钩子
参考 Claude Code hooks.ts 设计

支持的 Hook 事件:
- PreToolUse: 工具执行前（可阻断、修改输入）
- PostToolUse: 工具执行后（可修改输出、审计日志）
- PreLoop: agentic loop 每轮开始前
- PostLoop: agentic loop 结束后
- OnError: 错误发生时
- SessionStart/SessionEnd: 会话开始/结束

Hook 类型:
- CallbackHook: Python 函数回调
- CommandHook: 执行 shell 命令（stdin 接收 JSON，exit code 2 = 阻断）
- HttpHook: POST 到外部 API

Hook 决策:
- allow: 允许继续
- deny: 阻断并返回原因
- modify: 修改输入/输出后继续
"""

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
)

from swagent.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 枚举与数据类
# ---------------------------------------------------------------------------

class HookEvent(Enum):
    """Hook 事件类型"""
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    PRE_LOOP = "PreLoop"
    POST_LOOP = "PostLoop"
    ON_ERROR = "OnError"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"


class HookAction(Enum):
    """Hook 决策动作"""
    ALLOW = "allow"
    DENY = "deny"
    MODIFY = "modify"


class HookType(Enum):
    """Hook 执行方式"""
    CALLBACK = "callback"
    COMMAND = "command"
    HTTP = "http"


@dataclass
class HookDecision:
    """单个 Hook 的执行决策"""
    action: HookAction = HookAction.ALLOW
    reason: Optional[str] = None
    modified_data: Optional[Dict[str, Any]] = None

    @staticmethod
    def allow() -> "HookDecision":
        """快捷创建 ALLOW 决策"""
        return HookDecision(action=HookAction.ALLOW)

    @staticmethod
    def deny(reason: str) -> "HookDecision":
        """快捷创建 DENY 决策"""
        return HookDecision(action=HookAction.DENY, reason=reason)

    @staticmethod
    def modify(modified_data: Dict[str, Any], reason: Optional[str] = None) -> "HookDecision":
        """快捷创建 MODIFY 决策"""
        return HookDecision(
            action=HookAction.MODIFY,
            reason=reason,
            modified_data=modified_data,
        )


@dataclass
class AggregatedDecision:
    """
    多个 Hook 的聚合决策

    规则:
    - 任一 hook 返回 DENY -> 最终为 DENY（短路）
    - 有 MODIFY 且无 DENY -> 最终为 MODIFY（合并所有修改）
    - 全部 ALLOW -> 最终为 ALLOW
    """
    action: HookAction = HookAction.ALLOW
    reasons: List[str] = field(default_factory=list)
    modified_data: Optional[Dict[str, Any]] = None
    individual_decisions: List[HookDecision] = field(default_factory=list)

    @property
    def is_denied(self) -> bool:
        """是否被拒绝"""
        return self.action == HookAction.DENY

    @property
    def is_modified(self) -> bool:
        """是否有修改"""
        return self.action == HookAction.MODIFY

    @property
    def deny_reason(self) -> Optional[str]:
        """返回拒绝原因的合并字符串"""
        if not self.reasons:
            return None
        return "; ".join(self.reasons)


@dataclass
class HookContext:
    """传递给 Hook 的上下文信息"""
    event: HookEvent
    tool_name: Optional[str] = None
    tool_params: Optional[Dict[str, Any]] = None
    tool_result: Optional[Any] = None
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    error: Optional[Exception] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于 CommandHook / HttpHook）"""
        data: Dict[str, Any] = {
            "event": self.event.value,
            "tool_name": self.tool_name,
            "tool_params": self.tool_params,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
        }
        if self.tool_result is not None:
            try:
                json.dumps(self.tool_result)
                data["tool_result"] = self.tool_result
            except (TypeError, ValueError):
                data["tool_result"] = str(self.tool_result)
        if self.error is not None:
            data["error"] = str(self.error)
        if self.extra:
            data["extra"] = self.extra
        return data

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Hook 回调类型
# ---------------------------------------------------------------------------

HookCallback = Callable[[HookContext], Awaitable[HookDecision]]


# ---------------------------------------------------------------------------
# 匹配工具
# ---------------------------------------------------------------------------

def _matches_tool(pattern: Optional[str], tool_name: Optional[str]) -> bool:
    """
    判断工具名是否匹配模式

    支持:
    - None / "*": 匹配所有
    - 精确匹配: "read_file"
    - 通配符: "file_*", "*_write"
    """
    if pattern is None or pattern == "*":
        return True
    if tool_name is None:
        return True
    # 将简单通配符转为正则
    regex = pattern.replace("*", ".*")
    return bool(re.fullmatch(regex, tool_name))


# ---------------------------------------------------------------------------
# HookDefinition
# ---------------------------------------------------------------------------

@dataclass
class HookDefinition:
    """Hook 定义"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    event: HookEvent = HookEvent.PRE_TOOL_USE
    hook_type: HookType = HookType.CALLBACK
    # matcher: 用于匹配 tool_name 的 glob 模式，None 表示匹配所有
    matcher: Optional[str] = None
    # CallbackHook
    callback: Optional[HookCallback] = None
    # CommandHook
    command: Optional[str] = None
    command_timeout: float = 30.0
    # HttpHook
    url: Optional[str] = None
    http_headers: Dict[str, str] = field(default_factory=dict)
    http_timeout: float = 10.0
    # 通用
    priority: int = 0  # 越大越先执行
    enabled: bool = True
    description: Optional[str] = None

    def matches_tool(self, tool_name: Optional[str]) -> bool:
        """判断该 Hook 是否匹配指定的工具名"""
        return _matches_tool(self.matcher, tool_name)


# ---------------------------------------------------------------------------
# Hook 执行器（按类型分派）
# ---------------------------------------------------------------------------

async def _execute_callback_hook(
    hook: HookDefinition, context: HookContext
) -> HookDecision:
    """执行 CallbackHook"""
    if hook.callback is None:
        logger.warning(f"CallbackHook {hook.id} 未设置 callback，跳过")
        return HookDecision.allow()
    try:
        return await hook.callback(context)
    except Exception as exc:
        logger.error(f"CallbackHook {hook.id} 执行异常: {exc}", exc_info=True)
        return HookDecision.allow()


async def _execute_command_hook(
    hook: HookDefinition, context: HookContext
) -> HookDecision:
    """
    执行 CommandHook

    - 通过 stdin 向子进程传入 JSON 上下文
    - exit code 0: 允许，stdout 可选返回 JSON {action, reason, modified_data}
    - exit code 2: 阻断，stdout/stderr 中的 reason 字段说明原因
    - 其他 exit code: 视为 allow（容错）
    """
    if hook.command is None:
        logger.warning(f"CommandHook {hook.id} 未设置 command，跳过")
        return HookDecision.allow()

    stdin_data = context.to_json()
    try:
        proc = await asyncio.create_subprocess_shell(
            hook.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=stdin_data.encode("utf-8")),
            timeout=hook.command_timeout,
        )
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if stderr:
            logger.debug(f"CommandHook {hook.id} stderr: {stderr}")

        returncode = proc.returncode or 0

        if returncode == 2:
            # 阻断
            reason = "被 CommandHook 阻断"
            if stdout:
                try:
                    payload = json.loads(stdout)
                    reason = payload.get("reason", reason)
                except json.JSONDecodeError:
                    reason = stdout
            elif stderr:
                reason = stderr
            return HookDecision.deny(reason)

        if returncode == 0 and stdout:
            try:
                payload = json.loads(stdout)
                action_str = payload.get("action", "allow")
                action = HookAction(action_str)
                return HookDecision(
                    action=action,
                    reason=payload.get("reason"),
                    modified_data=payload.get("modified_data"),
                )
            except (json.JSONDecodeError, ValueError):
                pass

        return HookDecision.allow()

    except asyncio.TimeoutError:
        logger.error(f"CommandHook {hook.id} 执行超时 ({hook.command_timeout}s)")
        return HookDecision.allow()
    except Exception as exc:
        logger.error(f"CommandHook {hook.id} 执行异常: {exc}", exc_info=True)
        return HookDecision.allow()


async def _execute_http_hook(
    hook: HookDefinition, context: HookContext
) -> HookDecision:
    """
    执行 HttpHook

    POST JSON 到指定 URL，解析响应中的 {action, reason, modified_data}
    """
    if hook.url is None:
        logger.warning(f"HttpHook {hook.id} 未设置 url，跳过")
        return HookDecision.allow()

    try:
        import aiohttp
    except ImportError:
        logger.error("HttpHook 需要 aiohttp，请安装: pip install aiohttp")
        return HookDecision.allow()

    payload = context.to_dict()
    headers = {"Content-Type": "application/json", **hook.http_headers}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                hook.url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=hook.http_timeout),
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"HttpHook {hook.id} 返回非 200 状态: {resp.status}"
                    )
                    return HookDecision.allow()

                body = await resp.json()
                action_str = body.get("action", "allow")
                action = HookAction(action_str)
                return HookDecision(
                    action=action,
                    reason=body.get("reason"),
                    modified_data=body.get("modified_data"),
                )
    except asyncio.TimeoutError:
        logger.error(f"HttpHook {hook.id} 请求超时 ({hook.http_timeout}s)")
        return HookDecision.allow()
    except Exception as exc:
        logger.error(f"HttpHook {hook.id} 请求异常: {exc}", exc_info=True)
        return HookDecision.allow()


# 分派表
_EXECUTORS = {
    HookType.CALLBACK: _execute_callback_hook,
    HookType.COMMAND: _execute_command_hook,
    HookType.HTTP: _execute_http_hook,
}


async def _execute_single_hook(
    hook: HookDefinition, context: HookContext
) -> HookDecision:
    """执行单个 Hook 并返回决策"""
    executor = _EXECUTORS.get(hook.hook_type)
    if executor is None:
        logger.error(f"未知的 Hook 类型: {hook.hook_type}")
        return HookDecision.allow()
    return await executor(hook, context)


# ---------------------------------------------------------------------------
# HookRegistry
# ---------------------------------------------------------------------------

class HookRegistry:
    """
    Hook 注册中心

    管理所有已注册的 Hook 定义，支持按事件类型和工具名匹配。
    内置审计日志记录每次 Hook 的执行情况。
    """

    def __init__(self) -> None:
        self._hooks: Dict[str, HookDefinition] = {}
        self._audit_log: List[Dict[str, Any]] = []
        logger.info("Hook 注册中心初始化完成")

    # ---- 注册 / 注销 ----

    def register(self, hook: HookDefinition) -> str:
        """
        注册 Hook

        Args:
            hook: Hook 定义

        Returns:
            Hook ID
        """
        self._hooks[hook.id] = hook
        logger.info(
            f"注册 Hook: id={hook.id}, event={hook.event.value}, "
            f"type={hook.hook_type.value}, matcher={hook.matcher}"
        )
        return hook.id

    def unregister(self, hook_id: str) -> bool:
        """
        注销 Hook

        Args:
            hook_id: Hook ID

        Returns:
            是否成功注销
        """
        if hook_id in self._hooks:
            del self._hooks[hook_id]
            logger.info(f"注销 Hook: {hook_id}")
            return True
        return False

    def get_hook(self, hook_id: str) -> Optional[HookDefinition]:
        """根据 ID 获取 Hook"""
        return self._hooks.get(hook_id)

    def clear(self) -> None:
        """清空所有 Hook"""
        self._hooks.clear()
        logger.info("Hook 注册中心已清空")

    # ---- 查询 ----

    def get_matching_hooks(
        self,
        event: HookEvent,
        context: Optional[HookContext] = None,
    ) -> List[HookDefinition]:
        """
        获取匹配指定事件和上下文的所有 Hook

        按 priority 降序排列（高优先级先执行）

        Args:
            event: 事件类型
            context: 可选上下文，用于 tool_name 匹配

        Returns:
            匹配的 Hook 列表
        """
        tool_name = context.tool_name if context else None
        matched: List[HookDefinition] = []

        for hook in self._hooks.values():
            if not hook.enabled:
                continue
            if hook.event != event:
                continue
            if not hook.matches_tool(tool_name):
                continue
            matched.append(hook)

        # 按 priority 降序
        matched.sort(key=lambda h: h.priority, reverse=True)
        return matched

    # ---- 便捷注册方法 ----

    def on(
        self,
        event: HookEvent,
        callback: HookCallback,
        matcher: Optional[str] = None,
        priority: int = 0,
        description: Optional[str] = None,
    ) -> str:
        """
        注册 CallbackHook 的快捷方法

        Args:
            event: 事件类型
            callback: 异步回调函数
            matcher: 工具名匹配模式
            priority: 优先级
            description: 描述

        Returns:
            Hook ID
        """
        hook = HookDefinition(
            event=event,
            hook_type=HookType.CALLBACK,
            matcher=matcher,
            callback=callback,
            priority=priority,
            description=description,
        )
        return self.register(hook)

    def on_command(
        self,
        event: HookEvent,
        command: str,
        matcher: Optional[str] = None,
        priority: int = 0,
        timeout: float = 30.0,
        description: Optional[str] = None,
    ) -> str:
        """
        注册 CommandHook 的快捷方法

        Args:
            event: 事件类型
            command: Shell 命令
            matcher: 工具名匹配模式
            priority: 优先级
            timeout: 命令超时秒数
            description: 描述

        Returns:
            Hook ID
        """
        hook = HookDefinition(
            event=event,
            hook_type=HookType.COMMAND,
            matcher=matcher,
            command=command,
            command_timeout=timeout,
            priority=priority,
            description=description,
        )
        return self.register(hook)

    def on_http(
        self,
        event: HookEvent,
        url: str,
        matcher: Optional[str] = None,
        priority: int = 0,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
        description: Optional[str] = None,
    ) -> str:
        """
        注册 HttpHook 的快捷方法

        Args:
            event: 事件类型
            url: 目标 URL
            matcher: 工具名匹配模式
            priority: 优先级
            headers: 额外 HTTP 头
            timeout: 请求超时秒数
            description: 描述

        Returns:
            Hook ID
        """
        hook = HookDefinition(
            event=event,
            hook_type=HookType.HTTP,
            matcher=matcher,
            url=url,
            http_headers=headers or {},
            http_timeout=timeout,
            priority=priority,
            description=description,
        )
        return self.register(hook)

    # ---- 审计日志 ----

    @property
    def audit_log(self) -> List[Dict[str, Any]]:
        """获取 Hook 审计日志"""
        return list(self._audit_log)

    def clear_audit_log(self) -> None:
        """清空审计日志"""
        self._audit_log.clear()

    # ---- 统计 ----

    def get_statistics(self) -> Dict[str, Any]:
        """获取 Hook 注册统计"""
        by_event: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        for hook in self._hooks.values():
            by_event[hook.event.value] = by_event.get(hook.event.value, 0) + 1
            by_type[hook.hook_type.value] = by_type.get(hook.hook_type.value, 0) + 1
        return {
            "total": len(self._hooks),
            "by_event": by_event,
            "by_type": by_type,
        }

    def list_hooks(self) -> List[Dict[str, Any]]:
        """列出所有已注册 Hook 的摘要信息"""
        return [
            {
                "id": h.id,
                "event": h.event.value,
                "type": h.hook_type.value,
                "matcher": h.matcher,
                "enabled": h.enabled,
                "priority": h.priority,
                "description": h.description,
            }
            for h in self._hooks.values()
        ]

    def __len__(self) -> int:
        return len(self._hooks)

    def __repr__(self) -> str:
        return f"<HookRegistry(hooks={len(self._hooks)})>"


# ---------------------------------------------------------------------------
# 顶层执行函数
# ---------------------------------------------------------------------------

async def execute_hooks(
    registry: HookRegistry,
    event: HookEvent,
    context: HookContext,
) -> AggregatedDecision:
    """
    执行所有匹配的 Hook 并返回聚合决策

    聚合规则:
    1. 任一 Hook 返回 DENY -> 最终 DENY（短路）
    2. 有 MODIFY -> 最终 MODIFY（合并所有 modified_data）
    3. 全部 ALLOW -> 最终 ALLOW

    Args:
        registry: Hook 注册中心
        event: 事件类型
        context: Hook 上下文

    Returns:
        聚合后的决策
    """
    hooks = registry.get_matching_hooks(event, context)

    if not hooks:
        return AggregatedDecision()

    aggregated = AggregatedDecision()

    for hook in hooks:
        start_time = time.monotonic()
        decision = await _execute_single_hook(hook, context)
        duration_ms = (time.monotonic() - start_time) * 1000.0

        aggregated.individual_decisions.append(decision)

        # 记录审计日志
        registry._audit_log.append({
            "hook_id": hook.id,
            "event": event.value,
            "tool_name": context.tool_name,
            "action": decision.action.value,
            "reason": decision.reason,
            "duration_ms": round(duration_ms, 1),
            "timestamp": time.time(),
        })

        if decision.action == HookAction.DENY:
            # 短路：立即拒绝
            aggregated.action = HookAction.DENY
            if decision.reason:
                aggregated.reasons.append(decision.reason)
            logger.info(
                f"Hook {hook.id} 阻断了事件 {event.value}: {decision.reason}"
            )
            return aggregated

        if decision.action == HookAction.MODIFY:
            aggregated.action = HookAction.MODIFY
            if decision.reason:
                aggregated.reasons.append(decision.reason)
            # 合并修改：后执行的 Hook 覆盖先执行的同名字段
            if decision.modified_data:
                if aggregated.modified_data is None:
                    aggregated.modified_data = {}
                aggregated.modified_data.update(decision.modified_data)

        # ALLOW 不改变聚合状态

    return aggregated


# ---------------------------------------------------------------------------
# 全局 HookRegistry 单例
# ---------------------------------------------------------------------------

_global_hook_registry: Optional[HookRegistry] = None


def get_global_hook_registry() -> HookRegistry:
    """获取全局 Hook 注册中心"""
    global _global_hook_registry
    if _global_hook_registry is None:
        _global_hook_registry = HookRegistry()
    return _global_hook_registry
