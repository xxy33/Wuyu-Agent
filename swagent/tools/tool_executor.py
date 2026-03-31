"""
工具执行管线 - 带 Hook 和审计的工具执行器
参考 Claude Code toolExecution.ts 设计

执行流程:
输入验证 → PreToolUse hooks → 权限检查 → 执行工具 → PostToolUse hooks → 结果处理
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from swagent.core.hooks import (
    AggregatedDecision,
    HookAction,
    HookContext,
    HookEvent,
    HookRegistry,
    execute_hooks,
    get_global_hook_registry,
)
from swagent.tools.base_tool import BaseTool, ToolResult
from swagent.tools.tool_registry import ToolRegistry
from swagent.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class ToolExecutionContext:
    """工具执行上下文"""
    session_id: str = ""
    agent_id: str = ""
    user_id: str = ""
    permissions: Set[str] = field(default_factory=set)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditEntry:
    """审计日志条目"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    tool_name: str = ""
    params: Optional[Dict[str, Any]] = None
    result_success: Optional[bool] = None
    result_error: Optional[str] = None
    duration_ms: float = 0.0
    session_id: str = ""
    agent_id: str = ""
    user_id: str = ""
    hook_decision: Optional[str] = None  # allow / deny / modify
    denied_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "tool_name": self.tool_name,
            "params": self.params,
            "result_success": self.result_success,
            "result_error": self.result_error,
            "duration_ms": self.duration_ms,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "hook_decision": self.hook_decision,
            "denied_reason": self.denied_reason,
        }


