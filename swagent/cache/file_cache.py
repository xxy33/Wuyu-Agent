"""
文件状态缓存 - LRU 缓存避免重复磁盘读取
参考 Claude Code fileStateCache.ts 设计

特性:
- LRU 淘汰策略 (最近最少使用)
- 双重限制: 最大条目数 + 最大总字节数
- 路径标准化 (消除 .., 符号链接等差异)
- clone() 支持子 Agent 缓存共享
"""
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional, Dict
from copy import deepcopy


@dataclass
class FileState:
    """单个文件的缓存状态"""
    content: str
    file_path: str
    timestamp: float = field(default_factory=time.time)
    size_bytes: int = 0
    offset: Optional[int] = None
    limit: Optional[int] = None
    is_partial: bool = False

    def __post_init__(self):
        if self.size_bytes == 0:
            self.size_bytes = len(self.content.encode('utf-8'))


class FileStateCache:
    """
    LRU 文件状态缓存

    用法:
        cache = FileStateCache()
        cache.put("/path/to/file", content)
        state = cache.get("/path/to/file")  # 命中缓存
    """

    def __init__(self, max_entries: int = 100, max_size_bytes: int = 25 * 1024 * 1024):
        """
        Args:
            max_entries: 最大缓存条目数
            max_size_bytes: 最大总缓存字节数 (默认 25MB)
        """
        self.max_entries = max_entries
        self.max_size_bytes = max_size_bytes
        self._cache: OrderedDict[str, FileState] = OrderedDict()
        self._total_bytes: int = 0

    @staticmethod
    def _normalize_path(path: str) -> str:
        """标准化路径"""
        return os.path.normpath(os.path.abspath(os.path.expanduser(path)))

    def get(self, path: str) -> Optional[FileState]:
        """
        获取缓存的文件状态

        Args:
            path: 文件路径

        Returns:
            FileState 或 None (缓存未命中)
        """
        key = self._normalize_path(path)
        if key in self._cache:
            # LRU: 移到末尾 (最近访问)
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(
        self,
        path: str,
        content: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        is_partial: bool = False,
    ) -> None:
        """
        写入缓存

        Args:
            path: 文件路径
            content: 文件内容
            offset: 读取偏移
            limit: 读取限制
            is_partial: 是否部分视图
        """
        key = self._normalize_path(path)

        state = FileState(
            content=content,
            file_path=key,
            offset=offset,
            limit=limit,
            is_partial=is_partial,
        )

        # 如果已存在，先减去旧的大小
        if key in self._cache:
            self._total_bytes -= self._cache[key].size_bytes

        # 单条目超过总限制的一半则不缓存
        if state.size_bytes > self.max_size_bytes // 2:
            return

        self._cache[key] = state
        self._cache.move_to_end(key)
        self._total_bytes += state.size_bytes

        # 淘汰: 条目数超限
        while len(self._cache) > self.max_entries:
            self._evict_oldest()

        # 淘汰: 总字节超限
        while self._total_bytes > self.max_size_bytes and self._cache:
            self._evict_oldest()

    def invalidate(self, path: str) -> bool:
        """
        使指定路径的缓存失效

        Returns:
            是否存在并移除
        """
        key = self._normalize_path(path)
        if key in self._cache:
            self._total_bytes -= self._cache[key].size_bytes
            del self._cache[key]
            return True
        return False

    def clear(self):
        """清空全部缓存"""
        self._cache.clear()
        self._total_bytes = 0

    def clone(self) -> 'FileStateCache':
        """
        深拷贝缓存 (用于子 Agent 隔离)

        Returns:
            独立的 FileStateCache 副本
        """
        new_cache = FileStateCache(self.max_entries, self.max_size_bytes)
        for key, state in self._cache.items():
            new_cache._cache[key] = FileState(
                content=state.content,
                file_path=state.file_path,
                timestamp=state.timestamp,
                size_bytes=state.size_bytes,
                offset=state.offset,
                limit=state.limit,
                is_partial=state.is_partial,
            )
        new_cache._total_bytes = self._total_bytes
        return new_cache

    def _evict_oldest(self):
        """淘汰最旧的条目 (LRU)"""
        if self._cache:
            _, state = self._cache.popitem(last=False)
            self._total_bytes -= state.size_bytes

    @property
    def stats(self) -> Dict:
        """缓存统计"""
        return {
            "entries": len(self._cache),
            "max_entries": self.max_entries,
            "total_bytes": self._total_bytes,
            "max_bytes": self.max_size_bytes,
            "utilization": round(self._total_bytes / self.max_size_bytes * 100, 1) if self.max_size_bytes else 0,
        }

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, path: str) -> bool:
        return self._normalize_path(path) in self._cache
