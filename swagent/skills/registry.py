"""
Skill 注册中心与执行器

管理 Skill 的发现、注册和执行。
支持按领域过滤、模板渲染和工具依赖查询。
"""

import string
from pathlib import Path
from typing import Any, Dict, List, Optional

from swagent.skills.loader import SkillDefinition, SkillLoader
from swagent.utils.logger import get_logger

logger = get_logger(__name__)


class SkillRegistry:
    """
    Skill 注册中心

    职责:
    - 管理 Skill 的注册与注销
    - 支持从目录自动发现 Skill 文件
    - 按领域(domain)查询
    - 渲染 Prompt 模板
    - 生成供 LLM 选择 Skill 的描述文本
    """

    def __init__(self) -> None:
        """初始化注册中心"""
        self._skills: Dict[str, SkillDefinition] = {}
        logger.info("Skill 注册中心初始化完成")

    # ------------------------------------------------------------------
    # 注册 / 注销
    # ------------------------------------------------------------------

    def register(self, skill: SkillDefinition) -> None:
        """
        注册一个 Skill

        Args:
            skill: SkillDefinition 实例

        Raises:
            ValueError: 名称已被占用
        """
        if skill.name in self._skills:
            raise ValueError(f"Skill '{skill.name}' 已注册")
        self._skills[skill.name] = skill
        logger.info(f"注册 Skill: {skill.name}")

    def unregister(self, name: str) -> None:
        """
        注销一个 Skill

        Args:
            name: Skill 名称

        Raises:
            KeyError: 名称不存在
        """
        if name not in self._skills:
            raise KeyError(f"Skill '{name}' 未注册")
        del self._skills[name]
        logger.info(f"注销 Skill: {name}")

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_skill(self, name: str) -> Optional[SkillDefinition]:
        """
        获取指定名称的 Skill

        Args:
            name: Skill 名称

        Returns:
            SkillDefinition 或 None
        """
        return self._skills.get(name)

    def list_skills(self, domain: Optional[str] = None) -> List[SkillDefinition]:
        """
        列出所有已注册的 Skill，可按领域过滤

        Args:
            domain: 领域名称，为 None 时返回全部

        Returns:
            SkillDefinition 列表
        """
        skills = list(self._skills.values())
        if domain is not None:
            skills = [s for s in skills if s.domain == domain]
        return skills

    # ------------------------------------------------------------------
    # 自动发现
    # ------------------------------------------------------------------

    def discover_skills(self, directories: List[str | Path]) -> int:
        """
        扫描多个目录，加载并注册所有发现的 Skill 文件

        Args:
            directories: 要扫描的目录路径列表

        Returns:
            新注册的 Skill 数量
        """
        count = 0
        for dir_path in directories:
            skills = SkillLoader.load_from_directory(dir_path)
            for skill in skills:
                if skill.name in self._skills:
                    logger.debug(f"Skill '{skill.name}' 已存在，跳过")
                    continue
                self.register(skill)
                count += 1
        logger.info(f"自动发现并注册了 {count} 个新 Skill")
        return count

    # ------------------------------------------------------------------
    # 渲染与工具
    # ------------------------------------------------------------------

    def render_prompt(self, skill_name: str, **params: Any) -> str:
        """
        渲染 Skill 的 Prompt 模板，将参数填入占位符

        使用 Python 标准 string.Template 的 $variable 语法。
        未提供的参数保留原占位符。

        Args:
            skill_name: Skill 名称
            **params: 模板参数

        Returns:
            渲染后的 Prompt 字符串

        Raises:
            KeyError: Skill 不存在
        """
        skill = self._skills.get(skill_name)
        if skill is None:
            raise KeyError(f"Skill '{skill_name}' 未注册")

        template = string.Template(skill.prompt_template)
        return template.safe_substitute(**params)

    def get_required_tools(self, skill_name: str) -> List[str]:
        """
        获取某个 Skill 依赖的工具列表

        Args:
            skill_name: Skill 名称

        Returns:
            工具名称列表

        Raises:
            KeyError: Skill 不存在
        """
        skill = self._skills.get(skill_name)
        if skill is None:
            raise KeyError(f"Skill '{skill_name}' 未注册")
        return list(skill.tools)

    # ------------------------------------------------------------------
    # LLM 描述输出
    # ------------------------------------------------------------------

    def to_skill_descriptions(self) -> str:
        """
        生成面向 LLM 的 Skill 描述文本，供模型选择合适的 Skill

        Returns:
            格式化的 Skill 列表描述
        """
        if not self._skills:
            return "当前没有可用的 Skill。"

        lines: List[str] = ["可用的 Skill 列表:\n"]
        for idx, skill in enumerate(self._skills.values(), 1):
            param_strs: List[str] = []
            for p in skill.parameters:
                req = "必填" if p.required else "可选"
                param_strs.append(f"    - {p.name} ({p.type}, {req}): {p.description}")

            params_block = "\n".join(param_strs) if param_strs else "    (无参数)"
            lines.append(
                f"{idx}. {skill.name}\n"
                f"   描述: {skill.description}\n"
                f"   领域: {skill.domain}\n"
                f"   参数:\n{params_block}"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 魔术方法
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __repr__(self) -> str:
        return f"SkillRegistry(skills={len(self._skills)})"
