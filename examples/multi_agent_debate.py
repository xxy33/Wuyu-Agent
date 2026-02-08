"""
多Agent协作示例一：Orchestrator辩论模式

场景：
  模拟一场固废处理技术方案的多专家评审会。
  3位专家Agent从不同角度辩论，ReActAgent裁判自动判断何时终止。

演示能力：
  - BaseAgent 子类化（ExpertAgent）
  - Orchestrator 辩论模式编排
  - MessageBus 消息广播与轮流发言
  - ReActAgent 裁判自动终止判断

用法：
  python examples/multi_agent_debate.py "如何利用生物炭提高厌氧消化效率"
"""
import sys
import asyncio
import logging

from swagent.core.base_agent import BaseAgent, AgentConfig
from swagent.core.message import Message, MessageType
from swagent.core.orchestrator import Orchestrator, OrchestrationMode
from swagent.core.communication import RateLimitConfig
from swagent.agents.react_agent import ReActAgent
from swagent.llm.base_llm import LLMConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("example.debate")

# ============================================================
# API 配置（按实际环境修改）
# ============================================================
LLM_CONFIG = LLMConfig(
    provider="openai",
    model="gpt-5.2",
    api_key="sk-xxxx",
    base_url="https://www.xxxx.cn/v1",
    temperature=0.7,
    max_tokens=2048,
)


# ============================================================
# Agent 定义
# ============================================================
class ExpertAgent(BaseAgent):
    """通用专家Agent"""

    async def process(self, message: Message) -> Message:
        response_text = await self.chat(message.content, use_history=True)
        return Message(
            sender=self.agent_id,
            sender_name=self.config.name,
            receiver=message.sender,
            content=response_text,
            msg_type=MessageType.RESPONSE,
        )


def make_expert(name: str, role: str, prompt: str) -> ExpertAgent:
    """快捷创建专家"""
    return ExpertAgent(AgentConfig(
        name=name, role=role, llm_config=LLM_CONFIG,
        system_prompt=prompt + "\n请用简洁专业的语言发表意见，每次发言控制在200字以内。",
    ))


# ============================================================
# 报告渲染
# ============================================================
def render_report(topic: str, result: dict) -> str:
    lines = [
        "=" * 60,
        "          多专家评审报告",
        "=" * 60, "",
        f"  议题: {topic}",
        f"  轮数: {result.get('total_rounds', 0)}",
        f"  裁判终止: {'是' if result.get('terminated_by_judgment') else '否'}",
        "", "-" * 60, "",
    ]
    history = result.get("history", [])
    cur_round = 0
    for i, msg in enumerate(history):
        rn = i // 3 + 1
        if rn != cur_round:
            cur_round = rn
            lines.append(f"  --- 第 {cur_round} 轮 ---\n")
        lines.append(f"  [{msg.get('agent', '?')}]:")
        for ln in msg.get("content", "").split("\n"):
            lines.append(f"    {ln}")
        lines.append("")

    judgment = result.get("judgment")
    if judgment:
        lines += ["-" * 60, "  主持人结论", "-" * 60, "",
                  f"  决策: {judgment.decision.value}",
                  f"  置信度: {judgment.confidence:.2f}",
                  f"  理由: {judgment.reason}", ""]
    lines.append("=" * 60)
    return "\n".join(lines)


# ============================================================
# 主流程
# ============================================================
async def run(topic: str):
    # 创建3位专家
    experts = [
        make_expert("技术专家", "固废处理技术专家",
                    "你是资深固废处理技术专家。评估技术可行性、工艺完整性和创新性。"),
        make_expert("环境专家", "环境影响评估专家",
                    "你是环境影响评估专家。评估二次污染风险、法规合规性和碳排放。"),
        make_expert("经济专家", "产业经济分析专家",
                    "你是产业经济分析专家。评估成本效益、产业化前景和市场价值。"),
    ]

    # 创建裁判
    judge = ReActAgent(config=AgentConfig(
        name="主持人", role="评审主持人",
        description="判断讨论是否充分，综合各方意见",
        llm_config=LLM_CONFIG, temperature=0.3,
    ))

    # 编排器（辩论模式）
    orch = Orchestrator(
        mode=OrchestrationMode.DEBATE,
        enable_rate_limit=True,
        rate_limit_config=RateLimitConfig(
            max_messages_per_minute=30,
            max_messages_per_turn=1,
            cooldown_seconds=0.5,
        ),
    )
    for e in experts:
        orch.register_agent(e)

    await orch.start()
    result = await orch.debate_with_judgment(
        topic=f"请从各自专业角度评审以下研究方案：{topic}",
        max_rounds=3,
        judge_agent=judge,
    )
    await orch.stop()

    print(render_report(topic, result))


def main():
    if len(sys.argv) < 2:
        print("用法: python examples/multi_agent_debate.py <议题>")
        print('示例: python examples/multi_agent_debate.py "城市餐厨垃圾热解制备生物油"')
        sys.exit(1)
    asyncio.run(run(sys.argv[1]))


if __name__ == "__main__":
    main()
