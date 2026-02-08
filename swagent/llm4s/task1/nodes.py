"""
任务一：实验方案设计 - 工作流节点实现
"""
import json
import logging
from typing import Dict, Any

from swagent.llm4s.config import LLM4SConfig
from swagent.llm4s.llm_client import LLMClient
from swagent.llm4s.retrieval.hybrid_retriever import HybridRetriever
from swagent.llm4s.prompts.task1_prompts import (
    QUESTION_PARSE_SYSTEM, QUESTION_PARSE_USER,
    SCHEME_GENERATION_SYSTEM, SCHEME_GENERATION_USER,
    SCHEME_REVISION_USER,
    SCHEME_REVIEW_SYSTEM, SCHEME_REVIEW_USER,
)

logger = logging.getLogger(__name__)


class Task1Nodes:
    """任务一工作流节点集合"""

    def __init__(self, config: LLM4SConfig, llm: LLMClient, retriever: HybridRetriever):
        self.config = config
        self.llm = llm
        self.retriever = retriever

    async def parse_question(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """节点1：问题解析"""
        question = state["research_question"]
        logger.info(f"[节点1] 解析研究问题: {question[:80]}...")

        messages = [
            {"role": "system", "content": QUESTION_PARSE_SYSTEM},
            {"role": "user", "content": QUESTION_PARSE_USER.format(question=question)},
        ]
        result = await self.llm.chat_json(messages, temperature=self.config.low_temperature)

        return {
            "entities": result.get("entities", []),
            "goal": result.get("goal", ""),
            "waste_type": result.get("waste_type", ""),
            "technology": result.get("technology", ""),
            "constraints": result.get("constraints", "无"),
            "search_keywords": result.get("search_keywords", {}),
        }

    async def retrieve_knowledge(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """节点2：知识检索"""
        logger.info("[节点2] 执行三级知识检索...")
        keywords = state.get("search_keywords", {})
        waste_kw = keywords.get("waste_keywords", [])
        tech_kw = keywords.get("tech_keywords", [])

        query = f"{state.get('goal', '')} {state.get('waste_type', '')} {state.get('technology', '')}"

        # 判断是否需要Tavily补充
        use_tavily = bool(tech_kw)  # 有技术关键词时搜索最新研究

        search_result = await self.retriever.search(
            query=query,
            waste_keywords=waste_kw if waste_kw else None,
            tech_keywords=tech_kw if tech_kw else None,
            top_k=self.config.vector_top_k,
            use_tavily=use_tavily,
            tavily_query=f"latest research {' '.join(tech_kw[:3])} solid waste" if tech_kw else None,
        )

        related_papers = []
        for entry in search_result["kg_results"]:
            related_papers.append({
                "title": str(entry.get("id", "")),
                "year": entry.get("Meta_Info", {}).get("Year", ""),
                "location": entry.get("Meta_Info", {}).get("Location", ""),
                "technology": entry.get("Process_Event", {}).get("Technology", ""),
                "abstract": entry.get("_abstract", "")[:300],
            })

        return {
            "context": search_result["context"],
            "related_papers": related_papers,
            "tavily_results": search_result["tavily_results"],
        }

    async def generate_scheme(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """节点3：方案生成"""
        revision_count = state.get("revision_count", 0)
        logger.info(f"[节点3] 生成实验方案 (第{revision_count + 1}版)...")

        if revision_count > 0 and state.get("draft_scheme"):
            # 修订模式
            user_content = SCHEME_REVISION_USER.format(
                question=state["research_question"],
                previous_scheme=json.dumps(state["draft_scheme"], ensure_ascii=False, indent=2),
                review_comments="\n".join(state.get("review_comments", [])),
                review_score=state.get("review_score", 0),
                key_issues="\n".join(state.get("key_issues", [])),
                context=state.get("context", ""),
            )
        else:
            # 首次生成
            user_content = SCHEME_GENERATION_USER.format(
                question=state["research_question"],
                goal=state.get("goal", ""),
                waste_type=state.get("waste_type", ""),
                technology=state.get("technology", ""),
                context=state.get("context", ""),
            )

        messages = [
            {"role": "system", "content": SCHEME_GENERATION_SYSTEM},
            {"role": "user", "content": user_content},
        ]
        scheme = await self.llm.chat_json(messages, max_tokens=8192)

        return {"draft_scheme": scheme}

    async def review_scheme(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """节点4：方案评审"""
        logger.info("[节点4] 评审实验方案...")

        messages = [
            {"role": "system", "content": SCHEME_REVIEW_SYSTEM},
            {"role": "user", "content": SCHEME_REVIEW_USER.format(
                question=state["research_question"],
                scheme=json.dumps(state.get("draft_scheme", {}), ensure_ascii=False, indent=2),
                context=state.get("context", "")[:3000],
            )},
        ]
        review = await self.llm.chat_json(messages, temperature=self.config.low_temperature)

        total_score = review.get("total_score", 0)
        passed = total_score >= self.config.review_pass_threshold
        revision_count = state.get("revision_count", 0) + 1

        # 如果不通过且已达最大修订次数，强制通过
        if not passed and revision_count >= self.config.max_revision_rounds:
            logger.warning(f"已达最大修订次数({self.config.max_revision_rounds})，强制输出当前版本")
            passed = True

        return {
            "review_score": total_score,
            "review_passed": passed,
            "review_comments": review.get("overall_comments", []),
            "key_issues": review.get("key_issues", []),
            "review_detail": review.get("scores", {}),
            "revision_count": revision_count,
        }

    async def finalize(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """输出节点：整理最终方案"""
        logger.info("[输出] 整理最终实验方案...")
        scheme = state.get("draft_scheme", {})
        scheme["_review"] = {
            "scores": state.get("review_detail", {}),
            "total_score": state.get("review_score", 0),
            "comments": state.get("review_comments", []),
        }
        scheme["_meta"] = {
            "revision_count": state.get("revision_count", 0),
            "papers_count": len(state.get("related_papers", [])),
        }
        return {"final_scheme": scheme}
