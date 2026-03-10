"""
任务二：研究趋势推演 - StateGraph工作流

执行顺序：
  A1(时间线) → B1(政策) → A2(现状) → A3(趋势) → B2(博弈) → B3(冲突) → integrate(整合)

关键依赖：
  B1提前到A2之前执行，产出政策约束供A3使用
  A2的瓶颈分析结果供B2辩论使用
"""
import logging
from typing import Dict, Any, Optional

from swagent.stategraph.graph import StateGraph, ExecutionConfig
from swagent.stategraph.node import START, END
from swagent.llm4s.config import LLM4SConfig
from swagent.llm4s.llm_client import LLMClient
from swagent.llm4s.retrieval.hybrid_retriever import HybridRetriever
from swagent.llm4s.task2.step_a import StepANodes
from swagent.llm4s.task2.step_b import StepBNodes
from swagent.llm4s.task2.integration import IntegrationNode

logger = logging.getLogger(__name__)


def build_task2_workflow(
    config: Optional[LLM4SConfig] = None,
    llm: Optional[LLMClient] = None,
    retriever: Optional[HybridRetriever] = None,
):
    """
    构建任务二工作流

    由于StateGraph的并行边在当前实现中有限制（并行后无法继续），
    我们采用顺序执行但在节点内部使用asyncio并行的方式。

    实际执行流程：
    A1 → B1 → A2 → A3 → B2 → B3 → integrate
    B1提前到A2之前，使A3可以读取政策约束；A2在B2之前，使B2可以引用技术瓶颈。
    """
    config = config or LLM4SConfig()
    llm = llm or LLMClient(config)
    retriever = retriever or HybridRetriever(config)

    step_a = StepANodes(config, llm, retriever)
    step_b = StepBNodes(config, llm, retriever)
    integrator = IntegrationNode(config, llm)

    graph = StateGraph(name="TrendPrediction")

    # 注册节点
    graph.add_node("build_timeline", step_a.build_timeline)          # A1
    graph.add_node("analyze_current", step_a.analyze_current_status) # A2
    graph.add_node("predict_trends", step_a.predict_trends)          # A3
    graph.add_node("collect_policies", step_b.collect_policies)      # B1
    graph.add_node("run_debate", step_b.run_debate)                  # B2
    graph.add_node("analyze_conflicts", step_b.analyze_conflicts)    # B3
    graph.add_node("integrate", integrator.integrate)                # 整合

    # 定义边 - 顺序执行：A1 → B1 → A2 → A3 → B2 → B3 → integrate
    graph.add_edge(START, "build_timeline")
    graph.add_edge("build_timeline", "collect_policies")
    graph.add_edge("collect_policies", "analyze_current")
    graph.add_edge("analyze_current", "predict_trends")
    graph.add_edge("predict_trends", "run_debate")
    graph.add_edge("run_debate", "analyze_conflicts")
    graph.add_edge("analyze_conflicts", "integrate")
    graph.add_edge("integrate", END)

    exec_config = ExecutionConfig(
        max_iterations=20,
        save_checkpoints=True,
        timeout=1800,  # 30分钟超时（整个流程包含多个LLM调用）
    )
    return graph.compile(exec_config)


async def run_trend_prediction(
    topic: str,
    config: Optional[LLM4SConfig] = None,
) -> Dict[str, Any]:
    """
    运行研究趋势推演

    Args:
        topic: 分析的技术方向
        config: 配置

    Returns:
        ExecutionResult.state 包含 final_report
    """
    config = config or LLM4SConfig()
    workflow = build_task2_workflow(config)

    initial_state = {
        "topic": topic,
        "countries": config.countries,
    }

    logger.info(f"启动研究趋势推演工作流: {topic}")
    result = await workflow.invoke(initial_state)

    if result.success:
        logger.info(f"工作流完成，耗时 {result.duration:.1f}s，迭代 {result.iterations} 次")
    else:
        logger.error(f"工作流失败: {result.error}")

    return result.state
