# Wuyu-Agent 框架技术总览 (二次开发指南)

> 本文档面向需要对 Wuyu-Agent 进行二次开发的工程师，全面覆盖框架架构、核心模块设计细节与扩展方法。

---

## 目录

- [1. Agentic Loop 引擎](#1-agentic-loop-引擎)
- [2. Hook 系统](#2-hook-系统)
- [3. 工具执行管线](#3-工具执行管线)
- [4. 缓存系统](#4-缓存系统)
- [5. 子 Agent 隔离](#5-子-agent-隔离)
- [6. 会话持久化](#6-会话持久化)
- [7. 分层配置](#7-分层配置)
- [8. Skill 系统](#8-skill-系统)
- [9. 错误处理](#9-错误处理)
- [10. 进度报告](#10-进度报告)
- [11. CLI 入口](#11-cli-入口)
- [12. StateGraph 工作流引擎](#12-stategraph-工作流引擎)
- [二次开发指南](#二次开发指南)

---

## 1. Agentic Loop 引擎

**文件**: `swagent/core/agentic_loop.py`

Agentic Loop 是整个框架的核心驱动循环，实现了 LLM 与工具之间的自动化交互闭环。其设计参考了 Claude Code 的 `queryLoop` 模式。

### 1.1 核心循环流程

```
┌─────────────────────────────────────────────────────┐
│                    Agentic Loop                      │
│                                                     │
│  ┌──────────┐    ┌──────────────┐    ┌───────────┐  │
│  │ 调用 LLM │───>│ 检查 tool_calls│───>│ 执行工具  │  │
│  └──────────┘    └──────────────┘    └───────────┘  │
│       ^                                    │        │
│       │          ┌──────────────┐          │        │
│       └──────────│ 追加结果到消息 │<─────────┘        │
│                  └──────────────┘                    │
│                                                     │
│  终止条件:                                           │
│  - LLM 不再请求 tool_call (finish_reason=stop)       │
│  - 达到最大轮次 max_turns                             │
│  - 发生不可恢复的错误                                  │
└─────────────────────────────────────────────────────┘
```

每一轮循环的步骤：

1. **调用 LLM** -- 将当前消息列表（含历史工具结果）发送给 LLM
2. **检查 finish_reason** -- 若为 `length` 则触发自动恢复（见 1.3）
3. **检查 tool_calls** -- 若无工具调用则循环结束，yield `complete` 事件
4. **执行工具** -- 逐个解析参数、调用 `tool_registry.execute_tool()`
5. **追加结果** -- 将工具结果以 `role: tool` 追加到消息列表
6. **下一轮** -- 重复步骤 1

### 1.2 AsyncGenerator 事件流模式

`agentic_loop()` 函数是一个异步生成器，通过 `yield` 产出事件字典，使调用方可以实时订阅循环进度：

```python
async for event in agentic_loop(client, messages, tool_registry, config):
    match event["type"]:
        case "llm_start":      ...  # LLM 调用开始
        case "llm_response":   ...  # 收到 LLM 响应
        case "tool_start":     ...  # 工具开始执行
        case "tool_result":    ...  # 工具执行完成
        case "recovery":       ...  # 自动恢复（length 截断）
        case "complete":       ...  # 循环正常结束
        case "error":          ...  # 发生错误
```

每个事件均包含 `type`、`data`、`timestamp` 三个字段。

同时提供 `run_agentic_loop()` 便捷包装函数，消费所有事件并返回 `AgenticLoopResult`：

```python
result = await run_agentic_loop(client, messages, tool_registry, config, on_progress=callback)
print(result.content)        # 最终文本
print(result.turns_used)     # 使用的轮次数
print(result.total_tokens)   # 总 token 消耗
```

### 1.3 Max Output Tokens 自动恢复

当 LLM 返回 `finish_reason=length`（输出被截断）且当前没有 tool_calls 时，引擎会：

1. 将部分响应追加为 assistant 消息
2. 注入续写提示：`"你的回复被截断了，请从上次停止的地方继续。"`
3. yield `recovery` 事件
4. 继续循环

最多重试 `max_length_retries` 次（默认 3 次），超限后返回当前内容。**成功执行一次工具调用后，length 重试计数会重置为 0**。

### 1.4 关键类

| 类 | 说明 |
|---|---|
| `AgenticLoopConfig` | 循环配置：`max_turns`(20), `max_length_retries`(3), `temperature`(0.7), `max_tokens`(4096), `tool_choice`("auto") |
| `AgenticLoopResult` | 最终结果：`content`, `messages`, `turns_used`, `total_tokens`, `finish_reason` |

### 1.5 Fallback 模型降级

框架通过 `error_handler.py` 中的 `FallbackModelError` 和 `suggest_recovery()` 支持模型降级策略。当主模型调用失败时，循环 yield `error` 事件，上层调用者可根据 `RecoveryStrategy.FALLBACK_MODEL` 切换到备用模型重启循环。

### 1.6 AutoCompact 集成

`swagent/core/auto_compact.py` 提供自动上下文压缩能力。当 token 使用量接近阈值时：

```python
compact = AutoCompact(CompactConfig(token_threshold=80000, keep_recent=10))

if compact.should_compact(total_tokens):
    result = await compact.compact_with_llm(messages, llm_client, total_tokens)
    if result.success:
        messages[:] = result.compacted_messages
```

两种压缩模式：
- **LLM 摘要压缩** (`compact_with_llm`)：保留 system 消息和最近 N 条消息，对其余消息调用 LLM 生成摘要
- **MicroCompact** (`micro_compact`)：轻量级方案，仅将旧的 tool 消息内容替换为 `[结果已压缩]` 占位符

内置熔断机制：连续失败 `circuit_breaker_limit` 次后停止尝试压缩，避免死循环。

---

## 2. Hook 系统

**文件**: `swagent/core/hooks.py`

Hook 系统为工具执行生命周期提供了可插拔的拦截点，支持审计、权限控制和数据修改。

### 2.1 HookEvent 枚举

```python
class HookEvent(Enum):
    PRE_TOOL_USE   = "PreToolUse"    # 工具执行前（可阻断、修改输入）
    POST_TOOL_USE  = "PostToolUse"   # 工具执行后（可修改输出、审计）
    PRE_LOOP       = "PreLoop"       # agentic loop 每轮开始前
    POST_LOOP      = "PostLoop"      # agentic loop 结束后
    ON_ERROR       = "OnError"       # 错误发生时
    SESSION_START  = "SessionStart"  # 会话开始
    SESSION_END    = "SessionEnd"    # 会话结束
```

### 2.2 三种 Hook 类型

#### CallbackHook -- Python 异步函数

```python
async def my_audit_hook(ctx: HookContext) -> HookDecision:
    logger.info(f"工具 {ctx.tool_name} 被调用，参数: {ctx.tool_params}")
    return HookDecision.allow()

registry.on(HookEvent.PRE_TOOL_USE, my_audit_hook, description="审计日志")
```

#### CommandHook -- 执行 Shell 命令

通过 stdin 向子进程传入 JSON 上下文，通过 exit code 判断决策：
- **exit code 0**: 允许（stdout 可选返回 JSON `{action, reason, modified_data}`）
- **exit code 2**: 阻断（stdout/stderr 中的 reason 字段说明原因）
- **其他 exit code**: 容错视为 allow

```python
registry.on_command(
    HookEvent.PRE_TOOL_USE,
    command="python /path/to/policy_check.py",
    matcher="file_*",
    timeout=30.0,
    description="文件操作策略检查"
)
```

#### HttpHook -- POST 到外部 API

将上下文以 JSON POST 到指定 URL，解析响应中的 `{action, reason, modified_data}`：

```python
registry.on_http(
    HookEvent.POST_TOOL_USE,
    url="https://audit.example.com/hook",
    headers={"Authorization": "Bearer xxx"},
    timeout=10.0,
    description="外部审计服务"
)
```

### 2.3 HookDecision 决策

```python
@dataclass
class HookDecision:
    action: HookAction   # ALLOW / DENY / MODIFY
    reason: Optional[str]
    modified_data: Optional[Dict[str, Any]]
```

快捷构造方法：
- `HookDecision.allow()`
- `HookDecision.deny("原因")`
- `HookDecision.modify({"param_key": "new_value"}, reason="参数修正")`

### 2.4 聚合规则

多个 Hook 按 `priority` 降序依次执行，聚合规则：

1. **任一 Hook 返回 DENY** -> 最终为 DENY（短路，后续 Hook 不再执行）
2. **有 MODIFY 且无 DENY** -> 最终为 MODIFY（合并所有 `modified_data`，后执行的覆盖先执行的同名字段）
3. **全部 ALLOW** -> 最终为 ALLOW

聚合结果通过 `AggregatedDecision` 返回，包含 `is_denied`、`is_modified`、`deny_reason` 等便捷属性。

### 2.5 Matcher 工具匹配模式

每个 HookDefinition 可指定 `matcher` 字段，用于限定 Hook 仅对特定工具生效：

- `None` / `"*"` -- 匹配所有工具
- `"read_file"` -- 精确匹配
- `"file_*"` -- 通配符匹配（内部转换为正则 `file_.*`）

### 2.6 审计日志

`HookRegistry` 内置审计日志列表 `_audit_log`，每次 Hook 执行后自动记录：

```python
{
    "hook_id": "abc12345",
    "event": "PreToolUse",
    "tool_name": "emission_calculator",
    "action": "allow",
    "reason": None,
    "duration_ms": 1.2,
    "timestamp": 1711843200.0
}
```

通过 `registry.audit_log` 访问，`registry.clear_audit_log()` 清空。

### 2.7 全局单例

```python
from swagent.core.hooks import get_global_hook_registry
registry = get_global_hook_registry()
```

---

## 3. 工具执行管线

**文件**: `swagent/tools/tool_executor.py`

ToolExecutor 封装了工具调用的完整管线，将 Hook、权限检查和审计日志集成到统一的执行流程中。

### 3.1 完整管线流程

```
输入验证 → PreToolUse Hooks → 权限检查 → 执行工具 → PostToolUse Hooks → 审计记录
   │              │                │           │              │             │
   │ 参数缺失?    │ is_denied?     │ 不允许?   │ 超时/异常?   │ is_denied?  │
   │ → 返回错误   │ → 返回拒绝     │ → 返回权限  │ → 返回错误   │ → 覆盖结果  │
   ▼              ▼                ▼           ▼              ▼             ▼
                                                             结果截断
                                                    (max_result_size)
```

详细步骤：

1. **检查工具是否存在** -- 通过 `ToolRegistry.get_tool()` 查找
2. **输入验证** -- 调用 `tool.validate_parameters()` 校验参数
3. **PreToolUse Hooks** -- 调用 `execute_hooks()`，若 DENY 则返回拒绝；若 MODIFY 则更新参数
4. **权限检查** -- 若配置了 `permission_checker` 回调，则调用之
5. **执行工具** -- 带 `asyncio.wait_for` 超时控制，超时时间取自 `tool.timeout`
6. **结果截断** -- 若 `result.data` 字符数超过 `tool.max_result_size`(默认 50000)，截断并标记 `truncated=True`
7. **PostToolUse Hooks** -- 可修改 `data` 和 `metadata`，也可 DENY（覆盖为错误结果）
8. **审计记录** -- 写入 `AuditEntry`，包含时间戳、耗时、hook 决策等

### 3.2 并发执行策略

`execute_batch()` 支持批量工具调用，分区策略：

```python
# 分区
for call in tool_calls:
    tool = registry.get_tool(call["tool_name"])
    if getattr(tool, "is_read_only", False):
        read_only_calls.append(call)   # → asyncio.gather 并发
    else:
        serial_calls.append(call)      # → 顺序串行

# 执行
concurrent_results = await asyncio.gather(*read_only_tasks, return_exceptions=True)
for call in serial_calls:
    result = await self.execute(call)
```

**只读工具**（`is_read_only=True`）并发执行，**非只读工具**串行执行，最终按原始顺序返回结果。

### 3.3 AuditLog 审计日志

```python
@dataclass
class AuditEntry:
    id: str                        # UUID[:12]
    timestamp: str                 # ISO 8601
    tool_name: str
    params: Optional[Dict]
    result_success: Optional[bool]
    result_error: Optional[str]
    duration_ms: float
    session_id: str
    agent_id: str
    user_id: str
    hook_decision: Optional[str]   # allow / deny / modify
    denied_reason: Optional[str]
```

`AuditLog` 支持按 `tool_name` 和 `session_id` 查询，自动保留最新 `max_entries`(默认 10000) 条记录。

---

## 4. 缓存系统

**文件**: `swagent/cache/`

缓存系统包含三个组件，分别解决文件重复读取、工具结果过大、prompt cache 优化三个问题。

### 4.1 FileStateCache -- 文件状态缓存

**文件**: `swagent/cache/file_cache.py`

LRU 缓存，避免重复磁盘读取。

```python
cache = FileStateCache(max_entries=100, max_size_bytes=25*1024*1024)
cache.put("/path/to/file", content)
state = cache.get("/path/to/file")  # FileState 或 None
```

**双重淘汰策略**:
- **条目数上限** (`max_entries=100`): 超出时淘汰最旧的条目
- **总字节数上限** (`max_size_bytes=25MB`): 超出时持续淘汰最旧条目直到低于限制
- 单条目超过总限制一半时直接丢弃不缓存

**路径标准化**: 使用 `os.path.normpath(os.path.abspath(os.path.expanduser()))` 消除 `..`、符号链接等差异。

**子 Agent 隔离**: `clone()` 方法深拷贝整个缓存，子 Agent 的修改不影响父 Agent：

```python
child_cache = parent_cache.clone()  # 独立副本
```

### 4.2 ResultBudgetManager -- 结果预算管理

**文件**: `swagent/cache/result_budget.py`

防止大工具结果撑爆上下文窗口。

**两级预算**:

| 级别 | 参数 | 默认值 | 行为 |
|------|------|--------|------|
| 单工具级 | `max_per_tool` | 50,000 字符 | 超限结果持久化到磁盘，返回预览引用 |
| 消息聚合级 | `max_per_message` | 200,000 字符 | 同一轮所有工具结果总和超限时，从最大的开始持久化 |

**磁盘持久化与预览**:

```python
mgr = ResultBudgetManager(max_per_tool=50000, max_per_message=200000)
text = mgr.process_result("tool-call-id-1", "grep_tool", huge_result_text)
# 若超限，text 变为:
# "[结果已持久化] 工具: grep_tool, 原始大小: 128.5KB
#  完整输出已保存至: ~/.swagent/tool-results/tool-call-id-1.txt
#  预览(前2000字符):
#  ..."
```

### 4.3 SystemPromptBuilder -- Prompt 缓存优化

**文件**: `swagent/cache/prompt_cache.py`

将系统提示词分为**静态区段**和**动态区段**，中间插入边界标记，使 API prompt cache 能缓存不变的静态部分。

```python
builder = SystemPromptBuilder()
# 静态部分 -- 全局可缓存
builder.add_static("identity", "你是固废管理专家...")
builder.add_static("domain", waste_knowledge_text)
# 动态部分 -- 每会话变化
builder.add_dynamic("tools", "当前可用工具: ...")
builder.add_dynamic("env", "当前时间: 2026-03-31")

prompt = builder.build()
# 输出: 静态部分 + "\n\n# ---- 以下为动态内容 (每会话变化) ----\n\n" + 动态部分
```

**CacheSafeParams -- 父子共享 prompt cache**:

```python
params = builder.to_cache_safe_params(model="gpt-4")
# CacheSafeParams(system_prompt=..., tools_signature="a1b2c3d4", model="gpt-4")
```

父 Agent 每轮结束后保存 `CacheSafeParams`，子 Agent 复用同一静态 prompt，从而命中 API 的 prompt cache。

---

## 5. 子 Agent 隔离

**文件**: `swagent/core/subagent.py`

设计原则：**默认隔离，显式共享**。

### 5.1 SubagentContext

```python
@dataclass
class SubagentContext:
    agent_id: str              # 唯一标识
    parent_id: Optional[str]   # 父 Agent ID
    file_cache: Any            # 文件缓存（默认深拷贝）
    allowed_tools: Optional[Set[str]]   # 工具白名单（None=全部允许）
    disallowed_tools: Set[str]          # 工具黑名单
    denial_tracker: DenialTracker       # 权限拒绝追踪（独立实例）
    abort_event: asyncio.Event          # 中止事件
    working_dir: Optional[str]          # 工作目录
    metadata: Dict[str, Any]            # 附加元数据
```

### 5.2 创建子 Agent 上下文

```python
from swagent.core.subagent import create_subagent_context

child_ctx = create_subagent_context(
    parent_context=parent_ctx,
    allowed_tools={"emission_calculator", "visualizer"},  # 白名单限制
    disallowed_tools={"code_executor"},                    # 额外禁止
    share_file_cache=False,    # 默认: 深拷贝文件缓存
    share_abort=False,         # 默认: 独立 abort 事件
)
```

### 5.3 默认隔离行为

| 资源 | 默认行为 | 可选共享 |
|------|----------|----------|
| 文件缓存 | 深拷贝 (`file_cache.clone()`) | `share_file_cache=True` 共享引用 |
| 权限拒绝追踪 | 全新 `DenialTracker` 实例 | 不可共享 |
| 中止事件 | 新建 `asyncio.Event`，但父中止自动传播 | `share_abort=True` 共享同一事件 |
| 工具权限 | 继承父的 `disallowed_tools` | 可通过白名单进一步限制 |
| 工作目录 | 继承父的 `working_dir` | 可覆盖 |

### 5.4 父 Abort 传播

当 `share_abort=False` 时，框架创建一个后台 watcher task 轮询父事件，父中止后自动 set 子事件：

```python
async def _watcher():
    while not parent_event.is_set():
        await asyncio.sleep(0.5)
    child_event.set()
```

### 5.5 工具权限继承

子 Agent 的禁止工具列表 = 子自身 `disallowed_tools` **并集** 父 `disallowed_tools`：

```python
ctx.disallowed_tools = ctx.disallowed_tools | parent_context.disallowed_tools
```

---

## 6. 会话持久化

**文件**: `swagent/core/session_storage.py`

### 6.1 JSONL 存储格式

每条消息以 JSON 格式独占一行（JSONL），追加写入。消息自动附加 `_timestamp` 和 `_session_id` 元数据。

### 6.2 目录结构

```
~/.swagent/sessions/{session_id}/
├── main.jsonl                      # 主对话记录
├── subagents/
│   └── {agent_id}.jsonl            # 子 Agent 对话
├── tool-results/
│   └── {tool_use_id}.json          # 持久化的大工具结果
└── metadata.json                   # 会话元数据
```

### 6.3 SessionMetadata

```python
@dataclass
class SessionMetadata:
    session_id: str
    created_at: str          # ISO 8601
    updated_at: str          # 每次写入消息时更新
    message_count: int
    agent_ids: List[str]     # 参与的 agent ID 列表
    domain: str
    description: str
```

### 6.4 核心 API

```python
storage = SessionStorage()                            # 默认 ~/.swagent/sessions/

# 记录消息
storage.record_message(session_id, {"role": "user", "content": "..."})
storage.record_subagent_message(session_id, agent_id, message)

# 加载
messages = storage.load_session(session_id)            # List[Dict]
sub_msgs = storage.load_subagent_session(session_id, agent_id)

# 列出/导出/删除
sessions = storage.list_sessions()                     # List[SessionMetadata]
json_str = storage.export_session(session_id, format="json")
md_str   = storage.export_session(session_id, format="markdown")
storage.delete_session(session_id)
```

---

## 7. 分层配置

**文件**: `swagent/core/layered_settings.py`

### 7.1 五级优先级

```
高  ┌─────────────┐
    │   POLICY    │  管理员强制策略 (~/.swagent/policy.yaml)
    ├─────────────┤
    │    ENV      │  环境变量 SWAGENT_*
    ├─────────────┤
    │   LOCAL     │  本地配置 (.swagent/local.yaml, gitignored)
    ├─────────────┤
    │  PROJECT    │  项目共享配置 (.swagent/config.yaml)
    ├─────────────┤
    │   USER      │  用户全局配置 (~/.swagent/config.yaml)
    ├─────────────┤
低  │  DEFAULT    │  框架内置默认值
    └─────────────┘
```

### 7.2 深度合并算法

嵌套字典递归合并，其他类型直接覆盖：

```python
def _deep_merge(base: Dict, override: Dict) -> Dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
```

例如：
```yaml
# USER (base)                    # LOCAL (override)
llm:                              llm:
  model: gpt-4                     temperature: 0.3
  temperature: 0.7

# 合并结果:
# llm: { model: gpt-4, temperature: 0.3 }
```

### 7.3 环境变量映射

规则：`SWAGENT_XX_YY` -> `xx.yy`（前缀 `SWAGENT_` 后的部分转小写，`__` 替换为 `.`）

```bash
export SWAGENT_LLM__MODEL=gpt-4o       # → llm.model = "gpt-4o"
export SWAGENT_TOOLS__TIMEOUT=120       # → tools.timeout = "120"
```

### 7.4 get_with_source() 调试

```python
settings = LayeredSettings()
settings.load()

value, source = settings.get_with_source("llm.model")
print(f"llm.model = {value}  (来源: {source.name})")
# 输出: llm.model = gpt-4o  (来源: ENV)
```

### 7.5 使用示例

```python
settings = LayeredSettings(project_dir="/path/to/project")
settings.set_defaults({
    "llm": {"model": "gpt-4", "temperature": 0.7},
    "tools": {"timeout": 60},
})
settings.load()

model = settings.get("llm.model")                       # 最高优先级值
settings.set("llm.temperature", 0.5, SettingSource.LOCAL)  # 写入本地配置
```

---

## 8. Skill 系统

**文件**: `skills/` 目录

Skill 系统使用 Markdown 文件定义可复用的领域任务模板，包含参数定义、工具清单和 prompt 模板。

### 8.1 Markdown 文件格式

每个 Skill 是一个 `.md` 文件，包含 YAML frontmatter 和 Markdown 正文：

```markdown
---
name: 排放计算报告
description: 根据废物类型和处理方式计算碳排放量并生成对比分析报告
domain: waste
tools:
  - emission_calculator
  - visualizer
  - file_handler
parameters:
  - name: waste_type
    type: string
    required: true
    description: 废物类型
  - name: treatment_method
    type: string
    required: true
    description: 处理方式
  - name: quantity_tons
    type: number
    required: false
    description: 废物总量 (吨), 默认1000吨
---

## 执行步骤
1. 获取排放因子
2. 计算排放量
...

## Prompt
你是固体废物碳排放分析专家...
用户需要计算 **{waste_type}** 采用 **{treatment_method}** 处理时的碳排放量。
...
```

### 8.2 SkillDefinition 结构

从 frontmatter 解析出的数据结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | str | 技能名称 |
| `description` | str | 技能描述 |
| `domain` | str | 所属领域 |
| `tools` | List[str] | 依赖的工具名列表 |
| `parameters` | List[Dict] | 参数定义（name, type, required, description） |
| `prompt_template` | str | Prompt 正文（Markdown 中 `## Prompt` 之后的内容） |

### 8.3 三个示例 Skill

| Skill 文件 | 名称 | 描述 | 依赖工具 |
|------------|------|------|----------|
| `emission_report.md` | 排放计算报告 | 计算碳排放并生成对比分析 | emission_calculator, visualizer, file_handler |
| `compliance_check.md` | 合规性检查 | 按国标检查固废处理设施合规性 | file_handler |
| `site_analysis.md` | 选址分析 | 多维度评估固废处理设施选址 | location_tool, weather_tool, imagery_tool, emission_calculator |

### 8.4 SkillRegistry 机制

SkillRegistry 负责发现、注册和渲染 Skill：

- **discover**: 扫描 `skills/` 目录下的 `.md` 文件
- **register**: 解析 frontmatter 并注册为 SkillDefinition
- **render_prompt**: 用用户提供的参数填充 `{placeholder}` 后返回完整 prompt

```python
# 渲染 Skill prompt
prompt = skill_registry.render_prompt("排放计算报告", {
    "waste_type": "food_waste",
    "treatment_method": "composting",
    "quantity_tons": 500
})
```

---

## 9. 错误处理

**文件**: `swagent/core/error_handler.py`

### 9.1 classify_error() -- 遥测安全分类

将异常分类为不包含代码内容或文件路径的安全字符串，适用于遥测上报：

```python
classify_error(ToolExecutionError("calc", TimeoutError()))
# → "ToolError:calc:Timeout"

classify_error(FallbackModelError("gpt-4", ConnectionError()))
# → "FallbackModel:gpt-4"

classify_error(ContextOverflowError(150000, 128000))
# → "ContextOverflow"
```

分类规则：
- `ToolExecutionError` -> `"ToolError:{tool_name}:{inner_class}"`
- `FallbackModelError` -> `"FallbackModel:{model}"`
- `ContextOverflowError` -> `"ContextOverflow"`
- `TimeoutError` / `ConnectionError` / `PermissionError` / `FileNotFoundError` -> 类名
- 已知异常类名（长度>3 且非 `Exception`）-> 截取前 60 字符
- 其他 -> `"Error"`

### 9.2 format_error_for_llm() -- 头尾截断

超长错误文本保留头尾各 40%，中间省略：

```python
format_error_for_llm(error, max_length=10000)
# → "前4000字符...\n\n... [省略 12000 字符] ...\n\n后4000字符"
```

### 9.3 RecoveryStrategy 枚举

```python
class RecoveryStrategy(Enum):
    RETRY          = "retry"           # 重试
    FALLBACK_MODEL = "fallback_model"  # 模型降级
    COMPACT        = "compact"         # 压缩上下文后重试
    SKIP           = "skip"            # 跳过
    ABORT          = "abort"           # 中止
```

`suggest_recovery()` 根据异常类型推荐策略：

| 异常类型 | 推荐策略 |
|----------|----------|
| `ContextOverflowError` | COMPACT |
| `FallbackModelError` | FALLBACK_MODEL |
| `TimeoutError` / `ConnectionError` | RETRY |
| `ToolExecutionError` | SKIP |
| 其他 | ABORT |

### 9.4 DenialTracker 熔断器

**文件**: `swagent/core/denial_tracking.py`

追踪工具执行中的权限拒绝次数，防止 LLM 反复请求被拒绝的工具。

```python
tracker = DenialTracker(max_consecutive=3, max_total=20)

tracker.record_denial("code_executor", "安全策略禁止")
tracker.record_denial("code_executor", "安全策略禁止")
tracker.record_denial("code_executor", "安全策略禁止")

tracker.should_fallback()  # True -- 连续拒绝 3 次，触发降级

tracker.record_success()   # 重置连续计数
tracker.should_fallback()  # False (但累计仍在统计)
```

触发条件（任一满足）：
- 连续拒绝 >= `max_consecutive` (默认 3)
- 累计拒绝 >= `max_total` (默认 20)

---

## 10. 进度报告

**文件**: `swagent/core/progress.py`

### 10.1 ProgressEvent

```python
@dataclass
class ProgressEvent:
    event_type: ProgressEventType   # 事件类型枚举
    tool_name: Optional[str]        # 相关工具名
    message: str                    # 人类可读消息
    data: Optional[Dict[str, Any]]  # 附加数据
    timestamp: float                # 时间戳
```

**ProgressEventType 枚举**:

| 值 | 说明 |
|---|---|
| `LLM_START` | LLM 调用开始 |
| `LLM_RESPONSE` | LLM 响应返回 |
| `TOOL_START` | 工具开始执行 |
| `TOOL_RESULT` | 工具执行完成（成功/失败） |
| `RECOVERY` | 自动恢复 |
| `COMPACT` | 上下文压缩 |
| `COMPLETE` | 循环完成 |
| `ERROR` | 错误 |

### 10.2 ProgressReporter

支持同步和异步回调：

```python
reporter = ProgressReporter()

# 同步回调
reporter.add_callback(lambda event: print(f"[{event.event_type.value}] {event.message}"))

# 异步回调
reporter.add_async_callback(async_log_handler)

# 发射事件
reporter.emit_tool_start("emission_calculator", args={"waste_type": "food_waste"})
reporter.emit_tool_result("emission_calculator", success=True)

# 异步发射（同时触发同步和异步回调）
await reporter.emit_async(ProgressEvent(
    event_type=ProgressEventType.COMPLETE,
    message="循环完成"
))

# 获取历史
events = reporter.history    # List[ProgressEvent]
reporter.clear_history()
```

---

## 11. CLI 入口

**文件**: `swagent/cli.py`（推断，基于框架结构）

### 11.1 统一入口命令

```bash
swagent chat       # 交互式 REPL 对话
swagent serve      # 启动 HTTP API 服务
swagent detect     # 固废检测任务（多领域检测）
swagent run        # 运行 StateGraph 工作流
swagent skill      # 列出/执行 Skill
```

### 11.2 REPL 交互模式

`swagent chat` 进入交互式 REPL，支持 slash 命令：

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/tools` | 列出当前可用工具 |
| `/skill <name>` | 加载并执行指定 Skill |
| `/history` | 显示对话历史 |
| `/export` | 导出当前会话 |
| `/clear` | 清空上下文 |
| `/quit` | 退出 |

### 11.3 领域化工具加载

根据选择的领域（domain）动态加载对应的工具集：

```python
# waste 领域加载
registry.register(EmissionCalculator())
registry.register(WeatherTool())
registry.register(LocationTool())
registry.register(ImageryTool())
registry.register(Visualizer())
```

---

## 12. StateGraph 工作流引擎

**文件**: `swagent/stategraph/`

StateGraph 是一个类 LangGraph 的状态图工作流引擎，支持构建多节点、条件路由、循环执行的工作流。

### 12.1 核心组件

| 组件 | 文件 | 说明 |
|------|------|------|
| `StateGraph` | `graph.py` | 图构建器，注册节点和边 |
| `CompiledGraph` | `graph.py` | 编译后的可执行图，提供 `invoke`/`stream` 方法 |
| `Node` | `node.py` | 节点封装，支持配置和重试 |
| `Edge` | `edge.py` | 边定义，支持固定路由、条件路由、并行路由 |
| `StateManager` | `state.py` | 状态管理，处理状态更新和合并策略 |
| `Persistence` | `persistence.py` | 检查点持久化（内存 / 本地文件） |
| `ErrorHandler` | `errors.py` | 重试逻辑、回退策略、自定义错误处理 |

### 12.2 基本用法

```python
from swagent.stategraph import StateGraph, START, END, ExecutionConfig
from typing import TypedDict

class MyState(TypedDict):
    input: str
    result: str

graph = StateGraph(MyState)

@graph.node()
async def process(state: MyState) -> dict:
    return {"result": state["input"].upper()}

graph.set_entry_point("process")
graph.set_exit_point("process")

app = graph.compile()
result = await app.invoke({"input": "hello"})
print(result.state["result"])  # "HELLO"
```

### 12.3 条件路由与循环

```python
# 条件路由
graph.add_conditional_edge(
    "init_workflow",
    lambda state: "test" if state["mode"] == "test" else "prod",
    {"test": "load_and_split_image", "prod": "load_tile_list"}
)

# 循环（条件回边）
graph.add_conditional_edge(
    "process_single_tile",
    check_progress,
    {"continue": "process_single_tile", "done": "aggregate_results"}
)
```

### 12.4 与 Agentic Loop 的集成

在 StateGraph 工作流中，可以在任意节点内调用 `agentic_loop` 来实现 LLM 驱动的子任务：

```python
@graph.node()
async def generate_report(state: MyState) -> dict:
    result = await run_agentic_loop(
        client=llm_client,
        messages=[{"role": "user", "content": f"根据以下数据生成报告: {state['data']}"}],
        tool_registry=tool_registry,
    )
    return {"report": result.content}
```

### 12.5 实际案例：城市固废监测工作流

`swagent/waste_monitoring/workflow.py` 是一个完整的 StateGraph 应用示例：

```
init_workflow
    │
    ├── [mode=test] → load_and_split_image ─┐
    └── [mode=prod] → load_tile_list ───────┤
                                             ▼
                                    process_single_tile ←──┐
                                             │             │
                                         check_progress    │
                                         /        \        │
                                      done     continue ───┘
                                       │
                                aggregate_results
                                       │
                                fetch_external_data
                                       │
                                generate_report
                                       │
                                save_output
```

工作流状态通过 `WasteMonitoringState`（TypedDict）在节点间传递，每个节点返回部分更新的字典，由 StateManager 合并到全局状态。

### 12.6 ExecutionConfig

```python
config = ExecutionConfig(
    max_iterations=100,         # 防止无限循环
    save_checkpoints=True,      # 启用检查点
    interrupt_before={"generate_report"},  # 在指定节点前暂停
    timeout=3600,               # 全局超时（秒）
)
result = await app.invoke(initial_state, config=config)
```

---

## 二次开发指南

### 指南 1: 如何添加新工具

**步骤 1**: 创建工具类，继承 `BaseTool`

```python
# swagent/tools/domain/my_new_tool.py

from typing import List, Dict, Optional, Callable
from swagent.tools.base_tool import BaseTool, ToolCategory, ToolParameter, ToolResult


class MyNewTool(BaseTool):
    """我的自定义工具"""

    @property
    def name(self) -> str:
        return "my_new_tool"

    @property
    def description(self) -> str:
        return "执行自定义操作，返回处理结果"

    @property
    def category(self) -> ToolCategory:
        return ToolCategory.DOMAIN

    @property
    def is_read_only(self) -> bool:
        """只读工具可以并发执行"""
        return True

    @property
    def timeout(self) -> float:
        """自定义超时秒数"""
        return 30.0

    @property
    def max_result_size(self) -> int:
        """结果最大字符数"""
        return 50000

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="input_data",
                type="string",
                description="输入数据",
                required=True,
            ),
            ToolParameter(
                name="mode",
                type="string",
                description="处理模式",
                required=False,
                default="default",
                enum=["default", "advanced"],
            ),
        ]

    def get_return_description(self) -> str:
        return "返回包含处理结果的字典"

    def get_examples(self) -> List[Dict]:
        return [
            {
                "input": {"input_data": "example", "mode": "default"},
                "output": {"result": "processed_example"}
            }
        ]

    async def execute(
        self,
        on_progress: Optional[Callable[[str], None]] = None,
        **kwargs
    ) -> ToolResult:
        input_data = kwargs["input_data"]
        mode = kwargs.get("mode", "default")

        if on_progress:
            on_progress(f"正在处理: {input_data[:50]}...")

        try:
            # 你的业务逻辑
            result = {"result": f"processed_{input_data}", "mode": mode}

            return ToolResult(
                success=True,
                data=result,
                metadata={"source": "my_new_tool"}
            )
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))
```

**步骤 2**: 注册工具

```python
from swagent.tools.tool_registry import get_global_registry
from swagent.tools.domain.my_new_tool import MyNewTool

registry = get_global_registry()
registry.register(MyNewTool())
# 或者
registry.register_class(MyNewTool)
```

**关键属性说明**:

| 属性 | 作用 | 默认值 |
|------|------|--------|
| `is_read_only` | True 时可在 `execute_batch` 中并发执行 | False |
| `is_destructive` | 标记破坏性操作（如删除文件） | False |
| `timeout` | 工具执行超时秒数 | 60.0 |
| `max_result_size` | 超过此字符数的结果会被截断 | 50000 |

---

### 指南 2: 如何添加新 Skill

**步骤 1**: 创建 Markdown 文件 `skills/my_skill.md`

```markdown
---
name: 我的自定义技能
description: 技能描述
domain: my_domain
tools:
  - my_new_tool
  - file_handler
parameters:
  - name: param1
    type: string
    required: true
    description: 参数1说明
  - name: param2
    type: number
    required: false
    description: 参数2说明（可选）
---

## 执行步骤

1. 第一步说明
2. 第二步说明
3. 第三步说明

## Prompt

你是 {domain} 领域的专家。

用户需要处理 **{param1}** 相关任务。

请按以下步骤执行：

### 第一步：数据获取
调用 my_new_tool 获取 {param1} 的基础数据。

### 第二步：分析处理
对获取的数据进行分析...

### 第三步：输出报告
生成包含以下内容的报告：
- 数据摘要
- 分析结论
- 建议措施
```

**步骤 2**: Skill 放在 `skills/` 目录下后，SkillRegistry 会自动发现。参数通过 `{placeholder}` 语法引用，运行时由用户传入的值替换。

**注意**: `tools` 列表中声明的工具必须已注册到 ToolRegistry 中，否则 Skill 执行时会找不到工具。

---

### 指南 3: 如何添加新 Hook

#### 方式 A: CallbackHook（推荐，最灵活）

```python
from swagent.core.hooks import (
    HookEvent, HookContext, HookDecision, get_global_hook_registry
)

registry = get_global_hook_registry()

# 示例 1: 审计日志 Hook
async def audit_hook(ctx: HookContext) -> HookDecision:
    print(f"[AUDIT] {ctx.event.value}: tool={ctx.tool_name}, params={ctx.tool_params}")
    return HookDecision.allow()

registry.on(HookEvent.PRE_TOOL_USE, audit_hook, description="审计日志")

# 示例 2: 参数修正 Hook（仅对 file_* 工具生效）
async def path_rewrite_hook(ctx: HookContext) -> HookDecision:
    if ctx.tool_params and "path" in ctx.tool_params:
        original = ctx.tool_params["path"]
        sanitized = original.replace("../", "")
        if sanitized != original:
            return HookDecision.modify(
                {"path": sanitized},
                reason="路径安全: 移除了 ../ 遍历"
            )
    return HookDecision.allow()

registry.on(
    HookEvent.PRE_TOOL_USE,
    path_rewrite_hook,
    matcher="file_*",
    priority=10,
    description="路径安全检查"
)

# 示例 3: 策略拒绝 Hook
async def policy_deny_hook(ctx: HookContext) -> HookDecision:
    blocked_tools = {"code_executor", "web_search"}
    if ctx.tool_name in blocked_tools:
        return HookDecision.deny(f"策略禁止使用工具: {ctx.tool_name}")
    return HookDecision.allow()

registry.on(HookEvent.PRE_TOOL_USE, policy_deny_hook, priority=100, description="安全策略")
```

#### 方式 B: CommandHook（适合运维团队）

```python
# 调用外部脚本检查
registry.on_command(
    HookEvent.PRE_TOOL_USE,
    command="python /opt/security/check_tool_policy.py",
    matcher="*",
    timeout=5.0,
    description="外部安全策略检查"
)
```

外部脚本通过 stdin 接收 JSON，通过 exit code 和 stdout 返回决策：

```python
#!/usr/bin/env python3
# /opt/security/check_tool_policy.py
import json, sys

ctx = json.loads(sys.stdin.read())

if ctx["tool_name"] == "code_executor":
    print(json.dumps({"action": "deny", "reason": "代码执行被策略禁止"}))
    sys.exit(2)  # exit 2 = deny

print(json.dumps({"action": "allow"}))
sys.exit(0)
```

#### 方式 C: HttpHook（适合微服务架构）

```python
registry.on_http(
    HookEvent.POST_TOOL_USE,
    url="https://audit-service.internal/api/v1/tool-audit",
    headers={"Authorization": "Bearer <token>"},
    timeout=5.0,
    description="审计服务上报"
)
```

---

### 指南 4: 如何创建自定义 Agent

**步骤 1**: 继承 `BaseAgent`，实现 `process()` 方法

```python
# swagent/agents/my_agent.py

from swagent.core.base_agent import BaseAgent, AgentConfig, AgentState
from swagent.core.message import Message, MessageType
from swagent.core.agentic_loop import agentic_loop, AgenticLoopConfig
from swagent.tools.tool_registry import ToolRegistry


class MyDomainAgent(BaseAgent):
    """领域专用 Agent"""

    def __init__(self, tool_registry: ToolRegistry, config: AgentConfig = None):
        if config is None:
            config = AgentConfig(
                name="我的领域 Agent",
                role="领域专家",
                description="处理特定领域任务的智能助手",
                system_prompt="你是一个领域专家...",
                max_iterations=15,
            )
        super().__init__(config)
        self.tool_registry = tool_registry

    async def process(self, message: Message) -> Message:
        """核心处理逻辑"""
        self.state = AgentState.ACTING

        # 构建消息列表
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": message.content},
        ]

        # 使用 Agentic Loop 驱动工具调用
        loop_config = AgenticLoopConfig(
            max_turns=self.config.max_iterations,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )

        final_content = ""
        async for event in agentic_loop(
            self.llm, messages, self.tool_registry, loop_config
        ):
            if event["type"] == "complete":
                final_content = event["data"].get("content", "")
            elif event["type"] == "error":
                final_content = f"处理失败: {event['data'].get('message', '')}"

        return Message(
            sender=self.agent_id,
            sender_name=self.config.name,
            receiver=message.sender,
            content=final_content,
            msg_type=MessageType.RESPONSE,
        )
```

**步骤 2**: 使用 Agent

```python
from swagent.tools.tool_registry import ToolRegistry
from swagent.tools.domain.emission_calculator import EmissionCalculator
from swagent.agents.my_agent import MyDomainAgent

# 准备工具
registry = ToolRegistry()
registry.register(EmissionCalculator())

# 创建 Agent
agent = MyDomainAgent(tool_registry=registry)

# 运行
from swagent.core.message import Message, MessageType
msg = Message(sender="user", content="计算 100 吨厨余垃圾堆肥的碳排放")
response = await agent.run(msg)
print(response.content)
```

---

### 指南 5: 如何集成新的 LLM Provider

**步骤 1**: 继承 `BaseLLM`，实现 `chat()` 和 `chat_stream()`

```python
# swagent/llm/my_provider.py

from typing import List, Dict, Any, Optional, AsyncIterator
from swagent.llm.base_llm import BaseLLM, LLMConfig, LLMResponse


class MyProviderClient(BaseLLM):
    """自定义 LLM Provider"""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        # 初始化你的 SDK 客户端
        self._client = MySDK(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        response = await self._client.complete(
            messages=messages,
            temperature=temperature or self.config.temperature,
            max_tokens=max_tokens or self.config.max_tokens,
        )

        return LLMResponse(
            content=response.text,
            model=self.config.model,
            usage={
                "prompt_tokens": response.input_tokens,
                "completion_tokens": response.output_tokens,
                "total_tokens": response.input_tokens + response.output_tokens,
            },
            finish_reason=response.stop_reason,
            raw_response=response,
        )

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        **kwargs,
    ) -> AsyncIterator[str]:
        stream = await self._client.complete_stream(messages=messages)
        async for chunk in stream:
            if chunk.text:
                yield chunk.text

    async def chat_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        **kwargs,
    ) -> LLMResponse:
        """
        带工具调用的聊天接口
        注意: 需要将 tools 转换为你的 provider 的格式
        """
        # 将 OpenAI function calling 格式转换为你的格式
        native_tools = self._convert_tools(tools)

        response = await self._client.complete(
            messages=messages,
            tools=native_tools,
            tool_choice=tool_choice,
        )

        # 将响应中的 tool_calls 转换为 OpenAI 格式
        tool_calls = None
        if response.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments_json,
                    },
                }
                for tc in response.tool_calls
            ]

        return LLMResponse(
            content=response.text or "",
            model=self.config.model,
            usage={...},
            finish_reason=response.stop_reason,
            tool_calls=tool_calls,
        )

    def _convert_tools(self, openai_tools):
        """将 OpenAI function 格式转换为 provider 原生格式"""
        # 实现转换逻辑
        ...
```

**步骤 2**: 在配置中使用

```python
from swagent.llm.my_provider import MyProviderClient
from swagent.llm.base_llm import LLMConfig

config = LLMConfig(
    provider="my_provider",
    model="my-model-v1",
    api_key="your-api-key",
    base_url="https://api.my-provider.com/v1",
)
client = MyProviderClient(config)
```

**关键接口**: 必须实现 `chat_with_tools()` 方法返回标准化的 `tool_calls` 格式（与 OpenAI 一致），才能与 Agentic Loop 配合使用。`tool_calls` 中每项必须包含 `id`、`function.name`、`function.arguments` 字段。

---

### 指南 6: 如何添加新领域

**步骤 1**: 创建领域工具

在 `swagent/tools/domain/` 下创建领域工具类（参考 [指南 1](#指南-1-如何添加新工具)）。

**步骤 2**: 创建领域 Skill

在 `skills/` 目录下创建 `.md` 文件（参考 [指南 2](#指南-2-如何添加新-skill)），frontmatter 中 `domain` 字段设为你的领域名。

**步骤 3**: 创建领域工具注册函数

```python
# swagent/tools/domain/my_domain_tools.py

from swagent.tools.tool_registry import ToolRegistry


def register_my_domain_tools(registry: ToolRegistry) -> None:
    """注册我的领域的所有工具"""
    from swagent.tools.domain.tool_a import ToolA
    from swagent.tools.domain.tool_b import ToolB
    from swagent.tools.domain.tool_c import ToolC

    registry.register(ToolA())
    registry.register(ToolB())
    registry.register(ToolC())
```

**步骤 4**: 创建领域 Agent（可选）

如果领域需要特定的系统提示或工作流逻辑，创建领域专用 Agent（参考 [指南 4](#指南-4-如何创建自定义-agent)）。

**步骤 5**: 创建领域 StateGraph 工作流（可选）

对于复杂的多步骤领域任务，定义 StateGraph 工作流：

```python
# swagent/my_domain/workflow.py

from typing import TypedDict, List, Optional, Dict, Any
from swagent.stategraph import StateGraph


class MyDomainState(TypedDict):
    """领域工作流状态"""
    input_data: str
    intermediate_results: List[Dict[str, Any]]
    final_output: Optional[str]
    errors: List[str]


def create_my_domain_workflow() -> StateGraph:
    graph = StateGraph(MyDomainState)

    @graph.node()
    async def step_1(state: MyDomainState) -> dict:
        # 第一步处理逻辑
        return {"intermediate_results": [...]}

    @graph.node()
    async def step_2(state: MyDomainState) -> dict:
        # 第二步处理逻辑
        return {"final_output": "..."}

    graph.set_entry_point("step_1")
    graph.add_edge("step_1", "step_2")
    graph.set_exit_point("step_2")

    return graph
```

**步骤 6**: 集成到 CLI（可选）

在 CLI 的领域注册逻辑中添加你的领域：

```python
# 领域 → 工具注册映射
DOMAIN_REGISTRY = {
    "waste": register_waste_tools,
    "my_domain": register_my_domain_tools,  # 新领域
}
```

---

## 附录: 目录结构总览

```
swagent/
├── core/
│   ├── agentic_loop.py         # Agentic Loop 引擎
│   ├── hooks.py                # Hook 系统
│   ├── subagent.py             # 子 Agent 隔离
│   ├── session_storage.py      # 会话持久化
│   ├── layered_settings.py     # 分层配置
│   ├── error_handler.py        # 错误处理
│   ├── denial_tracking.py      # 权限拒绝追踪
│   ├── auto_compact.py         # 自动上下文压缩
│   ├── progress.py             # 进度报告
│   ├── base_agent.py           # Agent 基类
│   ├── orchestrator.py         # 多 Agent 编排调度器
│   ├── context.py              # 上下文管理
│   ├── message.py              # 消息定义
│   └── communication.py        # 通信总线
├── tools/
│   ├── base_tool.py            # 工具基类
│   ├── tool_registry.py        # 工具注册中心
│   ├── tool_executor.py        # 工具执行管线
│   ├── builtin/                # 内置工具
│   │   ├── code_executor.py
│   │   ├── file_handler.py
│   │   └── web_search.py
│   └── domain/                 # 领域工具
│       ├── emission_calculator.py
│       ├── weather_tool.py
│       ├── location_tool.py
│       ├── imagery_tool.py
│       ├── visualizer.py
│       └── lca_analyzer.py
├── llm/
│   ├── base_llm.py             # LLM 基类
│   └── openai_client.py        # OpenAI 兼容客户端
├── cache/
│   ├── file_cache.py           # 文件状态缓存
│   ├── result_budget.py        # 结果预算管理
│   └── prompt_cache.py         # Prompt 缓存优化
├── stategraph/                 # StateGraph 工作流引擎
│   ├── graph.py
│   ├── node.py
│   ├── edge.py
│   ├── state.py
│   ├── persistence.py
│   └── errors.py
├── waste_monitoring/           # 固废监测应用
│   ├── workflow.py
│   ├── state.py
│   ├── runner.py
│   ├── processors/
│   └── report/
└── multi_domain_detection/     # 多领域检测
    ├── runner.py
    ├── core/
    └── database/

skills/                          # Skill 定义目录
├── emission_report.md
├── compliance_check.md
└── site_analysis.md
```

---

## 附录: 关键数据流

```
用户输入
  │
  ▼
CLI / API ─── LayeredSettings (配置加载)
  │
  ▼
BaseAgent.run()
  │
  ├── SystemPromptBuilder (构建 prompt)
  ├── SkillRegistry (加载 Skill)
  │
  ▼
agentic_loop() ◄────── AutoCompact (上下文压缩)
  │
  ├── LLM 调用 (OpenAIClient.chat_with_tools)
  │     │
  │     └── 返回 tool_calls
  │
  ▼
ToolExecutor.execute()
  │
  ├── validate_parameters
  ├── HookRegistry.execute_hooks(PRE_TOOL_USE)
  ├── permission_check
  ├── BaseTool.execute()
  ├── ResultBudgetManager.process_result()
  ├── HookRegistry.execute_hooks(POST_TOOL_USE)
  └── AuditLog.record()
  │
  ▼
结果追加到消息列表 → 下一轮 LLM 调用
  │
  ▼
SessionStorage.record_message() (持久化)
  │
  ▼
ProgressReporter.emit() (进度通知)
```
