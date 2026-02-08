"""
Level 1: KG结构化检索
按字段过滤知识图谱条目（年份、地区、废物类型、技术关键词）
"""
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class KGRetriever:
    """知识图谱结构化检索器"""

    def __init__(self, kg_path: str):
        self.kg_path = kg_path
        self._entries: List[Dict[str, Any]] = []
        self._loaded = False

    def load(self) -> None:
        """加载KG数据到内存"""
        if self._loaded:
            return
        logger.info(f"加载知识图谱: {self.kg_path}")
        with open(self.kg_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    self._entries.append(entry)
                except json.JSONDecodeError:
                    continue
        self._loaded = True
        logger.info(f"知识图谱加载完成，共 {len(self._entries)} 条")

    def search(
        self,
        year_range: Optional[tuple] = None,
        location: Optional[str] = None,
        waste_keywords: Optional[List[str]] = None,
        tech_keywords: Optional[List[str]] = None,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        结构化过滤检索

        Args:
            year_range: (start_year, end_year) 年份范围
            location: 地区关键词
            waste_keywords: 废物类型关键词列表
            tech_keywords: 技术关键词列表
            max_results: 最大返回数量
        """
        self.load()
        results = []
        for entry in self._entries:
            if not self._match(entry, year_range, location, waste_keywords, tech_keywords):
                continue
            results.append(entry)
            if len(results) >= max_results:
                break
        return results

    def get_ids(self) -> List[str]:
        """获取所有条目的id列表"""
        self.load()
        return [str(e.get("id", "")) for e in self._entries]

    def get_entries_by_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        """根据id列表获取条目"""
        self.load()
        id_set = set(ids)
        return [e for e in self._entries if str(e.get("id", "")) in id_set]

    @property
    def entries(self) -> List[Dict[str, Any]]:
        self.load()
        return self._entries

    @staticmethod
    def _match(
        entry: Dict[str, Any],
        year_range: Optional[tuple],
        location: Optional[str],
        waste_keywords: Optional[List[str]],
        tech_keywords: Optional[List[str]],
    ) -> bool:
        """判断条目是否匹配过滤条件"""
        meta = entry.get("Meta_Info", {})
        proc = entry.get("Process_Event", {})

        # 年份过滤
        if year_range:
            year_str = meta.get("Year", "")
            if not year_str:
                return False
            try:
                year = int(year_str)
            except (ValueError, TypeError):
                return False
            if year < year_range[0] or year > year_range[1]:
                return False

        # 地区过滤
        if location:
            loc = (meta.get("Location") or "").lower()
            if location.lower() not in loc:
                return False

        # 废物类型关键词
        if waste_keywords:
            waste_obj = (proc.get("Waste_Object") or "").lower()
            if not any(kw.lower() in waste_obj for kw in waste_keywords):
                return False

        # 技术关键词
        if tech_keywords:
            tech = (proc.get("Technology") or "").lower()
            if not any(kw.lower() in tech for kw in tech_keywords):
                return False

        return True

    @staticmethod
    def entry_to_text(entry: Dict[str, Any]) -> str:
        """将KG条目转为可索引的文本"""
        parts = [str(entry.get("id", ""))]
        proc = entry.get("Process_Event", {})
        for field in ["Waste_Object", "Technology", "Key_Results", "Impact_Effect"]:
            val = proc.get(field)
            if val and val != "null":
                parts.append(str(val))
        return " | ".join(parts)
