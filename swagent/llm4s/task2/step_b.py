"""
任务二步骤B：国家规划与利益博弈推演
B1: 政策信息收集
B2: Multi-Agent博弈推演
B3: 冲突分析与研究机遇识别
"""
import json
import asyncio
import logging
from typing import Dict, Any, List

from swagent.llm4s.config import LLM4SConfig
from swagent.llm4s.llm_client import LLMClient
from swagent.llm4s.retrieval.hybrid_retriever import HybridRetriever
from swagent.llm4s.prompts.task2_prompts import (
    POLICY_COLLECTION_SYSTEM, POLICY_COLLECTION_USER,
    DEBATE_POSITION_SYSTEM, DEBATE_POSITION_USER,
    DEBATE_RESPONSE_SYSTEM, DEBATE_RESPONSE_USER,
    OBSERVER_SUMMARY_SYSTEM, OBSERVER_SUMMARY_USER,
    CONFLICT_ANALYSIS_SYSTEM, CONFLICT_ANALYSIS_USER,
)

logger = logging.getLogger(__name__)

# 角色定义
ROLE_CONCERNS = {
    "UNEP": "全球环境可持续性、巴塞尔公约、塑料公约",
    "China": "碳达峰碳中和、垃圾分类、焚烧发电扩张",
    "EU": "循环经济、生产者责任延伸、减量化优先",
    "USA": "技术创新、市场化手段、各州标准差异",
    "Southeast Asia/Africa": "基础设施缺口、非正规回收、废物进口问题",
}

LOCATION_MAP = {
    "UNEP": None,
    "China": "China",
    "EU": "Europe",
    "USA": "United States",
    "Southeast Asia/Africa": None,
}


