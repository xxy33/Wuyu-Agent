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
            timeout=600,  # 增加到10分钟，应对超长prompt
            max_retries=2,  # 减少重试次数
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
        logger.info(f"[chat_json] 开始调用 LLM，temperature={temperature}, max_tokens={max_tokens}")
        logger.debug(f"[chat_json] 消息数量: {len(messages)}")

        raw = await self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )

        logger.info(f"[chat_json] LLM 返回成功，响应长度: {len(raw)} 字符")
        logger.debug(f"[chat_json] 原始响应前500字符: {raw[:500]}")

        # 如果响应很短或看起来有问题，输出完整内容
        if len(raw) < 100 or not raw.strip().startswith("{"):
            logger.warning(f"[chat_json] 检测到异常响应，完整内容:\n{raw}")

        # 清理文本
        raw = raw.strip()

        # 去除markdown代码块
        if raw.startswith("```"):
            logger.debug("[chat_json] 检测到 markdown 代码块，正在清理...")
            lines = raw.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw = "\n".join(lines).strip()

        # 检查是否以{开头，如果不是则添加
        if not raw.startswith("{"):
            # 检查是否以字段名开头（如 "challenges": ...）
            if raw.startswith('"'):
                logger.warning("[chat_json] 检测到以字段名开头的JSON，添加开头的 {")
                raw = "{" + raw
            else:
                # 尝试找到第一个{并从那里开始
                first_brace = raw.find("{")
                if first_brace > 0:
                    removed = raw[:first_brace]
                    logger.debug(f"[chat_json] 移除开头的非JSON字符: {repr(removed[:100])}")
                    raw = raw[first_brace:]
                elif first_brace == -1:
                    logger.warning("[chat_json] 未找到 {，尝试添加...")
                    raw = "{" + raw

        # 确保有结尾的}
        if not raw.endswith("}"):
            # 尝试找到最后一个}
            last_brace = raw.rfind("}")
            if last_brace != -1 and last_brace < len(raw) - 1:
                removed_end = raw[last_brace + 1:]
                logger.debug(f"[chat_json] 移除结尾的非JSON字符: {repr(removed_end[:100])}")
                raw = raw[:last_brace + 1]
            else:
                logger.warning("[chat_json] 未找到结尾的 }，尝试添加...")
                raw = raw + "}"

        try:
            result = json.loads(raw)
            logger.info(f"[chat_json] JSON 解析成功，包含 {len(result)} 个字段")
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"[chat_json] JSON 解析失败: {e}，尝试修复...")
            logger.debug(f"[chat_json] 失败的 JSON 前500字符: {raw[:500]}")

            # 策略1: 提取第一个{到最后一个}
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                extracted = raw[start:end + 1]
                logger.debug(f"[chat_json] 提取的 JSON 片段长度: {len(extracted)}")
                try:
                    result = json.loads(extracted)
                    logger.info(f"[chat_json] JSON 修复成功（策略1）")
                    return result
                except json.JSONDecodeError as e2:
                    logger.debug(f"[chat_json] 策略1失败: {e2}")

            # 策略2: 逐行清理，移除非JSON行
            lines = raw.split('\n')
            cleaned_lines = []
            in_json = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith('{'):
                    in_json = True
                if in_json:
                    cleaned_lines.append(line)
                if stripped.endswith('}') and in_json:
                    break

            if cleaned_lines:
                cleaned = '\n'.join(cleaned_lines)
                logger.debug(f"[chat_json] 逐行清理后的 JSON 长度: {len(cleaned)}")
                try:
                    result = json.loads(cleaned)
                    logger.info(f"[chat_json] JSON 修复成功（策略2）")
                    return result
                except json.JSONDecodeError as e3:
                    logger.debug(f"[chat_json] 策略2失败: {e3}")

            # 所有策略都失败，记录详细信息并抛出错误
            logger.error(f"[chat_json] 所有修复策略都失败")
            logger.error(f"[chat_json] 完整原始内容:\n{raw}")
            raise ValueError(f"JSON解析失败: {e}, 原始内容: {raw[:500]}")


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
