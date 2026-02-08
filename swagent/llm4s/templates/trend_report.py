"""
任务二输出模板：研究趋势推演报告
"""
import json
from datetime import datetime
from typing import Dict, Any, List


def render_trend_report(state: Dict[str, Any]) -> str:
    """将任务二的最终状态渲染为文本报告"""
    topic = state.get("topic", "")
    countries = state.get("countries", [])
    timeline = state.get("timeline", [])
    current = state.get("current_status", {})
    trends = state.get("tech_trends", [])
    policies = state.get("country_policies", {})
    debate = state.get("debate_history", [])
    conflicts = state.get("conflicts", {})
    report = state.get("final_report", {})

    lines = [
        "=" * 55,
        "              研究趋势推演报告",
        "=" * 55,
        "",
        f"  分析主题: {topic}",
        f"  分析范围: {', '.join(countries)}",
        f"  时间跨度: 2000年 - 2025年 -> 展望2030年",
        "",
    ]

    # 第一部分：技术演变回顾
    lines.append("=" * 55)
    lines.append("第一部分：技术演变回顾")
    lines.append("=" * 55)
    lines.append("")
    lines.append("  时间线")
    for t in timeline:
        period = t.get("period", "")
        summary = t.get("summary", "")
        count = t.get("paper_count", 0)
        lines.append(f"  +-- {period} ({count}篇): {summary}")
    lines.append("")

    # 第二部分：当前研究格局
    lines.append("=" * 55)
    lines.append("第二部分：当前研究格局")
    lines.append("=" * 55)
    lines.append("")

    hotspots = current.get("hotspots", [])
    if hotspots:
        lines.append("  当前热点")
        for h in hotspots:
            if isinstance(h, dict):
                lines.append(f"  +-- {h.get('topic', '')}: {h.get('description', '')}")
        lines.append("")

    bottlenecks = current.get("bottlenecks", [])
    if bottlenecks:
        lines.append("  技术瓶颈")
        for b in bottlenecks:
            if isinstance(b, dict):
                lines.append(f"  +-- {b.get('issue', '')}: {b.get('why_hard', '')}")
        lines.append("")

    controversies = current.get("controversies", [])
    if controversies:
        lines.append("  学术争议")
        for c in controversies:
            if isinstance(c, dict):
                lines.append(f"  +-- {c.get('topic', '')}: {c.get('pro', '')} vs {c.get('con', '')}")
        lines.append("")

    # 第三部分：未来技术趋势预测
    lines.append("=" * 55)
    lines.append("第三部分：未来技术趋势预测 (2025-2030)")
    lines.append("=" * 55)
    lines.append("")

    for i, t in enumerate(trends, 1):
        if isinstance(t, dict):
            conf = t.get("confidence", 0)
            stars = int(conf * 5) if isinstance(conf, (int, float)) else 3
            lines.append(f"  趋势{i}: {t.get('name', '')}")
            lines.append(f"  +-- 置信度: {'*' * stars} / {conf}")
            lines.append(f"  +-- 描述: {t.get('description', '')}")
            lines.append(f"  +-- 推理逻辑: {t.get('reasoning', '')}")
            lines.append(f"  +-- 预计爆发时间: {t.get('time_window', '')}")
            evidence = t.get("evidence", [])
            if evidence:
                lines.append(f"  +-- 支撑证据: {'; '.join(str(e) for e in evidence[:3])}")
            lines.append("")

    # 第四部分：全球政策格局与博弈分析
    lines.append("=" * 55)
    lines.append("第四部分：全球政策格局与博弈分析")
    lines.append("=" * 55)
    lines.append("")

    lines.append("  各方政策概览")
    lines.append("")
    for role, policy in policies.items():
        if isinstance(policy, dict):
            lines.append(f"  {role}:")
            lines.append(f"  +-- 核心目标: {policy.get('core_interests', '')}")
            dirs = policy.get("priority_directions", [])
            if dirs:
                lines.append(f"  +-- 重点方向: {', '.join(str(d) for d in dirs[:3])}")
            lines.append(f"  +-- 立场: {policy.get('stance_summary', '')}")
            lines.append("")

    # 利益冲突分析
    conflict_list = conflicts.get("conflicts", [])
    if conflict_list:
        lines.append("  利益冲突分析")
        lines.append("")
        for i, c in enumerate(conflict_list, 1):
            if isinstance(c, dict):
                lines.append(f"  冲突{i}: {c.get('description', '')[:80]}")
                lines.append(f"  +-- 类型: {c.get('type', '')}")
                parties = c.get("parties", [])
                lines.append(f"  +-- 涉及方: {', '.join(str(p) for p in parties)}")
                lines.append(f"  +-- 严重程度: {c.get('severity', '')}")
                lines.append(f"  +-- 研究启示: {c.get('research_implication', '')}")
                lines.append("")

    # 第五部分：重点研究方向推荐
    lines.append("=" * 55)
    lines.append("第五部分：重点研究方向推荐")
    lines.append("=" * 55)
    lines.append("")

    recommended = report.get("recommended_directions", [])
    for i, d in enumerate(recommended, 1):
        if isinstance(d, dict):
            priority = d.get("priority", 0)
            lines.append(f"  推荐方向{i}: {d.get('name', '')}")
            lines.append(f"  +-- 综合优先级: {'*' * int(priority)}")
            lines.append(f"  +-- 技术支撑: {d.get('tech_support', '')}")
            lines.append(f"  +-- 政策支撑: {d.get('policy_support', '')}")
            lines.append(f"  +-- 全球价值: {d.get('global_value', '')}")
            lines.append(f"  +-- 推荐理由: {d.get('rationale', '')}")
            lines.append(f"  +-- 建议切入点: {d.get('entry_point', '')}")
            refs = d.get("key_references", [])
            if refs:
                lines.append(f"  +-- 关键文献: {'; '.join(str(r) for r in refs[:3])}")
            lines.append("")

    # 风险提醒
    warnings = report.get("risk_warnings", [])
    if warnings:
        lines.append("  风险提醒")
        for w in warnings:
            if isinstance(w, dict):
                lines.append(f"  +-- {w.get('direction', '')}: {w.get('risk', '')} ({w.get('reason', '')})")
        lines.append("")

    # 报告元信息
    lines.append("-" * 55)
    lines.append(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"涉及国家/地区: {', '.join(countries)}")
    lines.append(f"时间线时段数: {len(timeline)}")
    lines.append(f"博弈议题数: {len(debate)}")
    lines.append(f"技术趋势预测数: {len(trends)}")
    lines.append("-" * 55)

    return "\n".join(lines)
