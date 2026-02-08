"""
任务二：研究趋势推演 - 结果整合
"""
import json
import logging
from typing import Dict, Any

from swagent.llm4s.config import LLM4SConfig
from swagent.llm4s.llm_client import LLMClient
from swagent.llm4s.prompts.task2_prompts import (
    INTEGRATION_SYSTEM, INTEGRATION_USER,
)

logger = logging.getLogger(__name__)


class IntegrationNode:
    """结果整合节点"""

    def __init__(self, config: LLM4SConfig, llm: LLMClient):
        self.config = config
        self.llm = llm

    async def integrate(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """整合所有分析结果生成最终报告"""
        topic = state["topic"]
        logger.info(f"[整合] 生成综合研究趋势推演报告: {topic}")

        # 构建时间线文本
        timeline = state.get("timeline", [])
        timeline_text = "\n".join(
            f"- {t.get('period', '')}: {t.get('summary', '')} (论文数: {t.get('paper_count', 0)})"
            for t in timeline
        )

        # 当前现状
        current = state.get("current_status", {})
        current_text = json.dumps(current, ensure_ascii=False, indent=2)[:3000]

        # 技术趋势
        trends = state.get("tech_trends", [])
        trends_text = json.dumps(trends, ensure_ascii=False, indent=2)[:3000]

        # 政策画像
        policies = state.get("country_policies", {})
        policies_text = json.dumps(policies, ensure_ascii=False, indent=2)[:4000]

        # 辩论结果
        debate = state.get("debate_history", [])
        debate_text = ""
        for record in debate:
            obs = record.get("observer", {})
            debate_text += f"\n议题: {record.get('issue', '')}\n"
            debate_text += f"总结: {obs.get('summary', '')}\n"
        debate_text = debate_text[:3000]

        # 冲突分析
        conflicts = state.get("conflicts", {})
        conflicts_text = json.dumps(conflicts, ensure_ascii=False, indent=2)[:3000]

        messages = [
            {"role": "system", "content": INTEGRATION_SYSTEM},
            {"role": "user", "content": INTEGRATION_USER.format(
                topic=topic,
                timeline=timeline_text,
                current_status=current_text,
                tech_trends=trends_text,
                policies=policies_text,
                debate_results=debate_text,
                conflicts=conflicts_text,
            )},
        ]
        result = await self.llm.chat_json(messages, max_tokens=8192)

        return {"final_report": result}
