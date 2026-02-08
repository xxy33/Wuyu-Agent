"""
多Agent协作示例二：StateGraph 多Agent流水线

场景：
  用户输入一个固废领域的研究主题，系统通过StateGraph工作流串联多个
  专家Agent，依次完成：需求分析 → 并行多视角评估 → 综合决策。

演示能力：
  - StateGraph 工作流引擎（节点、边、条件路由）
  - 多Agent在不同节点中协作
  - 并行节点（多专家同时评估）+ converge_to 汇聚
  - 条件路由（根据评分决定是否通过）

工作流：
  START → analyst(需求分析)
        → parallel[tech_eval, env_eval, econ_eval](三专家并行评估)
        → converge_to → synthesizer(综合评分)
        → router: 通过→END / 不通过→reviser(修订建议)→END

用法：
  python examples/multi_agent_stategraph.py "污泥厌氧消化耦合热水解预处理"
"""
import sys
import json
import asyncio
import logging
from typing import Dict, Any

from openai import AsyncOpenAI

from swagent.stategraph.graph import StateGraph, ExecutionConfig
from swagent.stategraph.node import START, END

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("example.stategraph")

# ============================================================
# API 配置
# ============================================================
API_KEY = "sk-xxxx"
BASE_URL = "https://www.xxxx.cn/v1"
MODEL = "gpt-5.2"

client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=120)


async def llm_call(system: str, user: str, temperature: float = 0.7) -> str:
    """统一的LLM调用"""
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=2048,
    )
    return resp.choices[0].message.content or ""


# ============================================================
# 节点1：需求分析Agent
# ============================================================
async def analyst(state: Dict[str, Any]) -> Dict[str, Any]:
    """分析用户的研究主题，提取关键信息"""
    topic = state["topic"]
    logger.info(f"[需求分析Agent] 分析主题: {topic}")

    result = await llm_call(
        system=(
            "你是一位固废领域的需求分析师。请分析用户的研究主题，提取：\n"
            "1. 核心技术方向\n2. 涉及的废物类型\n3. 关键科学问题\n4. 预期目标\n"
            "用简洁的条目式输出。"
        ),
        user=f"请分析以下研究主题：{topic}",
        temperature=0.3,
    )
    return {"analysis": result}


# ============================================================
# 节点2a/2b/2c：三位专家并行评估
# ============================================================
async def tech_eval(state: Dict[str, Any]) -> Dict[str, Any]:
    """技术专家评估"""
    logger.info("[技术专家] 评估中...")
    result = await llm_call(
        system="你是固废处理技术专家。请从技术可行性、工艺创新性角度评估，给出1-10分并说明理由。",
        user=f"研究主题：{state['topic']}\n需求分析：{state['analysis']}",
    )
    return {"tech_opinion": result}


async def env_eval(state: Dict[str, Any]) -> Dict[str, Any]:
    """环境专家评估"""
    logger.info("[环境专家] 评估中...")
    result = await llm_call(
        system="你是环境影响评估专家。请从环境风险、合规性、碳排放角度评估，给出1-10分并说明理由。",
        user=f"研究主题：{state['topic']}\n需求分析：{state['analysis']}",
    )
    return {"env_opinion": result}


async def econ_eval(state: Dict[str, Any]) -> Dict[str, Any]:
    """经济专家评估"""
    logger.info("[经济专家] 评估中...")
    result = await llm_call(
        system="你是产业经济分析专家。请从成本效益、产业化前景角度评估，给出1-10分并说明理由。",
        user=f"研究主题：{state['topic']}\n需求分析：{state['analysis']}",
    )
    return {"econ_opinion": result}


