"""
工具结果预算管理 - 防止大结果撑爆上下文
参考 Claude Code toolResultStorage.ts 设计

两级预算:
1. 单工具级: 超过 max_per_tool 字符的结果持久化到磁盘
2. 消息聚合级: 同一轮所有工具结果总和不超 max_per_message 字符
"""
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


PREVIEW_SIZE = 2000  # 预览保留前 2000 字符


@dataclass
class PersistedResult:
    """持久化结果的引用"""
    tool_use_id: str
    tool_name: str
    file_path: str
    original_size: int
    preview: str


class ResultBudgetManager:
    """
    工具结果预算管理器

    用法:
        mgr = ResultBudgetManager()
        text = mgr.process_result("id-1", "grep_tool", huge_result_text)
        # 如果超限，text 是持久化引用 + 预览；否则原样返回
    """

    def __init__(
        self,
        max_per_tool: int = 50000,
        max_per_message: int = 200000,
        storage_dir: Optional[str] = None,
    ):
        """
        Args:
            max_per_tool: 单个工具结果最大字符数
            max_per_message: 同一轮消息中所有工具结果的最大总字符数
            storage_dir: 持久化存储目录
        """
        self.max_per_tool = max_per_tool
        self.max_per_message = max_per_message

        if storage_dir is None:
            storage_dir = os.path.join(
                os.path.expanduser("~"), ".swagent", "tool-results"
            )
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)

        # 已持久化结果的记录 (防止重复处理)
        self._persisted: Dict[str, PersistedResult] = {}

    def process_result(
        self, tool_use_id: str, tool_name: str, result_text: str
    ) -> str:
        """
        处理单个工具结果

        如果结果超过 max_per_tool，持久化到磁盘并返回预览引用。

        Args:
            tool_use_id: 工具调用 ID
            tool_name: 工具名称
            result_text: 原始结果文本

        Returns:
            原始文本或持久化引用文本
        """
        if len(result_text) <= self.max_per_tool:
            return result_text

        # 已经持久化过
        if tool_use_id in self._persisted:
            return self._build_reference(self._persisted[tool_use_id])

        # 持久化到磁盘
        file_path = self._persist_to_disk(tool_use_id, tool_name, result_text)
        preview = result_text[:PREVIEW_SIZE]

        record = PersistedResult(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            file_path=file_path,
            original_size=len(result_text),
            preview=preview,
        )
        self._persisted[tool_use_id] = record

        return self._build_reference(record)

    def enforce_message_budget(
        self, tool_results: List[Dict]
    ) -> List[Dict]:
        """
        消息级预算执行

        如果同一轮所有工具结果总和超过 max_per_message，
        从最大的结果开始持久化，直到总量低于预算。

        Args:
            tool_results: [{"tool_use_id": str, "tool_name": str, "content": str}, ...]

        Returns:
            处理后的 tool_results (大结果被替换为引用)
        """
        total = sum(len(r.get("content", "")) for r in tool_results)
        if total <= self.max_per_message:
            return tool_results

        # 按大小降序排列索引
        indexed = sorted(
            enumerate(tool_results),
            key=lambda x: len(x[1].get("content", "")),
            reverse=True,
        )

        result = list(tool_results)  # 浅拷贝
        for idx, item in indexed:
            if total <= self.max_per_message:
                break
            content = item.get("content", "")
            if len(content) <= self.max_per_tool:
                continue  # 已经在单工具限制内

            # 持久化
            new_content = self.process_result(
                item.get("tool_use_id", f"budget-{idx}"),
                item.get("tool_name", "unknown"),
                content,
            )
            saved = len(content) - len(new_content)
            total -= saved
            result[idx] = {**item, "content": new_content}

        return result

    def _persist_to_disk(self, tool_use_id: str, tool_name: str, content: str) -> str:
        """将结果写入磁盘"""
        safe_id = tool_use_id.replace("/", "_").replace("\\", "_")
        filename = f"{safe_id}.txt"
        file_path = os.path.join(self.storage_dir, filename)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return file_path

    @staticmethod
    def _build_reference(record: PersistedResult) -> str:
        """构建持久化引用文本"""
        size_kb = record.original_size / 1024
        return (
            f"[结果已持久化] 工具: {record.tool_name}, "
            f"原始大小: {size_kb:.1f}KB\n"
            f"完整输出已保存至: {record.file_path}\n"
            f"预览(前{PREVIEW_SIZE}字符):\n"
            f"{record.preview}\n..."
        )

    @property
    def persisted_count(self) -> int:
        """已持久化的结果数量"""
        return len(self._persisted)
