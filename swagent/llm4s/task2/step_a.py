"""
任务二步骤A：时间线趋势推演
A1: 历史时间线构建
A2: 当前研究现状分析
A3: 趋势推断
"""
import json
import asyncio
import logging
from typing import Dict, Any, List, Optional

from swagent.llm4s.config import LLM4SConfig
from swagent.llm4s.llm_client import LLMClient
from swagent.llm4s.retrieval.hybrid_retriever import HybridRetriever
from swagent.llm4s.prompts.task2_prompts import (
    TIMELINE_ANALYSIS_SYSTEM, TIMELINE_ANALYSIS_USER,
    CURRENT_STATUS_SYSTEM, CURRENT_STATUS_USER,
    TREND_PREDICTION_SYSTEM, TREND_PREDICTION_USER,
)

logger = logging.getLogger(__name__)


class StepANodes:
    """步骤A节点集合"""

    def __init__(self, config: LLM4SConfig, llm: LLMClient, retriever: HybridRetriever):
        self.config = config
        self.llm = llm
        self.retriever = retriever

    async def build_timeline(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """A1: 历史时间线构建 - 对每个时段并行分析"""
        topic = state["topic"]
        periods = self.config.time_periods
        logger.info(f"[A1] 构建历史时间线: {topic}, {len(periods)}个时段")

        async def analyze_period(period: str) -> Dict[str, Any]:
            start, end = period.split("-")
            # KG检索该时段的文献
            search_result = await self.retriever.search(
                query=topic,
                year_range=(int(start), int(end)),
                top_k=self.config.timeline_top_k,
            )
            context = search_result["context"]
            count = len(search_result["kg_results"])

            messages = [
                {"role": "system", "content": TIMELINE_ANALYSIS_SYSTEM},
                {"role": "user", "content": TIMELINE_ANALYSIS_USER.format(
                    topic=topic, period=period, count=count, context=context,
                )},
            ]
            result = await self.llm.chat_json(messages, temperature=self.config.low_temperature)
            result["period"] = period
            result["paper_count"] = count
            return result

        # 并行分析所有时段
        tasks = [analyze_period(p) for p in periods]
        timeline = await asyncio.gather(*tasks)

        return {"timeline": list(timeline)}

    async def analyze_current_status(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """A2: 当前研究现状分析"""
        topic = state["topic"]
        logger.info(f"[A2] 分析当前研究现状: {topic}")

        # KG检索近3年文献
        kg_result = await self.retriever.search(
            query=topic,
            year_range=(2022, 2025),
            top_k=20,
        )

        # Tavily搜索最新进展
        tavily_results = self.retriever.tavily.search_latest_research(topic)
        tavily_context = "\n".join(
            f"- {r['title']}: {r['content'][:200]}" for r in tavily_results
        )

        messages = [
            {"role": "system", "content": CURRENT_STATUS_SYSTEM},
            {"role": "user", "content": CURRENT_STATUS_USER.format(
                topic=topic,
                kg_context=kg_result["context"],
                tavily_context=tavily_context,
            )},
        ]
        result = await self.llm.chat_json(messages)

        return {"current_status": result}

    async def predict_trends(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """A3: 趋势推断"""
        topic = state["topic"]
        logger.info(f"[A3] 推断未来趋势: {topic}")

        # 构建时间线摘要
        timeline = state.get("timeline", [])
        timeline_summary = "\n".join(
            f"- {t.get('period', '')}: {t.get('summary', '')}" for t in timeline
        )

        # 当前现状摘要
        current = state.get("current_status", {})
        current_summary = current.get("summary", "")

        # Tavily搜索最前沿动态
        tavily_results = self.retriever.tavily.search(
            f"latest breakthrough {topic} solid waste 2024 2025",
            max_results=5,
        )
        latest_dynamics = "\n".join(
            f"- {r['title']}: {r['content'][:200]}" for r in tavily_results
        )

        messages = [
            {"role": "system", "content": TREND_PREDICTION_SYSTEM},
            {"role": "user", "content": TREND_PREDICTION_USER.format(
                topic=topic,
                timeline_summary=timeline_summary,
                current_status=json.dumps(current, ensure_ascii=False, indent=2)[:3000],
                latest_dynamics=latest_dynamics,
            )},
        ]
        result = await self.llm.chat_json(messages, max_tokens=8192)

        return {"tech_trends": result.get("trends", [])}
