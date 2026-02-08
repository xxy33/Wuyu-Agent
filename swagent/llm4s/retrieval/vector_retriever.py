"""
Level 2: 向量语义检索
基于FAISS的向量索引，支持带过滤条件的ANN检索
"""
import json
import os
import logging
import pickle
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class VectorRetriever:
    """向量语义检索器"""

    def __init__(self, index_dir: str, dim: int = 1024):
        self.index_dir = index_dir
        self.dim = dim
        self._index = None
        self._id_list: List[str] = []  # faiss行号 -> KG id
        self._loaded = False

    def build_index(
        self,
        ids: List[str],
        vectors: np.ndarray,
    ) -> None:
        """构建FAISS索引并保存"""
        import faiss

        logger.info(f"构建FAISS索引: {len(ids)} 条, 维度 {vectors.shape[1]}")
        self.dim = vectors.shape[1]

        # 使用IVF索引加速大规模检索
        nlist = min(256, max(1, len(ids) // 100))
        quantizer = faiss.IndexFlatIP(self.dim)
        index = faiss.IndexIVFFlat(quantizer, self.dim, nlist, faiss.METRIC_INNER_PRODUCT)

        # 归一化向量（用于余弦相似度）
        faiss.normalize_L2(vectors)
        index.train(vectors)
        index.add(vectors)

        self._index = index
        self._id_list = ids
        self._loaded = True

        # 保存
        os.makedirs(self.index_dir, exist_ok=True)
        faiss.write_index(index, os.path.join(self.index_dir, "index.faiss"))
        with open(os.path.join(self.index_dir, "id_list.pkl"), "wb") as f:
            pickle.dump(ids, f)
        logger.info("FAISS索引构建并保存完成")

    def load_index(self) -> bool:
        """加载已有的FAISS索引"""
        import faiss

        index_file = os.path.join(self.index_dir, "index.faiss")
        id_file = os.path.join(self.index_dir, "id_list.pkl")
        if not os.path.exists(index_file) or not os.path.exists(id_file):
            logger.warning("FAISS索引文件不存在")
            return False

        self._index = faiss.read_index(index_file)
        with open(id_file, "rb") as f:
            self._id_list = pickle.load(f)
        self._loaded = True
        logger.info(f"FAISS索引加载完成，共 {len(self._id_list)} 条")
        return True

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 15,
        candidate_ids: Optional[set] = None,
    ) -> List[Tuple[str, float]]:
        """
        向量检索

        Args:
            query_vector: 查询向量 (dim,)
            top_k: 返回数量
            candidate_ids: 候选id集合（用于叠加Level 1过滤）

        Returns:
            [(id, score), ...] 按相似度降序
        """
        import faiss

        if not self._loaded:
            if not self.load_index():
                return []

        qv = query_vector.reshape(1, -1).astype(np.float32)
        faiss.normalize_L2(qv)

        # 如果有候选集过滤，需要多检索一些再过滤
        search_k = top_k * 5 if candidate_ids else top_k
        self._index.nprobe = 16
        scores, indices = self._index.search(qv, min(search_k, len(self._id_list)))

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._id_list):
                continue
            doc_id = self._id_list[idx]
            if candidate_ids and doc_id not in candidate_ids:
                continue
            results.append((doc_id, float(score)))
            if len(results) >= top_k:
                break
        return results

    @property
    def is_loaded(self) -> bool:
        return self._loaded
