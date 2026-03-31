"""
Prompt 缓存优化 - 静态/动态分区
参考 Claude Code prompts.ts 的 SYSTEM_PROMPT_DYNAMIC_BOUNDARY 设计

原理:
- 静态部分 (身份、规则、领域知识) 全局可缓存 → API prompt cache 命中
- 动态部分 (环境信息、当前工具、会话状态) 每次变化
- 分区标记使 API 只需重新处理动态部分

CacheSafeParams:
- 父子 Agent 共享相同的静态 prompt → 子 Agent 复用父 Agent 的 cache
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from collections import OrderedDict


DYNAMIC_BOUNDARY = "\n\n# ---- 以下为动态内容 (每会话变化) ----\n\n"


@dataclass
class CacheSafeParams:
    """
    缓存安全参数 - 父子 Agent 共享以复用 prompt cache

    父 Agent 每轮结束后保存此结构，子 Agent 复用以命中缓存。
    """
    system_prompt: str
    user_context: Dict[str, str] = field(default_factory=dict)
    system_context: Dict[str, str] = field(default_factory=dict)
    tools_signature: str = ""  # 工具列表的哈希签名
    model: str = ""


class SystemPromptBuilder:
    """
    系统提示词构建器

    将提示词分为静态和动态两部分，中间插入边界标记。
    静态部分在多轮对话中不变，可被 API prompt cache 缓存。

    用法:
        builder = SystemPromptBuilder()
        builder.add_static("identity", "你是固废管理专家...")
        builder.add_static("domain", waste_knowledge_text)
        builder.add_dynamic("tools", "当前可用工具: ...")
        builder.add_dynamic("env", "当前时间: 2026-03-31")
        prompt = builder.build()
    """

    def __init__(self):
        self._static: OrderedDict[str, str] = OrderedDict()
        self._dynamic: OrderedDict[str, str] = OrderedDict()

    def add_static(self, name: str, content: str) -> 'SystemPromptBuilder':
        """
        添加静态区段 (全局可缓存)

        适用于: 身份定义、角色规则、领域知识、标准法规等
        """
        self._static[name] = content
        return self

    def add_dynamic(self, name: str, content: str) -> 'SystemPromptBuilder':
        """
        添加动态区段 (每会话变化)

        适用于: 环境信息、当前工具列表、会话状态、加载的 Skill 等
        """
        self._dynamic[name] = content
        return self

    def remove_section(self, name: str) -> 'SystemPromptBuilder':
        """移除区段"""
        self._static.pop(name, None)
        self._dynamic.pop(name, None)
        return self

    def build(self) -> str:
        """
        构建最终的系统提示词

        Returns:
            静态部分 + 边界标记 + 动态部分
        """
        parts = []

        # 静态部分
        for name, content in self._static.items():
            if content.strip():
                parts.append(content.strip())

        static_text = "\n\n".join(parts)

        # 动态部分
        dynamic_parts = []
        for name, content in self._dynamic.items():
            if content.strip():
                dynamic_parts.append(content.strip())

        dynamic_text = "\n\n".join(dynamic_parts)

        if dynamic_text:
            return static_text + DYNAMIC_BOUNDARY + dynamic_text
        return static_text

    def build_static_only(self) -> str:
        """仅构建静态部分 (用于 cache 签名)"""
        parts = [c.strip() for c in self._static.values() if c.strip()]
        return "\n\n".join(parts)

    def to_cache_safe_params(self, model: str = "") -> CacheSafeParams:
        """导出为 CacheSafeParams"""
        import hashlib
        static = self.build_static_only()
        return CacheSafeParams(
            system_prompt=self.build(),
            tools_signature=hashlib.md5(static.encode()).hexdigest()[:8],
            model=model,
        )

    @property
    def section_names(self) -> Dict[str, List[str]]:
        """查看当前区段"""
        return {
            "static": list(self._static.keys()),
            "dynamic": list(self._dynamic.keys()),
        }
