"""
任务一输出模板：实验方案设计报告
"""
import json
from datetime import datetime
from typing import Dict, Any


def render_experiment_report(state: Dict[str, Any]) -> str:
    """将任务一的最终状态渲染为文本报告"""
    scheme = state.get("final_scheme", state.get("draft_scheme", {}))
    review = scheme.get("_review", {})
    meta = scheme.get("_meta", {})
    scores = review.get("scores", {})

    lines = [
        "=" * 55,
        "                 实验方案设计报告",
        "=" * 55,
        "",
    ]

    # 研究问题
    lines.append(f"  研究问题")
    lines.append(f"  {state.get('research_question', '')}")
    lines.append("")

    # 研究背景
    lines.append(f"  研究背景")
    lines.append(f"  {scheme.get('background', '')}")
    lines.append("")

    # 研究目标
    lines.append(f"  研究目标")
    lines.append(f"  {scheme.get('objectives', '')}")
    lines.append("")

    # 实验材料
    materials = scheme.get("materials", {})
    lines.append("  实验材料")
    raw = materials.get("raw_materials", [])
    if raw:
        lines.append("  +-- 原料:")
        for m in raw:
            if isinstance(m, dict):
                lines.append(f"  |   - {m.get('name', '')}: {m.get('spec', '')} (来源: {m.get('source', '')})")
            else:
                lines.append(f"  |   - {m}")
    reagents = materials.get("reagents", [])
    if reagents:
        lines.append("  +-- 试剂:")
        for r in reagents:
            if isinstance(r, dict):
                lines.append(f"  |   - {r.get('name', '')} (纯度: {r.get('purity', '')})")
            else:
                lines.append(f"  |   - {r}")
    equip = materials.get("equipment", [])
    if equip:
        lines.append("  +-- 主要设备:")
        for e in equip:
            if isinstance(e, dict):
                lines.append(f"      - {e.get('name', '')} ({e.get('model_suggestion', '')})")
            else:
                lines.append(f"      - {e}")
    lines.append("")

    # 实验方法
    methods = scheme.get("methods", {})
    lines.append("  实验方法")

    pre = methods.get("pretreatment", {})
    if pre:
        lines.append("  +-- 1. 原料预处理")
        lines.append(f"  |   方法: {pre.get('method', '')}")
        params = pre.get("parameters", {})
        if params:
            for k, v in params.items():
                lines.append(f"  |   {k}: {v}")

    exp = methods.get("experimental_design", {})
    if exp:
        lines.append("  +-- 2. 实验设计")
        lines.append(f"  |   实验类型: {exp.get('type', '')}")
        ivars = exp.get("independent_variables", [])
        if ivars:
            lines.append(f"  |   自变量: {', '.join(str(v) for v in ivars)}")
        conds = exp.get("conditions", {})
        if conds:
            lines.append("  |   实验条件:")
            for k, v in conds.items():
                lines.append(f"  |     {k}: {v}")
        lines.append(f"  |   对照组: {exp.get('control_group', '')}")
        lines.append(f"  |   重复次数: {exp.get('replicates', 3)}")

    chars = methods.get("characterization", [])
    if chars:
        lines.append("  +-- 3. 表征与分析")
        for c in chars:
            if isinstance(c, dict):
                lines.append(f"      - {c.get('method', '')} -- 测量{c.get('target_indicator', '')} -- 使用{c.get('instrument', '')}")
            else:
                lines.append(f"      - {c}")
    lines.append("")

    # 预期结果
    lines.append("  预期结果")
    lines.append(f"  {scheme.get('expected_results', '')}")
    lines.append("")

    # 风险提示
    risks = scheme.get("risks", [])
    if risks:
        lines.append("  风险提示与应对")
        for r in risks:
            if isinstance(r, dict):
                lines.append(f"  +-- {r.get('risk', '')} -> 应对: {r.get('mitigation', '')}")
            else:
                lines.append(f"  +-- {r}")
        lines.append("")

    # 评审结果
    lines.append("  评审结果")
    for dim_key, dim_name in [
        ("scientific_rigor", "科学性"),
        ("completeness", "完整性"),
        ("feasibility", "可行性"),
        ("innovation", "创新性"),
    ]:
        dim = scores.get(dim_key, {})
        s = dim.get("score", "N/A") if isinstance(dim, dict) else dim
        lines.append(f"  +-- {dim_name}: {s}/25")
    lines.append(f"  +-- 总分: {review.get('total_score', 'N/A')}/100")
    comments = review.get("comments", [])
    if comments:
        lines.append(f"  +-- 评审意见: {'; '.join(comments[:3])}")
    lines.append("")

    # 参考文献
    refs = scheme.get("references", [])
    if refs:
        lines.append("  参考文献")
        for i, ref in enumerate(refs, 1):
            lines.append(f"  [{i}] {ref}")
        lines.append("")

    # 元信息
    lines.append("-" * 55)
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"修订次数: {meta.get('revision_count', 0)}")
    lines.append(f"检索论文数: {meta.get('papers_count', 0)}")
    lines.append("-" * 55)

    return "\n".join(lines)
