# Wuyu-Agent (梧雨智能体框架)

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![PyPI version](https://img.shields.io/badge/pypi-v0.1.0-blue.svg)]()

## 简介

Wuyu-Agent（`swagent`）是面向环境、固废、物流等垂直领域的企业级多智能体协作框架。框架借鉴 Claude Code 的核心设计模式（Agentic Loop、Hook 管道、AutoCompact、分层配置等），面向中国国有企业项目提供安全可控、领域增强、审计可追溯的 AI Agent 运行时。

适用场景：固体废物管理、环境监测、遥感检测、碳排放核算、物流调度等垂直领域的智能分析与报告生成。

## 核心架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        应用层 (Application)                         │
│          CLI (chat/serve/detect)  |  Web UI  |  Python API          │
├─────────────────────────────────────────────────────────────────────┤
│                       企业层 (Enterprise)                           │
│   分层配置 (5级优先级)  |  会话持久化 (JSONL)  |  审计日志            │
├─────────────────────────────────────────────────────────────────────┤
│                       缓存层 (Cache)                                │
│      FileCache  |  ResultBudget (大结果持久化)  |  PromptCache       │
├─────────────────────────────────────────────────────────────────────┤
│                      工作流层 (Workflow)                             │
│     StateGraph (条件分支/并行扇出/检查点)  |  Workflow Templates     │
├─────────────────────────────────────────────────────────────────────┤
│                      领域层 (Domain)                                │
│   固废知识库 (1000+行分类)  |  排放计算  |  LCA分析  |  30+国标      │
├─────────────────────────────────────────────────────────────────────┤
│                      工具层 (Tools)                                 │
│   ToolRegistry  |  Hook Pipeline (Pre/PostToolUse)  |  Executor     │
├─────────────────────────────────────────────────────────────────────┤
│                      Agent 层 (Agent)                               │
│   BaseAgent  |  AgenticLoop (自主循环)  |  SubAgent (隔离模型)       │
├─────────────────────────────────────────────────────────────────────┤
│                      LLM 层 (LLM)                                  │
│          OpenAI 兼容接口  |  流式响应  |  Function Calling           │
└─────────────────────────────────────────────────────────────────────┘
```

## 核心特性

### 1. Agentic Loop 引擎

自主工具调用循环，参考 Claude Code `queryLoop` 设计。LLM 输出 tool_calls 后自动执行工具、追加结果、继续推理，直到任务完成。支持 `finish_reason=length` 时自动注入续写提示恢复生成。

### 2. Hook 系统

提供 `PreToolUse` / `PostToolUse` / `PreLoop` / `PostLoop` / `OnError` / `SessionStart` / `SessionEnd` 等生命周期钩子，支持三种执行方式：
- **CallbackHook** - Python 函数回调
- **CommandHook** - Shell 命令（exit code 2 = 阻断）
- **HttpHook** - POST 到外部 API

可用于输入校验、输出审计、权限拦截等企业级场景。

### 3. AutoCompact 上下文压缩

当上下文逼近 token 上限时自动压缩历史消息。支持 LLM 摘要压缩和轻量级 MicroCompact（占位符替换）两种模式，保留最近 N 条消息不压缩，确保长对话不丢失关键上下文。

### 4. 结果预算管理

两级预算防止大工具结果撑爆上下文：
- **单工具级** - 超过阈值的结果自动持久化到磁盘，仅保留前 2000 字符预览
- **消息聚合级** - 同一轮所有工具结果总和不超过上限

### 5. 子 Agent 隔离

默认隔离、显式共享的安全模型。子 Agent 获得完全隔离的上下文（文件缓存深拷贝、独立权限追踪），父 Agent 的中止信号自动传播，可选择性共享特定资源。

### 6. Skill 系统

Markdown frontmatter 声明式技能定义，领域专家无需编码即可配置。Skill 声明所需工具、参数和执行步骤，由框架自动编排执行。

### 7. 分层配置

五级优先级配置合并（高到低）：
`policy_settings` > `env_settings` > `local_settings` > `project_settings` > `user_settings`

国企 IT 管理员可通过 policy 层强制覆盖所有下级配置。

### 8. 会话持久化

JSONL 格式完整记录主对话和子 Agent 对话，支持会话恢复和审计追溯：
```
~/.swagent/sessions/{session_id}/
├── main.jsonl              # 主对话记录
├── subagents/{id}.jsonl    # 子 Agent 对话
├── tool-results/{id}.json  # 持久化的大结果
└── metadata.json           # 会话元数据
```

### 9. StateGraph 工作流引擎

类 LangGraph 的声明式状态图工作流，支持条件分支、并行扇出（fan-out）、循环迭代、检查点保存/恢复、可配置重试策略和退避算法。

### 10. 领域知识库

内置 1000+ 行固废分类数据、30+ 国家/国际标准法规、60+ 专业术语（中英互译 + 缩写展开）、8 种场景优化提示词，覆盖城市生活垃圾、工业固废、危废、建筑垃圾、医疗废物等全品类。

## 快速开始

```bash
# 安装（含全部依赖）
pip install -e ".[full]"

# 启动交互式对话
swagent chat --domain waste
```

## 安装

### 环境要求

- Python >= 3.9
- pip

### 基础安装

```bash
git clone https://github.com/xxy33/Wuyu-Agent.git
cd Wuyu-Agent

# 最小安装（仅核心框架）
pip install -e .

# 完整安装（推荐）
pip install -e ".[full]"
```

### 按需安装

```bash
# LLM 接口
pip install -e ".[llm]"

# Web 服务
pip install -e ".[web]"

# GIS / 遥感
pip install -e ".[gis]"

# 数据分析 / 可视化
pip install -e ".[data]"

# 向量检索
pip install -e ".[vectors]"

# 外部存储 (Redis / MongoDB)
pip install -e ".[storage]"

# 开发工具
pip install -e ".[dev]"
```

### 配置

在项目根目录创建 `.env`：

```env
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
```

或编辑 `config.yaml` 修改 LLM 提供商、Agent 参数、领域配置等（见「配置说明」章节）。

## 使用方式

### CLI 交互模式

```bash
# 默认模型对话
swagent chat --domain waste

# 指定模型
swagent chat --model gpt-4 --domain waste
```

### Web 服务模式

```bash
swagent serve --port 8080
```

### 遥感检测

```bash
swagent detect --mode prod --input ./images --city "北京市"
```

### 工作流执行

```bash
swagent run research --input ./task.json
```

### Skill 使用

在交互式 REPL 中直接使用 Skill：

```
> /排放计算报告 --waste_type food_waste --treatment_method composting --quantity_tons 500

计算中...
=== 碳排放对比分析报告 ===
废物类型: 厨余垃圾 (food_waste)
处理方式: 堆肥 (composting)
处理量: 500 吨
CO2排放: xxx kg CO2e
...
```

查看可用 Skill：

```bash
swagent skill list
swagent skill run 排放计算报告 --param waste_type=plastic
```

### 编程接口

```python
import asyncio
from swagent.core.agentic_loop import AgenticLoop, AgenticLoopConfig
from swagent.llm.openai_client import OpenAIClient
from swagent.llm.base_llm import LLMConfig
from swagent.tools.tool_registry import ToolRegistry

# 配置 LLM
llm_config = LLMConfig(
    provider="openai",
    model="gpt-4",
    api_key="your_api_key",
    base_url="https://api.openai.com/v1",
)
llm = OpenAIClient(llm_config)

# 注册工具
registry = ToolRegistry()

# 创建 Agentic Loop
loop = AgenticLoop(
    llm=llm,
    tool_registry=registry,
    config=AgenticLoopConfig(max_turns=10),
)

async def main():
    result = await loop.run(
        system_prompt="你是固废管理领域专家。",
        user_message="分析厨余垃圾堆肥和厌氧消化的碳排放差异",
    )
    print(result.content)

asyncio.run(main())
```

#### Agent 协作示例

```python
from swagent.agents import ReActAgent, PlannerAgent
from swagent.core.orchestrator import Orchestrator

planner = PlannerAgent("规划师", llm=llm)
analyst = ReActAgent("分析师", llm=llm)

orchestrator = Orchestrator(llm=llm)
orchestrator.add_agent(planner)
orchestrator.add_agent(analyst)

result = await orchestrator.debate(
    "应该优先选择填埋还是焚烧？",
    rounds=2,
)
```

#### StateGraph 工作流示例

```python
from swagent.stategraph import StateGraph, START, END
from typing import TypedDict

class PipelineState(TypedDict):
    input: str
    processed: str
    result: str

graph = StateGraph(PipelineState)

@graph.node()
async def preprocess(state: PipelineState) -> dict:
    return {"processed": state["input"].strip().lower()}

@graph.node()
async def analyze(state: PipelineState) -> dict:
    return {"result": f"分析结果: {state['processed']}"}

graph.set_entry_point("preprocess")
graph.add_edge("preprocess", "analyze")
graph.set_exit_point("analyze")

app = graph.compile()
result = await app.invoke({"input": "  HELLO WORLD  "})
print(result.state["result"])  # "分析结果: hello world"
```

## 项目结构

```
Wuyu-Agent/
├── swagent/                         # 主包
│   ├── cli.py                       # 统一 CLI 入口 (chat/serve/detect/run/skill)
│   ├── core/                        # 核心引擎
│   │   ├── agentic_loop.py          # Agentic Loop 自主循环
│   │   ├── auto_compact.py          # AutoCompact 上下文压缩
│   │   ├── base_agent.py            # Agent 基类
│   │   ├── hooks.py                 # Hook 管道 (Pre/PostToolUse 等)
│   │   ├── layered_settings.py      # 五级分层配置
│   │   ├── session_storage.py       # JSONL 会话持久化
│   │   ├── subagent.py              # 子 Agent 隔离模型
│   │   ├── denial_tracking.py       # 权限拒绝追踪
│   │   ├── error_handler.py         # 错误分类与恢复
│   │   ├── progress.py              # 进度事件系统
│   │   ├── orchestrator.py          # 多 Agent 编排器
│   │   ├── communication.py         # Agent 间通信
│   │   ├── context.py               # 上下文管理
│   │   └── message.py               # 消息定义
│   ├── agents/                      # Agent 实现
│   │   ├── planner_agent.py         # 规划 Agent
│   │   └── react_agent.py           # ReAct Agent
│   ├── llm/                         # LLM 接口层
│   │   ├── base_llm.py              # LLM 基类
│   │   └── openai_client.py         # OpenAI 兼容客户端
│   ├── tools/                       # 工具系统
│   │   ├── tool_registry.py         # 工具注册中心
│   │   ├── tool_executor.py         # 工具执行器
│   │   ├── base_tool.py             # 工具基类
│   │   ├── builtin/                 # 内置工具 (代码执行/文件/搜索)
│   │   └── domain/                  # 领域工具 (排放计算/LCA/影像/天气)
│   ├── domain/                      # 领域增强
│   │   ├── knowledge_base.py        # 固废知识库
│   │   ├── terminology.py           # 60+ 术语中英互译
│   │   ├── standards.py             # 30+ 国标法规
│   │   └── prompts.py               # 领域优化提示词
│   ├── cache/                       # 缓存层
│   │   ├── file_cache.py            # 文件缓存
│   │   ├── result_budget.py         # 结果预算管理
│   │   └── prompt_cache.py          # 提示词缓存
│   ├── skills/                      # Skill 系统
│   │   ├── loader.py                # Skill 加载器
│   │   └── registry.py              # Skill 注册中心
│   ├── stategraph/                  # StateGraph 工作流引擎
│   │   ├── graph.py                 # 图核心
│   │   ├── state.py                 # 状态管理
│   │   ├── node.py                  # 节点定义
│   │   ├── edge.py                  # 边定义 (固定/条件/并行)
│   │   ├── persistence.py           # 检查点持久化
│   │   └── integrations/            # LLM/Agent/Tool 节点集成
│   ├── workflows/                   # 工作流模板
│   │   ├── research_workflow.py     # 科研工作流
│   │   ├── report_workflow.py       # 报告工作流
│   │   ├── analysis_workflow.py     # 分析工作流
│   │   └── coding_workflow.py       # 编码工作流
│   ├── waste_monitoring/            # 固废监测子系统
│   │   ├── runner.py                # 监测运行器
│   │   ├── workflow.py              # 监测工作流
│   │   └── report/                  # 报告生成
│   ├── multi_domain_detection/      # 多领域遥感检测
│   └── utils/                       # 通用工具
│       ├── config.py                # 配置加载
│       └── logger.py                # 日志模块
├── skills/                          # Skill 定义文件 (Markdown)
│   ├── emission_report.md           # 排放计算报告
│   ├── compliance_check.md          # 合规检查
│   └── site_analysis.md             # 场地分析
├── config.yaml                      # 全局配置文件
├── web/                             # Web 前端
├── examples/                        # 示例代码
├── docs/                            # 文档
├── setup.py                         # 安装配置
└── requirements.txt                 # 依赖清单
```

## 自定义 Skill

Skill 使用 Markdown + YAML frontmatter 格式定义，放置在 `skills/` 目录下即可自动加载：

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
    description: 废物类型 (food_waste, paper, plastic, wood 等)
  - name: treatment_method
    type: string
    required: true
    description: 处理方式 (landfill, incineration, composting 等)
  - name: quantity_tons
    type: number
    required: false
    description: 废物总量 (吨), 默认1000吨
---

## 执行步骤

1. 从 IPCC 排放因子数据库获取对应参数
2. 调用 emission_calculator 计算 CO2、CH4、N2O 排放量
3. 与其他可选处理方式进行对比计算
4. 调用 visualizer 生成排放量对比柱状图
5. 整合结果生成标准格式报告，包含减排建议
```

领域专家只需编写 Markdown 即可创建新的自动化能力，无需修改代码。

## 配置说明

### 全局配置文件 (`config.yaml`)

```yaml
app:
  name: "SolidWaste-Agent"
  version: "0.1.0"

llm:
  default_provider: "openai"
  providers:
    openai:
      api_key: "${OPENAI_API_KEY}"
      base_url: "https://api.openai.com/v1"
      default_model: "gpt-4"
    local:
      base_url: "http://localhost:8000"
      default_model: "qwen-7b"

agents:
  default_temperature: 0.7
  default_max_tokens: 4096

domain:
  name: "固体废物管理"
  knowledge_base_path: "./data/knowledge_base"

cache:
  enabled: true
  backend: "memory"
  ttl: 3600
```

### 分层配置目录

```
~/.swagent/config.yaml        # 用户全局配置 (user_settings)
~/.swagent/policy.yaml         # 管理员强制策略 (policy_settings, 最高优先级)
.swagent/config.yaml           # 项目共享配置 (project_settings)
.swagent/local.yaml            # 本地配置 (local_settings, gitignored)
环境变量 SWAGENT_*             # 环境变量覆盖 (env_settings)
```

合并规则：高优先级的值覆盖低优先级，policy 层用于国企 IT 管理员强制约束模型白名单、工具权限等安全策略。

## 技术架构

框架核心设计借鉴 Claude Code 的关键模式：

| 设计模式 | 来源参考 | 框架实现 |
|----------|---------|---------|
| Agentic Loop | `queryLoop` | `core/agentic_loop.py` - LLM -> tool_calls -> 执行 -> 追加 -> 循环 |
| Hook Pipeline | `hooks.ts` | `core/hooks.py` - Pre/PostToolUse 生命周期管道 |
| AutoCompact | `autoCompact.ts` | `core/auto_compact.py` - token 阈值触发自动摘要压缩 |
| Result Budget | `toolResultStorage.ts` | `cache/result_budget.py` - 两级预算防 context 溢出 |
| Forked Agent | `forkedAgent.ts` | `core/subagent.py` - 默认隔离、显式共享 |
| Layered Settings | `settings/` | `core/layered_settings.py` - 五级优先级配置 |
| Session Storage | `sessionStorage.ts` | `core/session_storage.py` - JSONL 持久化 |
| Denial Tracking | `denialTracking.ts` | `core/denial_tracking.py` - 权限拒绝模式检测 |

## 测试

```bash
# 运行全部测试
python -m pytest tests/

# 按模块测试
python -m pytest tests/test_phase1_llm.py
python -m pytest tests/test_phase4_tools.py
python -m pytest tests/test_phase5_workflows.py
```

## 许可证

本项目采用 [MIT](LICENSE) 许可证。
