"""
Level 3: Tavily网络搜索
实时搜索互联网补充KG中未覆盖的最新信息
"""
import logging
from typing import List, Dict, Any, Optional

from tavily import TavilyClient

logger = logging.getLogger(__name__)


class TavilyRetriever:
    """Tavily网络搜索检索器"""

    def __init__(self, api_key: str):
        self.client = TavilyClient(api_key=api_key)

    def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "advanced",
        include_domains: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        执行Tavily搜索

        Args:
            query: 搜索查询
            max_results: 最大结果数
            search_depth: 搜索深度 ("basic" / "advanced")
            include_domains: 限定搜索域名

        Returns:
            搜索结果列表，每项包含 title, url, content
        """
        try:
            params: Dict[str, Any] = {
                "query": query,
                "max_results": max_results,
                "search_depth": search_depth,
            }
            if include_domains:
                params["include_domains"] = include_domains

            response = self.client.search(**params)
            results = []
            for item in response.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("content", ""),
                    "score": item.get("score", 0.0),
                })
            logger.info(f"Tavily搜索 '{query[:50]}...' 返回 {len(results)} 条结果")
            return results
        except Exception as e:
            logger.error(f"Tavily搜索失败: {e}")
            return []

    def search_for_policies(
        self,
        country: str,
        topic: str,
    ) -> List[Dict[str, Any]]:
        """搜索特定国家的固废政策"""
        query = f"{country} solid waste management policy {topic} 2024 2025"
        domains = [
            "unep.org", "who.int", "worldbank.org",
            "europa.eu", "epa.gov", "gov.cn",
        ]
        return self.search(query, include_domains=domains)

    def search_latest_research(
        self,
        topic: str,
    ) -> List[Dict[str, Any]]:
        """搜索最新研究进展"""
        query = f"latest research {topic} solid waste 2024 2025"
        domains = [
            "sciencedirect.com", "springer.com", "nature.com",
            "wiley.com", "mdpi.com", "acs.org",
        ]
        return self.search(query, include_domains=domains)