# ============================================================
# 节点3：综合评审Agent
# ============================================================
async def synthesizer(state: Dict[str, Any]) -> Dict[str, Any]:
    """综合三位专家意见，给出总评"""
    logger.info("[综合评审Agent] 汇总意见...")

    result = await llm_call(
        system=(
            "你是评审委员会主席。请综合三位专家的意见，给出：\n"
            "1. 综合评分（满分100，>=70为通过）\n"
            "2. 核心优势\n3. 主要不足\n4. 最终结论\n"
            "请在第一行输出纯数字评分，后续输出分析。"
        ),
        user=(
            f"研究主题：{state['topic']}\n\n"
            f"技术专家意见：\n{state.get('tech_opinion', '无')}\n\n"
            f"环境专家意见：\n{state.get('env_opinion', '无')}\n\n"
            f"经济专家意见：\n{state.get('econ_opinion', '无')}"
        ),
        temperature=0.3,
    )

    # 尝试从第一行提取分数
    first_line = result.strip().split("\n")[0].strip()
    try:
        score = int("".join(c for c in first_line if c.isdigit())[:3])
    except (ValueError, IndexError):
        score = 60

    return {"synthesis": result, "score": score}


# ============================================================
# 节点4：修订建议Agent（仅在不通过时触发）
# ============================================================
async def reviser(state: Dict[str, Any]) -> Dict[str, Any]:
    """针对不通过的方案给出修订建议"""
    logger.info(f"[修订Agent] 评分 {state.get('score', 0)} 未通过，生成修订建议...")

    result = await llm_call(
        system=(
            "你是一位资深学术顾问。方案评审未通过，请基于专家意见给出具体的修订建议，"
            "包括：1. 需要补充的实验 2. 需要调整的参数 3. 需要加强的论证"
        ),
        user=(
            f"研究主题：{state['topic']}\n\n"
            f"综合评审意见：\n{state.get('synthesis', '')}"
        ),
    )
    return {"revision_advice": result}


# ============================================================
# 构建 StateGraph 工作流（并行版，使用 converge_to 汇聚）
# ============================================================
def build_workflow():
    """构建StateGraph并行工作流"""
    graph = StateGraph(name="MultiAgentReview")

    # 注册节点
    graph.add_node("analyst", analyst)
    graph.add_node("tech_eval", tech_eval)
    graph.add_node("env_eval", env_eval)
    graph.add_node("econ_eval", econ_eval)
    graph.add_node("synthesizer", synthesizer)
    graph.add_node("reviser", reviser)

    # 边：START → 需求分析
    graph.add_edge(START, "analyst")

    # 边：需求分析 → 三专家并行评估 → 汇聚到综合评审
    graph.add_parallel_edge(
        "analyst",
        ["tech_eval", "env_eval", "econ_eval"],
        converge_to="synthesizer",
    )

    # 条件路由：评分>=70通过，否则修订
    def score_router(state: Dict[str, Any]) -> str:
        return "pass" if state.get("score", 0) >= 70 else "revise"

    graph.add_conditional_edge("synthesizer", score_router, {
        "pass": END,
        "revise": "reviser",
    })
    graph.add_edge("reviser", END)

    return graph.compile(ExecutionConfig(max_iterations=20))


async def run_stategraph(topic: str):
    """使用StateGraph框架执行（并行版）"""
    workflow = build_workflow()
    result = await workflow.invoke({"topic": topic})

    if result.success:
        s = result.state
        print(f"\n{'='*60}")
        print(f"  StateGraph 执行完成")
        print(f"  耗时: {result.duration:.1f}s | 迭代: {result.iterations}")
        print(f"  评分: {s.get('score', 'N/A')}/100")
        print(f"{'='*60}")
        print(f"\n[需求分析]\n{s.get('analysis', '')[:500]}")
        print(f"\n[技术专家]\n{s.get('tech_opinion', '')[:500]}")
        print(f"\n[环境专家]\n{s.get('env_opinion', '')[:500]}")
        print(f"\n[经济专家]\n{s.get('econ_opinion', '')[:500]}")
        print(f"\n[综合评审]\n{s.get('synthesis', '')[:500]}")
        if s.get("revision_advice"):
            print(f"\n[修订建议]\n{s['revision_advice'][:500]}")
        else:
            print(f"\n  评审通过!")
    else:
        logger.error(f"工作流失败: {result.error}")

    return result


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python examples/multi_agent_stategraph.py <研究主题>")
        print()
        print("示例:")
        print('  python examples/multi_agent_stategraph.py "污泥厌氧消化耦合热水解预处理"')
        sys.exit(1)

    topic = sys.argv[1]
    logger.info("使用 StateGraph 框架执行（并行版）")
    asyncio.run(run_stategraph(topic))


if __name__ == "__main__":
    main()
