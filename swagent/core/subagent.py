"""
子 Agent 隔离模型 - 默认隔离，显式共享
参考 Claude Code forkedAgent.ts / createSubagentContext 设计

设计原则:
- 子 Agent 默认获得完全隔离的上下文 (文件缓存深拷贝、独立权限追踪)
- 父 Agent 的中止信号自动传播到子 Agent
- 可选择性地共享特定资源 (如文件缓存、中止事件)
"""
import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from swagent.core.denial_tracking import DenialTracker


@dataclass
class SubagentContext:
    """
    子 Agent 执行上下文

    包含子 Agent 运行所需的所有隔离资源。
    """
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: Optional[str] = None

    # 文件缓存 (默认深拷贝, 子不污染父)
    file_cache: Any = None

    # 工具权限
    allowed_tools: Optional[Set[str]] = None      # None = 全部允许
    disallowed_tools: Set[str] = field(default_factory=set)

    # 权限拒绝追踪 (默认独立)
    denial_tracker: DenialTracker = field(default_factory=DenialTracker)

    # 中止事件
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)

    # 工作目录
    working_dir: Optional[str] = None

    # 附加元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_tool_allowed(self, tool_name: str) -> bool:
        """检查工具是否被允许"""
        if tool_name in self.disallowed_tools:
            return False
        if self.allowed_tools is not None:
            return tool_name in self.allowed_tools
        return True

    def should_abort(self) -> bool:
        """检查是否应该中止"""
        return self.abort_event.is_set()

    def abort(self):
        """触发中止"""
        self.abort_event.set()


def create_subagent_context(
    parent_context: Optional[SubagentContext] = None,
    *,
    agent_id: Optional[str] = None,
    share_file_cache: bool = False,
    share_abort: bool = False,
    allowed_tools: Optional[Set[str]] = None,
    disallowed_tools: Optional[Set[str]] = None,
    working_dir: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> SubagentContext:
    """
    创建子 Agent 上下文

    默认行为 (隔离优先):
    - 文件缓存: 深拷贝 (子的修改不影响父)
    - 拒绝追踪: 全新实例
    - 中止事件: 新建 (但父中止会传播)
    - 工具权限: 继承父的限制 + 可额外约束

    Args:
        parent_context: 父 Agent 上下文 (None = 顶层 Agent)
        agent_id: 指定 agent ID (默认自动生成)
        share_file_cache: True = 共享父缓存引用 (适用于只读 Agent)
        share_abort: True = 共享父的 abort_event
        allowed_tools: 允许的工具白名单
        disallowed_tools: 额外禁止的工具
        working_dir: 工作目录覆盖
        metadata: 附加元数据

    Returns:
        隔离的 SubagentContext
    """
    ctx = SubagentContext(
        agent_id=agent_id or str(uuid.uuid4()),
        parent_id=parent_context.agent_id if parent_context else None,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools or set(),
        working_dir=working_dir,
        metadata=metadata or {},
    )

    if parent_context:
        # 文件缓存
        if share_file_cache:
            ctx.file_cache = parent_context.file_cache
        elif parent_context.file_cache is not None:
            # 深拷贝 (需要 file_cache 实现 clone 方法)
            if hasattr(parent_context.file_cache, 'clone'):
                ctx.file_cache = parent_context.file_cache.clone()
            else:
                ctx.file_cache = parent_context.file_cache  # fallback: 共享

        # 中止事件
        if share_abort:
            ctx.abort_event = parent_context.abort_event
        else:
            # 创建子级 abort，父中止时自动传播
            child_event = asyncio.Event()
            ctx.abort_event = child_event
            _propagate_abort(parent_context.abort_event, child_event)

        # 工具权限: 继承父的限制
        if parent_context.disallowed_tools:
            ctx.disallowed_tools = ctx.disallowed_tools | parent_context.disallowed_tools

        # 工作目录
        if not working_dir and parent_context.working_dir:
            ctx.working_dir = parent_context.working_dir

    return ctx


def _propagate_abort(parent_event: asyncio.Event, child_event: asyncio.Event):
    """父中止信号传播到子 (非阻塞)"""
    async def _watcher():
        while not parent_event.is_set():
            await asyncio.sleep(0.5)
        child_event.set()

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_watcher())
    except RuntimeError:
        pass  # 没有运行中的事件循环，跳过
