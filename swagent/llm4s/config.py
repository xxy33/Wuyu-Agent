"""
LLM4S 全局配置
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class LLM4SConfig:
    """LLM4S系统配置"""

    # LLM API配置
    api_key: str = "sk-xxxx"
    base_url: str = "https://www.xxxx.cn/v1"
    llm_model: str = "gpt-5.2"
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024

    # 本地嵌入模型路径（用于构建索引）
    local_embedding_model_path: str = "/root/abstract_agent/embedding/bge-m3"

    # Tavily配置
    tavily_api_key: str = "tvly-dev-xxxx"

    # 数据路径
    kg_path: str = "/root/abstract_agent/data/knowledge/New_Output_Remote.jsonl"
    papers_path: str = "/root/abstract_agent/data/data.jsonl"

    # 向量索引路径
    faiss_index_path: str = "/root/Wuyu-Agent-main/swagent/llm4s/data/faiss_index"
    id_map_path: str = "/root/Wuyu-Agent-main/swagent/llm4s/data/id_map.json"

    # 检索参数
    kg_top_k: int = 50
    vector_top_k: int = 15
    tavily_max_results: int = 5

    # LLM参数
    temperature: float = 0.7
    max_tokens: int = 4096
    low_temperature: float = 0.3  # 用于信息提取等简单任务

    # 任务一参数
    max_revision_rounds: int = 3
    review_pass_threshold: float = 70.0

    # 任务二参数
    time_periods: List[str] = field(default_factory=lambda: [
        "2000-2005", "2005-2010", "2010-2015", "2015-2020", "2020-2025"
    ])
    countries: List[str] = field(default_factory=lambda: [
        "UNEP", "China", "EU", "USA", "Southeast Asia/Africa"
    ])
    debate_rounds: int = 2
    debate_topics_count: int = 4
    timeline_top_k: int = 25

    # 嵌入批量大小
    embedding_batch_size: int = 64
