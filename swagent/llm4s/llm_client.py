"""
LLM客户端 - 封装OpenAI兼容接口，供LLM4S各模块使用
"""
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional

from openai import AsyncOpenAI

from swagent.llm4s.config import LLM4SConfig

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM调用客户端"""

    def __init__(self, config: Optional[LLM4SConfig] = None):
        self.config = config or LLM4SConfig()
        self.client = AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=120,
            max_retries=3,
        )

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        response_format: Optional[Dict] = None,
    ) -> str:
        """调用LLM并返回文本内容"""
        params: Dict[str, Any] = {
            "model": model or self.config.llm_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        if response_format:
            params["response_format"] = response_format

        response = await self.client.chat.completions.create(**params)
        return response.choices[0].message.content or ""

    async def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """调用LLM并解析JSON输出"""
        raw = await self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        # 尝试从返回文本中提取JSON
        raw = raw.strip()
        if raw.startswith("```"):
            # 去除markdown代码块
            lines = raw.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw = "\n".join(lines)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("LLM返回的JSON解析失败，尝试修复...")
            # 尝试找到第一个{和最后一个}
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                return json.loads(raw[start:end + 1])
            raise


class EmbeddingClient:
    """嵌入模型客户端"""

    def __init__(self, config: Optional[LLM4SConfig] = None):
        self.config = config or LLM4SConfig()
        self.client = AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=60,
            max_retries=3,
        )

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """批量获取文本嵌入向量"""
        if not texts:
            return []
        results = []
        for i in range(0, len(texts), self.config.embedding_batch_size):
            batch = texts[i:i + self.config.embedding_batch_size]
            response = await self.client.embeddings.create(
                model=self.config.embedding_model,
                input=batch,
            )
            results.extend([d.embedding for d in response.data])
        return results

    async def embed_single(self, text: str) -> List[float]:
        """获取单条文本嵌入向量"""
        vecs = await self.embed([text])
        return vecs[0]


class LocalEmbeddingClient:
    """本地嵌入模型客户端 - 使用sentence-transformers加载本地BGE-M3模型，用于构建索引"""

    def __init__(self, model_path: str, batch_size: int = 64):
        from sentence_transformers import SentenceTransformer
        logger.info(f"加载本地嵌入模型: {model_path}")
        self.model = SentenceTransformer(model_path)
        self.batch_size = batch_size
        logger.info(f"本地嵌入模型加载完成，维度: {self.model.get_sentence_embedding_dimension()}")

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """同步批量嵌入（本地推理，无需async）"""
        if not texts:
            return []
        embeddings = self.model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return embeddings.tolist()