class AuditLog:
    """
    审计日志

    记录每次工具调用的完整信息，支持查询和导出
    """

    def __init__(self, max_entries: int = 10000) -> None:
        self._entries: List[AuditEntry] = []
        self._max_entries = max_entries

    def record(self, entry: AuditEntry) -> None:
        """记录一条审计条目"""
        self._entries.append(entry)
        # 超出上限时丢弃最旧的记录
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

    def query(
        self,
        tool_name: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """
        查询审计条目

        Args:
            tool_name: 按工具名过滤
            session_id: 按会话 ID 过滤
            limit: 最大返回数量

        Returns:
            匹配的审计条目列表（最新在前）
        """
        results = reversed(self._entries)
        filtered: List[AuditEntry] = []
        for entry in results:
            if tool_name and entry.tool_name != tool_name:
                continue
            if session_id and entry.session_id != session_id:
                continue
            filtered.append(entry)
            if len(filtered) >= limit:
                break
        return filtered

    def export(self) -> List[Dict[str, Any]]:
        """导出所有审计条目为字典列表"""
        return [e.to_dict() for e in self._entries]

    def clear(self) -> None:
        """清空审计日志"""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# ToolExecutor
# ---------------------------------------------------------------------------

class ToolExecutor:
    """
    工具执行器

    封装 ToolRegistry，在工具调用前后执行 Hook、权限检查和审计日志。

    执行流程:
    1. 输入验证（参数校验）
    2. PreToolUse hooks（可阻断或修改输入）
    3. 权限检查
    4. 执行工具（带超时和结果截断）
    5. PostToolUse hooks（可修改输出）
    6. 审计记录
    """

    def __init__(
        self,
        registry: ToolRegistry,
        hook_registry: Optional[HookRegistry] = None,
        audit_log: Optional[AuditLog] = None,
        permission_checker: Optional[
            Callable[[str, ToolExecutionContext], bool]
        ] = None,
    ) -> None:
        """
        初始化工具执行器

        Args:
            registry: 工具注册中心
            hook_registry: Hook 注册中心（None 时使用全局实例）
            audit_log: 审计日志（None 时自动创建）
            permission_checker: 自定义权限检查函数，返回 True 表示允许
        """
        self._registry = registry
        self._hook_registry = hook_registry or get_global_hook_registry()
        self._audit_log = audit_log or AuditLog()
        self._permission_checker = permission_checker
        logger.info("工具执行器初始化完成")

    @property
    def registry(self) -> ToolRegistry:
        """获取工具注册中心"""
        return self._registry

    @property
    def hook_registry(self) -> HookRegistry:
        """获取 Hook 注册中心"""
        return self._hook_registry

    @property
    def audit_log(self) -> AuditLog:
        """获取审计日志"""
        return self._audit_log

    # ---- 单次执行 ----

    async def execute(
        self,
        tool_name: str,
        params: Optional[Dict[str, Any]] = None,
        context: Optional[ToolExecutionContext] = None,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> ToolResult:
        """
        执行单个工具（带完整管线）

        Args:
            tool_name: 工具名称
            params: 工具参数
            context: 执行上下文
            on_progress: 进度回调

        Returns:
            工具执行结果
        """
        params = params or {}
        context = context or ToolExecutionContext()
        start_time = time.monotonic()

        audit_entry = AuditEntry(
            tool_name=tool_name,
            params=params,
            session_id=context.session_id,
            agent_id=context.agent_id,
            user_id=context.user_id,
        )

        # 1. 检查工具是否存在
        tool = self._registry.get_tool(tool_name)
        if tool is None:
            result = ToolResult(
                success=False, data=None, error=f"工具 '{tool_name}' 不存在"
            )
            audit_entry.result_success = False
            audit_entry.result_error = result.error
            audit_entry.duration_ms = _elapsed_ms(start_time)
            self._audit_log.record(audit_entry)
            return result

        # 2. 输入验证
        validation_error = tool.validate_parameters(**params)
        if validation_error:
            result = ToolResult(
                success=False, data=None, error=f"参数验证失败: {validation_error}"
            )
            audit_entry.result_success = False
            audit_entry.result_error = result.error
            audit_entry.duration_ms = _elapsed_ms(start_time)
            self._audit_log.record(audit_entry)
            return result

        # 3. PreToolUse hooks
        hook_ctx = HookContext(
            event=HookEvent.PRE_TOOL_USE,
            tool_name=tool_name,
            tool_params=params,
            session_id=context.session_id,
            agent_id=context.agent_id,
        )
        pre_decision = await execute_hooks(
            self._hook_registry, HookEvent.PRE_TOOL_USE, hook_ctx
        )

        if pre_decision.is_denied:
            reason = pre_decision.deny_reason or "被 Hook 阻断"
            result = ToolResult(success=False, data=None, error=reason)
            audit_entry.result_success = False
            audit_entry.result_error = reason
            audit_entry.hook_decision = "deny"
            audit_entry.denied_reason = reason
            audit_entry.duration_ms = _elapsed_ms(start_time)
            self._audit_log.record(audit_entry)
            logger.info(f"工具 {tool_name} 被 PreToolUse Hook 阻断: {reason}")
            return result

        if pre_decision.is_modified and pre_decision.modified_data:
            params.update(pre_decision.modified_data)
            audit_entry.hook_decision = "modify"
            logger.debug(f"工具 {tool_name} 参数被 PreToolUse Hook 修改")

        # 4. 权限检查
        if self._permission_checker is not None:
            if not self._permission_checker(tool_name, context):
                result = ToolResult(
                    success=False,
                    data=None,
                    error=f"权限不足: 不允许执行工具 '{tool_name}'",
                )
                audit_entry.result_success = False
                audit_entry.result_error = result.error
                audit_entry.duration_ms = _elapsed_ms(start_time)
                self._audit_log.record(audit_entry)
                return result

        # 5. 执行工具
        if on_progress:
            on_progress(f"正在执行工具: {tool_name}")

        tool_timeout = getattr(tool, "timeout", 60)
        try:
            result = await asyncio.wait_for(
                tool.execute(**params),
                timeout=tool_timeout,
            )
        except asyncio.TimeoutError:
            result = ToolResult(
                success=False,
                data=None,
                error=f"工具 '{tool_name}' 执行超时 ({tool_timeout}s)",
            )
        except Exception as exc:
            result = ToolResult(
                success=False,
                data=None,
                error=f"工具执行异常: {exc}",
            )

        # 计算执行时间
        elapsed = _elapsed_ms(start_time)
        result.duration_ms = elapsed

        # 结果截断
        max_size = getattr(tool, "max_result_size", 50000)
        if result.data is not None:
            data_str = str(result.data)
            if len(data_str) > max_size:
                result.data = data_str[:max_size] + f"\n... [截断，原始长度 {len(data_str)}]"
                result.truncated = True

        # 6. PostToolUse hooks
        post_ctx = HookContext(
            event=HookEvent.POST_TOOL_USE,
            tool_name=tool_name,
            tool_params=params,
            tool_result=result.to_dict(),
            session_id=context.session_id,
            agent_id=context.agent_id,
        )
        post_decision = await execute_hooks(
            self._hook_registry, HookEvent.POST_TOOL_USE, post_ctx
        )

        if post_decision.is_denied:
            reason = post_decision.deny_reason or "被 PostToolUse Hook 拒绝"
            result = ToolResult(success=False, data=None, error=reason)

        if post_decision.is_modified and post_decision.modified_data:
            # 允许 Hook 修改输出
            if "data" in post_decision.modified_data:
                result.data = post_decision.modified_data["data"]
            if "metadata" in post_decision.modified_data:
                result.metadata.update(post_decision.modified_data["metadata"])

        # 7. 审计记录
        audit_entry.result_success = result.success
        audit_entry.result_error = result.error
        audit_entry.duration_ms = _elapsed_ms(start_time)
        if audit_entry.hook_decision is None:
            audit_entry.hook_decision = "allow"
        self._audit_log.record(audit_entry)

        if on_progress:
            on_progress(f"工具 {tool_name} 执行完成 ({elapsed:.1f}ms)")

        return result

    # ---- 批量执行 ----

    async def execute_batch(
        self,
        tool_calls: List[Dict[str, Any]],
        context: Optional[ToolExecutionContext] = None,
    ) -> List[ToolResult]:
        """
        批量执行工具

        分区策略:
        - is_read_only 的工具并发执行（asyncio.gather）
        - 非只读工具按顺序串行执行

        保持输入顺序返回结果。

        Args:
            tool_calls: 工具调用列表，每项包含 {"tool_name": str, "params": dict}
            context: 执行上下文

        Returns:
            按输入顺序排列的结果列表
        """
        if not tool_calls:
            return []

        context = context or ToolExecutionContext()

        # 给每个调用标记原始索引
        indexed_calls = list(enumerate(tool_calls))
        results: List[Optional[ToolResult]] = [None] * len(tool_calls)

        # 分区: read-only vs non-read-only
        read_only_calls: List[tuple] = []
        serial_calls: List[tuple] = []

        for idx, call in indexed_calls:
            tool_name = call.get("tool_name", "")
            tool = self._registry.get_tool(tool_name)
            is_read_only = getattr(tool, "is_read_only", False) if tool else False
            if is_read_only:
                read_only_calls.append((idx, call))
            else:
                serial_calls.append((idx, call))

        # 并发执行只读工具
        if read_only_calls:
            async def _run(idx: int, call: Dict[str, Any]) -> tuple:
                r = await self.execute(
                    call.get("tool_name", ""),
                    call.get("params", {}),
                    context,
                )
                return idx, r

            concurrent_tasks = [
                _run(idx, call) for idx, call in read_only_calls
            ]
            concurrent_results = await asyncio.gather(
                *concurrent_tasks, return_exceptions=True
            )
            for item in concurrent_results:
                if isinstance(item, Exception):
                    logger.error(f"批量执行异常: {item}")
                    continue
                idx, r = item
                results[idx] = r

        # 串行执行非只读工具
        for idx, call in serial_calls:
            r = await self.execute(
                call.get("tool_name", ""),
                call.get("params", {}),
                context,
            )
            results[idx] = r

        # 填充未完成的位置
        final: List[ToolResult] = []
        for r in results:
            if r is None:
                final.append(ToolResult(success=False, data=None, error="执行未完成"))
            else:
                final.append(r)
        return final

    # ---- 辅助方法 ----

    def get_audit_entries(
        self,
        tool_name: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """查询审计日志"""
        return self._audit_log.query(
            tool_name=tool_name,
            session_id=session_id,
            limit=limit,
        )

    def __repr__(self) -> str:
        return (
            f"<ToolExecutor(tools={len(self._registry)}, "
            f"hooks={len(self._hook_registry)}, "
            f"audit_entries={len(self._audit_log)})>"
        )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _elapsed_ms(start: float) -> float:
    """计算从 start 到现在的毫秒数"""
    return (time.monotonic() - start) * 1000.0
