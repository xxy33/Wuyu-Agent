"""
三级混合检索器
整合KG结构化检索、向量语义检索和Tavily网络搜索
"""
import json
import logging
import numpy as np
from typing import List, Dict, Any, Optional

from swagent.llm4s.config import LLM4SConfig
from swagent.llm4s.llm_client import EmbeddingClient
from swagent.llm4s.retrieval.kg_retriever import KGRetriever
from swagent.llm4s.retrieval.vector_retriever import VectorRetriever
from swagent.llm4s.retrieval.tavily_retriever import TavilyRetriever

logger = logging.getLogger(__name__)


class PaperStore:
    """论文摘要存储 - 通过Article Title快速查找摘要"""

    def __init__(self, papers_path: str):
        self.papers_path = papers_path
        self._title_map: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        logger.info(f"加载论文数据: {self.papers_path}")
        with open(self.papers_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    paper = json.loads(line)
                    title = paper.get("Article Title", "")
                    if title:
                        self._title_map[title] = paper
                except json.JSONDecodeError:
                    continue
        self._loaded = True
        logger.info(f"论文数据加载完成，共 {len(self._title_map)} 条")

    def get_abstract(self, title: str) -> str:
        """根据论文标题获取摘要"""
        self.load()
        paper = self._title_map.get(title, {})
        return paper.get("Abstract", "")

    def get_paper(self, title: str) -> Optional[Dict[str, Any]]:
        """根据标题获取完整论文信息"""
        self.load()
        return self._title_map.get(title)


class HybridRetriever:
    """三级混合检索器"""

    def __init__(self, config: Optional[LLM4SConfig] = None):
        self.config = config or LLM4SConfig()
        self.kg = KGRetriever(self.config.kg_path)
        self.vector = VectorRetriever(self.config.faiss_index_path, self.config.embedding_dim)
        self.tavily = TavilyRetriever(self.config.tavily_api_key)
        self.papers = PaperStore(self.config.papers_path)
        self.embedder = EmbeddingClient(self.config)

    async def search(
        self,
        query: str,
        year_range: Optional[tuple] = None,
        location: Optional[str] = None,
        waste_keywords: Optional[List[str]] = None,
        tech_keywords: Optional[List[str]] = None,
        top_k: int = 15,
        use_tavily: bool = False,
        tavily_query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        三级混合检索

        Returns:
            {
                "kg_results": [...],      # KG条目（含摘要）
                "tavily_results": [...],  # Tavily搜索结果
                "context": str,           # 合并后的上下文文本
            }
        """
        # Level 1: KG结构化过滤
        kg_filtered = self.kg.search(
            year_range=year_range,
            location=location,
            waste_keywords=waste_keywords,
            tech_keywords=tech_keywords,
            max_results=self.config.kg_top_k,
        )
        candidate_ids = {str(e.get("id", "")) for e in kg_filtered} if kg_filtered else None

        # Level 2: 向量语义检索
        vector_results = []
        if self.vector.is_loaded or self.vector.load_index():
            query_vec = await self.embedder.embed_single(query)
            query_arr = np.array(query_vec, dtype=np.float32)
            vector_results = self.vector.search(
                query_arr, top_k=top_k, candidate_ids=candidate_ids
            )

        # 合并去重
        seen_ids = set()
        merged_entries = []

        # 先放向量检索结果（按相似度排序）
        for doc_id, score in vector_results:
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            entries = self.kg.get_entries_by_ids([doc_id])
            if entries:
                entry = entries[0].copy()
                entry["_score"] = score
                entry["_abstract"] = self.papers.get_abstract(doc_id)
                merged_entries.append(entry)

        # 补充KG结构化结果中未被向量检索覆盖的
        for entry in kg_filtered:
            doc_id = str(entry.get("id", ""))
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            entry_copy = entry.copy()
            entry_copy["_abstract"] = self.papers.get_abstract(doc_id)
            merged_entries.append(entry_copy)
            if len(merged_entries) >= top_k:
                break

        # Level 3: Tavily（按需）
        tavily_results = []
        if use_tavily:
            tq = tavily_query or query
            tavily_results = self.tavily.search(tq)

        # 构建上下文文本
        context = self._build_context(merged_entries[:top_k], tavily_results)

        return {
            "kg_results": merged_entries[:top_k],
            "tavily_results": tavily_results,
            "context": context,
        }

    def _build_context(
        self,
        entries: List[Dict[str, Any]],
        tavily_results: List[Dict[str, Any]],
    ) -> str:
        """构建注入LLM的上下文文本"""
        parts = []

        if entries:
            parts.append("=== 知识图谱检索结果 ===\n")
            for i, entry in enumerate(entries, 1):
                meta = entry.get("Meta_Info", {})
                proc = entry.get("Process_Event", {})
                abstract = entry.get("_abstract", "")
                text = (
                    f"[{i}] {str(entry.get('id', 'Unknown'))}\n"
                    f"  年份: {meta.get('Year', 'N/A')} | "
                    f"地区: {meta.get('Location', 'N/A')}\n"
                    f"  废物类型: {proc.get('Waste_Object', 'N/A')}\n"
                    f"  技术: {proc.get('Technology', 'N/A')}\n"
                    f"  关键结果: {proc.get('Key_Results', 'N/A')}\n"
                    f"  影响: {proc.get('Impact_Effect', 'N/A')}\n"
                )
                if abstract:
                    # 截断过长摘要
                    if len(abstract) > 500:
                        abstract = abstract[:500] + "..."
                    text += f"  摘要: {abstract}\n"
                parts.append(text)

        if tavily_results:
            parts.append("\n=== 网络搜索补充 ===\n")
            for i, item in enumerate(tavily_results, 1):
                content = item.get("content", "")
                if len(content) > 300:
                    content = content[:300] + "..."
                parts.append(
                    f"[Web-{i}] {item.get('title', '')}\n"
                    f"  来源: {item.get('url', '')}\n"
                    f"  内容: {content}\n"
                )

        return "\n".join(parts)
