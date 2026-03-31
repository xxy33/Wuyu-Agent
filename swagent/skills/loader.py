"""
Skill 加载器 - 从 Markdown 文件加载 Skill 定义

参考 Claude Code 的 Skill 系统设计，使用 Markdown + YAML frontmatter 格式。

Skill 文件格式 (*.md):
---
name: 排放计算报告
description: 根据废物类型和处理方式计算碳排放并生成报告
domain: waste
tools:
  - emission_calculator
  - visualizer
  - file_handler
parameters:
  - name: waste_type
    type: string
    required: true
    description: 废物类型
  - name: treatment_method
    type: string
    required: true
    description: 处理方式
hooks:
  PreToolUse:
    - matcher: "emission_calculator"
      type: callback
      description: "验证排放参数合理性"
---

## 执行步骤

1. 根据用户指定的废物类型，从知识库获取成分数据
2. 调用 emission_calculator 计算各处理方式的排放量
3. 调用 visualizer 生成对比图表
4. 生成标准格式报告

## Prompt

你是固废碳排放分析专家。请按以下步骤执行...
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from swagent.utils.logger import get_logger

logger = get_logger(__name__)

# ---------- Frontmatter 分隔正则 ----------
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n(.*)",
    re.DOTALL,
)


# ---------- 数据类 ----------

@dataclass
class SkillParameter:
    """Skill 参数定义"""

    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    default: Optional[Any] = None
    enum: Optional[List[str]] = None


@dataclass
class SkillDefinition:
    """完整的 Skill 定义"""

    name: str
    description: str
    domain: str = "general"
    tools: List[str] = field(default_factory=list)
    parameters: List[SkillParameter] = field(default_factory=list)
    hooks: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    steps: List[str] = field(default_factory=list)
    prompt_template: str = ""
    file_path: Optional[str] = None

    # ------ 便捷方法 ------

    def required_parameters(self) -> List[SkillParameter]:
        """返回所有必填参数"""
        return [p for p in self.parameters if p.required]

    def validate_params(self, params: Dict[str, Any]) -> List[str]:
        """
        校验传入参数，返回错误列表（空列表表示通过）

        Args:
            params: 用户传入的参数字典

        Returns:
            错误信息列表
        """
        errors: List[str] = []
        for p in self.parameters:
            if p.required and p.name not in params:
                errors.append(f"缺少必填参数: {p.name} ({p.description})")
            if p.enum and p.name in params and params[p.name] not in p.enum:
                errors.append(
                    f"参数 {p.name} 的值 '{params[p.name]}' "
                    f"不在允许范围 {p.enum} 中"
                )
        return errors


# ---------- 加载器 ----------

class SkillLoader:
    """
    Skill 加载器

    从 Markdown 文件（含 YAML frontmatter）加载 Skill 定义。
    """

    # 必须在 frontmatter 中出现的字段
    REQUIRED_FIELDS = {"name", "description"}

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @classmethod
    def load_from_file(cls, path: str | Path) -> SkillDefinition:
        """
        从单个 Markdown 文件加载 Skill 定义

        Args:
            path: .md 文件路径

        Returns:
            SkillDefinition 实例

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 格式或必填字段缺失
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Skill 文件不存在: {path}")

        content = path.read_text(encoding="utf-8")
        meta, body = cls._parse_frontmatter(content, str(path))

        # 校验必填字段
        missing = cls.REQUIRED_FIELDS - set(meta.keys())
        if missing:
            raise ValueError(
                f"Skill 文件 {path} 缺少必填字段: {', '.join(sorted(missing))}"
            )

        # 构建参数列表
        parameters = cls._parse_parameters(meta.get("parameters", []))

        # 从 body 中提取步骤和 prompt
        steps = cls._extract_steps(body)
        prompt_template = cls._extract_prompt(body)

        skill = SkillDefinition(
            name=meta["name"],
            description=meta.get("description", ""),
            domain=meta.get("domain", "general"),
            tools=meta.get("tools", []),
            parameters=parameters,
            hooks=meta.get("hooks", {}),
            steps=steps,
            prompt_template=prompt_template,
            file_path=str(path.resolve()),
        )

        logger.info(f"已加载 Skill: {skill.name} (domain={skill.domain})")
        return skill

    @classmethod
    def load_from_directory(cls, dir_path: str | Path) -> List[SkillDefinition]:
        """
        从目录中加载所有 .md Skill 文件

        Args:
            dir_path: 目录路径

        Returns:
            SkillDefinition 列表
        """
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            logger.warning(f"Skill 目录不存在: {dir_path}")
            return []

        skills: List[SkillDefinition] = []
        for md_file in sorted(dir_path.glob("*.md")):
            try:
                skills.append(cls.load_from_file(md_file))
            except Exception as exc:
                logger.warning(f"跳过无效 Skill 文件 {md_file}: {exc}")

        logger.info(f"从 {dir_path} 加载了 {len(skills)} 个 Skill")
        return skills

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_frontmatter(content: str, source: str = "") -> tuple[dict, str]:
        """解析 YAML frontmatter 和 Markdown body"""
        match = _FRONTMATTER_RE.match(content)
        if not match:
            raise ValueError(
                f"Skill 文件格式错误（缺少 YAML frontmatter）: {source}"
            )

        yaml_str, body = match.group(1), match.group(2)
        try:
            meta = yaml.safe_load(yaml_str) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"YAML 解析错误 ({source}): {exc}") from exc

        return meta, body.strip()

    @staticmethod
    def _parse_parameters(raw_params: list) -> List[SkillParameter]:
        """将 frontmatter 中的参数列表转为 SkillParameter 对象"""
        params: List[SkillParameter] = []
        for item in raw_params:
            if not isinstance(item, dict):
                continue
            params.append(
                SkillParameter(
                    name=item.get("name", ""),
                    type=item.get("type", "string"),
                    required=item.get("required", False),
                    description=item.get("description", ""),
                    default=item.get("default"),
                    enum=item.get("enum"),
                )
            )
        return params

    @staticmethod
    def _extract_steps(body: str) -> List[str]:
        """从 Markdown body 中提取「执行步骤」章节的有序列表"""
        steps: List[str] = []
        in_steps = False
        for line in body.splitlines():
            stripped = line.strip()
            # 匹配 ## 执行步骤 / ## Steps 等标题
            if re.match(r"^#{1,3}\s*(执行步骤|Steps)", stripped, re.IGNORECASE):
                in_steps = True
                continue
            # 遇到下一个标题则终止
            if in_steps and re.match(r"^#{1,3}\s+", stripped):
                break
            if in_steps:
                # 匹配有序列表 "1. xxx"
                m = re.match(r"^\d+\.\s+(.+)", stripped)
                if m:
                    steps.append(m.group(1))
        return steps

    @staticmethod
    def _extract_prompt(body: str) -> str:
        """从 Markdown body 中提取 ## Prompt 章节的内容"""
        lines: List[str] = []
        in_prompt = False
        for line in body.splitlines():
            stripped = line.strip()
            if re.match(r"^#{1,3}\s*Prompt", stripped, re.IGNORECASE):
                in_prompt = True
                continue
            if in_prompt and re.match(r"^#{1,3}\s+", stripped):
                break
            if in_prompt:
                lines.append(line)

        return "\n".join(lines).strip()
