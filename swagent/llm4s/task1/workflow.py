"""
任务一：实验方案设计 - StateGraph工作流
问题解析 → 知识检索 → 方案生成 ⇄ 方案评审 → 输出
"""
import logging
from typing import Dict, Any, Optional

from swagent.stategraph.graph import StateGraph, ExecutionConfig
from swagent.stategraph.node import START, END
from swagent.llm4s.config import LLM4SConfig
from swagent.llm4s.llm_client import LLMClient
from swagent.llm4s.retrieval.hybrid_retriever import HybridRetriever
from swagent.llm4s.task1.nodes import Task1Nodes

logger = logging.getLogger(__name__)


def build_task1_workflow(
    config: Optional[LLM4SConfig] = None,
    llm: Optional[LLMClient] = None,
    retriever: Optional[HybridRetriever] = None,
):
    """
    构建任务一工作流

    Returns:
        CompiledGraph 实例
    """
    config = config or LLM4SConfig()
    llm = llm or LLMClient(config)
    retriever = retriever or HybridRetriever(config)
    nodes = Task1Nodes(config, llm, retriever)

    graph = StateGraph(name="ExperimentDesign")

    # 注册节点
    graph.add_node("parse_question", nodes.parse_question)
    graph.add_node("retrieve_knowledge", nodes.retrieve_knowledge)
    graph.add_node("generate_scheme", nodes.generate_scheme)
    graph.add_node("review_scheme", nodes.review_scheme)
    graph.add_node("finalize", nodes.finalize)

    # 定义边
    graph.add_edge(START, "parse_question")
    graph.add_edge("parse_question", "retrieve_knowledge")
    graph.add_edge("retrieve_knowledge", "generate_scheme")
    graph.add_edge("generate_scheme", "review_scheme")

    # 条件路由：评审通过 → 输出，不通过 → 返回修改
    def review_router(state: Dict[str, Any]) -> str:
        if state.get("review_passed", False):
            return "pass"
        return "revise"

    graph.add_conditional_edge(
        "review_scheme",
        review_router,
        {"pass": "finalize", "revise": "generate_scheme"},
    )
    graph.add_edge("finalize", END)

    # 编译
    exec_config = ExecutionConfig(
        max_iterations=20,  # 足够支持3轮修订循环
        save_checkpoints=True,
    )
    return graph.compile(exec_config)


async def run_experiment_design(
    question: str,
    config: Optional[LLM4SConfig] = None,
) -> Dict[str, Any]:
    """
    运行实验方案设计

    Args:
        question: 用户的研究问题
        config: 配置

    Returns:
        ExecutionResult.state 包含 final_scheme
    """
    config = config or LLM4SConfig()
    workflow = build_task1_workflow(config)

    initial_state = {
        "research_question": question,
        "revision_count": 0,
    }

    logger.info(f"启动实验方案设计工作流: {question[:80]}...")
    result = await workflow.invoke(initial_state)

    if result.success:
        logger.info(f"工作流完成，耗时 {result.duration:.1f}s，迭代 {result.iterations} 次")
    else:
        logger.error(f"工作流失败: {result.error}")

    return result.state