class StepBNodes:
    """步骤B节点集合"""

    def __init__(self, config: LLM4SConfig, llm: LLMClient, retriever: HybridRetriever):
        self.config = config
        self.llm = llm
        self.retriever = retriever

    async def collect_policies(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """B1: 政策信息收集 - 并行收集各方政策"""
        topic = state["topic"]
        countries = self.config.countries
        logger.info(f"[B1] 收集政策信息: {countries}")

        async def collect_one(role: str) -> Dict[str, Any]:
            concern = ROLE_CONCERNS.get(role, "")
            location = LOCATION_MAP.get(role)

            # KG检索该角色近5年的研究
            kg_result = await self.retriever.search(
                query=f"{topic} {role}",
                year_range=(2020, 2025),
                location=location,
                top_k=10,
            )

            # Tavily搜索政策信息
            tavily_results = self.retriever.tavily.search_for_policies(role, topic)
            tavily_context = "\n".join(
                f"- {r['title']}: {r['content'][:200]}" for r in tavily_results
            )

            messages = [
                {"role": "system", "content": POLICY_COLLECTION_SYSTEM},
                {"role": "user", "content": POLICY_COLLECTION_USER.format(
                    role=role, concern=concern, topic=topic,
                    kg_context=kg_result["context"][:2000],
                    tavily_context=tavily_context,
                )},
            ]
            result = await self.llm.chat_json(messages)
            result["role"] = role
            return result

        tasks = [collect_one(r) for r in countries]
        policies_list = await asyncio.gather(*tasks)

        # 添加调试日志
        logger.debug(f"[B1] policies_list 长度: {len(policies_list)}")
        for i, p in enumerate(policies_list):
            logger.debug(f"[B1] policy {i}: role={repr(p.get('role', 'MISSING'))}, keys={list(p.keys())}")

        country_policies = {p["role"]: p for p in policies_list}

        return {"country_policies": country_policies}

    async def run_debate(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """B2: Multi-Agent博弈推演"""
        topic = state["topic"]
        policies = state.get("country_policies", {})
        tech_trends = state.get("tech_trends", [])
        logger.info("[B2] 启动Multi-Agent博弈推演...")
        logger.info(f"[B2] 参与角色: {list(policies.keys())}")

        tech_trends_text = json.dumps(tech_trends, ensure_ascii=False, indent=2)[:3000]

        # 动态生成议题
        issues = [
            f"{topic}的全球发展路径选择",
            "废物跨境转移规则与巴塞尔公约执行",
            f"碳减排目标下{topic}的优先级排序",
            "技术转让、资金支持与能力建设",
        ]

        debate_history = []
        roles = list(policies.keys())

        for issue_idx, issue in enumerate(issues[:self.config.debate_topics_count], 1):
            logger.info(f"[B2] 议题 {issue_idx}/{self.config.debate_topics_count}: {issue}")
            issue_record = {"issue": issue, "round1": {}, "round2": {}, "observer": {}}

            # 第一轮：阐述立场
            logger.info(f"[B2] 议题 {issue_idx} - 第1轮：阐述立场")
            async def state_position(role: str) -> tuple:
                profile = json.dumps(policies.get(role, {}), ensure_ascii=False)[:2000]
                messages = [
                    {"role": "system", "content": DEBATE_POSITION_SYSTEM.format(role=role)},
                    {"role": "user", "content": DEBATE_POSITION_USER.format(
                        issue=issue, policy_profile=profile,
                        tech_trends=tech_trends_text, role=role,
                    )},
                ]
                logger.debug(f"[B2] {role} 阐述立场，prompt 长度: {len(messages[1]['content'])}")
                resp = await self.llm.chat(messages)
                logger.debug(f"[B2] {role} 立场阐述完成，响应长度: {len(resp)}")
                return role, resp

            r1_tasks = [state_position(r) for r in roles]
            r1_results = await asyncio.gather(*r1_tasks)
            for role, position in r1_results:
                issue_record["round1"][role] = position
            logger.info(f"[B2] 议题 {issue_idx} - 第1轮完成，{len(r1_results)} 个角色")

            # 第二轮：交叉质疑
            logger.info(f"[B2] 议题 {issue_idx} - 第2轮：交叉质疑")
            async def cross_challenge(role: str) -> tuple:
                my_pos = issue_record["round1"].get(role, "")
                others = "\n\n".join(
                    f"【{r}】: {p}" for r, p in issue_record["round1"].items() if r != role
                )
                messages = [
                    {"role": "system", "content": DEBATE_RESPONSE_SYSTEM.format(role=role)},
                    {"role": "user", "content": DEBATE_RESPONSE_USER.format(
                        issue=issue, my_position=my_pos,
                        other_positions=others, role=role,
                    )},
                ]
                logger.debug(f"[B2] {role} 交叉质疑，prompt 长度: {len(messages[1]['content'])}")
                try:
                    resp = await self.llm.chat_json(messages)
                    logger.debug(f"[B2] {role} 质疑完成，返回字段: {list(resp.keys())}")
                    return role, resp
                except Exception as e:
                    import traceback
                    logger.error(f"[B2] {role} 交叉质疑失败: {e}")
                    logger.error(f"[B2] 完整错误堆栈:\n{traceback.format_exc()}")
                    raise

            r2_tasks = [cross_challenge(r) for r in roles]
            try:
                r2_results = await asyncio.gather(*r2_tasks)
                for role, response in r2_results:
                    issue_record["round2"][role] = response
                logger.info(f"[B2] 议题 {issue_idx} - 第2轮完成")
            except Exception as e:
                import traceback
                logger.error(f"[B2] 第2轮gather失败: {e}")
                logger.error(f"[B2] 异常类型: {type(e)}")
                logger.error(f"[B2] 完整traceback:\n{traceback.format_exc()}")
                raise

            # 观察者总结
            logger.info(f"[B2] 议题 {issue_idx} - 观察者总结")
            r1_text = "\n\n".join(
                f"【{r}】: {p}" for r, p in issue_record["round1"].items()
            )
            r2_text = json.dumps(
                {r: resp for r, resp in r2_results}, ensure_ascii=False, indent=2
            )[:4000]

            messages = [
                {"role": "system", "content": OBSERVER_SUMMARY_SYSTEM},
                {"role": "user", "content": OBSERVER_SUMMARY_USER.format(
                    issue=issue, round1_positions=r1_text, round2_responses=r2_text,
                )},
            ]
            logger.debug(f"[B2] 观察者总结 prompt 长度: {len(messages[1]['content'])}")
            observer = await self.llm.chat_json(messages)
            issue_record["observer"] = observer
            logger.info(f"[B2] 议题 {issue_idx} - 观察者总结完成")

            debate_history.append(issue_record)

        logger.info(f"[B2] 所有辩论完成，共 {len(debate_history)} 个议题")
        return {"debate_history": debate_history}

    async def analyze_conflicts(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """B3: 冲突分析与研究机遇识别"""
        logger.info("[B3] 冲突分析与研究机遇识别...")

        tech_trends = state.get("tech_trends", [])
        debate_history = state.get("debate_history", [])

        # 构建辩论摘要
        debate_summary_parts = []
        for record in debate_history:
            obs = record.get("observer", {})
            debate_summary_parts.append(
                f"议题: {record.get('issue', '')}\n"
                f"核心分歧: {json.dumps(obs.get('core_divergences', []), ensure_ascii=False)}\n"
                f"共识: {json.dumps(obs.get('consensus_areas', []), ensure_ascii=False)}\n"
                f"根本冲突: {json.dumps(obs.get('fundamental_conflicts', []), ensure_ascii=False)}"
            )
        debate_summary = "\n\n".join(debate_summary_parts)

        messages = [
            {"role": "system", "content": CONFLICT_ANALYSIS_SYSTEM},
            {"role": "user", "content": CONFLICT_ANALYSIS_USER.format(
                tech_trends=json.dumps(tech_trends, ensure_ascii=False, indent=2)[:3000],
                debate_summary=debate_summary[:4000],
            )},
        ]
        result = await self.llm.chat_json(messages, max_tokens=8192)

        return {"conflicts": result}
