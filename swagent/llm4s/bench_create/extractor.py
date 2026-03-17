"""
bench_create 核心提取逻辑

对 KG JSONL 进行全量扫描，按领域配置进行统计：
- 按 Waste_Object / Technology 关键词匹配
- 按年份分组计数
- 收集匹配到的条目 ID
- 计算增长率与数据充足性标记
"""
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def load_kg_entries(kg_path: str) -> List[Dict[str, Any]]:
    """加载 KG JSONL 全量数据到内存"""
    entries: List[Dict[str, Any]] = []
    with open(kg_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    logger.info(f"KG 加载完成: {len(entries)} 条")
    return entries


def _get_year(entry: Dict[str, Any]) -> Optional[int]:
    """安全提取年份"""
    year_str = entry.get("Meta_Info", {}).get("Year", "")
    if not year_str:
        return None
    try:
        return int(year_str)
    except (ValueError, TypeError):
        return None


def _match_keywords(text: str, keywords: List[str]) -> bool:
    """判断 text 中是否包含任一关键词（大小写不敏感）"""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def scan_entries(
    entries: List[Dict[str, Any]],
    waste_keywords: List[str],
    tech_keywords: List[str],
    year_range: Tuple[int, int],
) -> Dict[str, Any]:
    """
    全量扫描 KG 条目，匹配 Waste_Object 或 Technology 关键词并限定年份范围。

    匹配逻辑（与现有 KGRetriever 一致）：
    - 如果 waste_keywords 非空，Waste_Object 字段必须命中至少一个
    - 如果 tech_keywords 非空，Technology 字段必须命中至少一个
    - 两者都提供时取 AND 关系

    返回:
        {
            "total_count": int,
            "yearly_distribution": {year_str: count},
            "matched_ids": [id_str, ...],
        }
    """
    yearly: Dict[int, int] = defaultdict(int)
    matched_ids: List[str] = []

    for entry in entries:
        year = _get_year(entry)
        if year is None:
            continue
        if year < year_range[0] or year > year_range[1]:
            continue

        proc = entry.get("Process_Event", {})

        # Waste_Object 匹配
        if waste_keywords:
            waste_obj = (proc.get("Waste_Object") or "").lower()
            if not any(kw.lower() in waste_obj for kw in waste_keywords):
                continue

        # Technology 匹配
        if tech_keywords:
            tech = (proc.get("Technology") or "").lower()
            if not any(kw.lower() in tech for kw in tech_keywords):
                continue

        yearly[year] += 1
        matched_ids.append(str(entry.get("id", "")))

    # 补全年份范围内缺失的年份为 0
    yearly_dist = {}
    for y in range(year_range[0], year_range[1] + 1):
        yearly_dist[str(y)] = yearly.get(y, 0)

    return {
        "total_count": sum(yearly_dist.values()),
        "yearly_distribution": yearly_dist,
        "matched_ids": matched_ids,
    }


def scan_entries_waste_or_tech(
    entries: List[Dict[str, Any]],
    waste_keywords: List[str],
    tech_keywords: List[str],
    year_range: Tuple[int, int],
) -> Dict[str, Any]:
    """
    宽松匹配版本：Waste_Object OR Technology 命中任一即可。
    用于领域整体论文计数（不区分 pre/post 技术方向）。
    """
    yearly: Dict[int, int] = defaultdict(int)
    matched_ids: List[str] = []

    for entry in entries:
        year = _get_year(entry)
        if year is None:
            continue
        if year < year_range[0] or year > year_range[1]:
            continue

        proc = entry.get("Process_Event", {})
        waste_obj = (proc.get("Waste_Object") or "").lower()
        tech = (proc.get("Technology") or "").lower()

        # OR 逻辑：废物类型或技术命中任一
        waste_hit = any(kw.lower() in waste_obj for kw in waste_keywords) if waste_keywords else False
        tech_hit = any(kw.lower() in tech for kw in tech_keywords) if tech_keywords else False

        if not (waste_hit or tech_hit):
            continue

        yearly[year] += 1
        matched_ids.append(str(entry.get("id", "")))

    yearly_dist = {}
    for y in range(year_range[0], year_range[1] + 1):
        yearly_dist[str(y)] = yearly.get(y, 0)

    return {
        "total_count": sum(yearly_dist.values()),
        "yearly_distribution": yearly_dist,
        "matched_ids": matched_ids,
    }


def scan_entries_ranked(
    entries: List[Dict[str, Any]],
    waste_keywords: List[str],
    tech_keywords: List[str],
    year_range: Tuple[int, int],
    max_results: int = 0,
) -> Dict[str, Any]:
    """
    OR 匹配 + 排序：
    - 优先级 2 (both): Waste_Object AND Technology 都命中
    - 优先级 1 (waste_only): 仅 Waste_Object 命中
    - 优先级 0 (tech_only): 仅 Technology 命中

    返回按优先级排序的 matched_ids，以及各级计数。
    """
    yearly: Dict[int, int] = defaultdict(int)
    both_ids: List[str] = []
    waste_only_ids: List[str] = []
    tech_only_ids: List[str] = []

    for entry in entries:
        year = _get_year(entry)
        if year is None:
            continue
        if year < year_range[0] or year > year_range[1]:
            continue

        proc = entry.get("Process_Event", {})
        waste_obj = (proc.get("Waste_Object") or "").lower()
        tech = (proc.get("Technology") or "").lower()

        waste_hit = any(kw.lower() in waste_obj for kw in waste_keywords) if waste_keywords else False
        tech_hit = any(kw.lower() in tech for kw in tech_keywords) if tech_keywords else False

        if not (waste_hit or tech_hit):
            continue

        entry_id = str(entry.get("id", ""))
        yearly[year] += 1

        if waste_hit and tech_hit:
            both_ids.append(entry_id)
        elif waste_hit:
            waste_only_ids.append(entry_id)
        else:
            tech_only_ids.append(entry_id)

    # 按优先级排序：both > waste_only > tech_only
    matched_ids = both_ids + waste_only_ids + tech_only_ids
    if max_results > 0:
        matched_ids = matched_ids[:max_results]

    # 补全年份范围内缺失的年份为 0
    yearly_dist = {}
    for y in range(year_range[0], year_range[1] + 1):
        yearly_dist[str(y)] = yearly.get(y, 0)

    return {
        "total_count": sum(yearly_dist.values()),
        "yearly_distribution": yearly_dist,
        "matched_ids": matched_ids,
        "match_breakdown": {
            "both": len(both_ids),
            "waste_only": len(waste_only_ids),
            "tech_only": len(tech_only_ids),
        },
    }


def compute_growth_rate(count_pre: int, count_post: int) -> str:
    """计算增长率百分比字符串"""
    if count_pre == 0:
        return "inf" if count_post > 0 else "0.0%"
    rate = (count_post - count_pre) / count_pre * 100
    return f"{rate:+.1f}%"


def detect_weak_signals(
    entries: List[Dict[str, Any]],
    post_tech_keywords: List[str],
    pre_year_range: Tuple[int, int],
    post_year_range: Tuple[int, int],
    threshold_ratio: float = 0.01,
    growth_factor: float = 3.0,
) -> List[Dict[str, Any]]:
    """
    识别弱信号技术：在 pre 时期占比极低（< threshold_ratio），
    但在 post 时期爆发式增长（> growth_factor 倍）的技术关键词。
    """
    signals = []
    for kw in post_tech_keywords:
        pre_count = 0
        post_count = 0
        for entry in entries:
            year = _get_year(entry)
            if year is None:
                continue
            tech = (entry.get("Process_Event", {}).get("Technology") or "").lower()
            if kw.lower() not in tech:
                continue
            if pre_year_range[0] <= year <= pre_year_range[1]:
                pre_count += 1
            elif post_year_range[0] <= year <= post_year_range[1]:
                post_count += 1

        is_weak = (
            pre_count <= 5
            and post_count >= pre_count * growth_factor
            and post_count > 0
        )
        if is_weak:
            signals.append({
                "keyword": kw,
                "count_pre": pre_count,
                "count_post": post_count,
                "growth_rate": compute_growth_rate(pre_count, post_count),
            })

    return signals


def build_case_data(
    case_config: Dict[str, Any],
    entries: List[Dict[str, Any]],
    pre_year_range: Tuple[int, int],
    post_year_range: Tuple[int, int],
) -> Dict[str, Any]:
    """
    为单个 case 构建完整的 benchmark 数据记录。

    Args:
        case_config: 来自 cases_config.json 的单个 case 配置
        entries: 全量 KG 条目
        pre_year_range: (2015, 2020)
        post_year_range: (2021, 2025)

    Returns:
        结构化的 benchmark 数据字典
    """
    pre_cfg = case_config["pre_shift"]
    post_cfg = case_config["post_shift"]

    # 1) pre_shift 统计：OR 匹配 + 优先级排序
    pre_result = scan_entries_ranked(
        entries,
        waste_keywords=pre_cfg["Waste_Object"],
        tech_keywords=pre_cfg["Technology"],
        year_range=pre_year_range,
    )

    # 2) post_shift 统计：OR 匹配 + 优先级排序
    post_result = scan_entries_ranked(
        entries,
        waste_keywords=post_cfg["Waste_Object"],
        tech_keywords=post_cfg["Technology"],
        year_range=post_year_range,
    )

    # 3) 领域整体统计（仅用 Waste_Object，不限技术方向）
    domain_pre = scan_entries(
        entries,
        waste_keywords=pre_cfg["Waste_Object"],
        tech_keywords=[],
        year_range=pre_year_range,
    )
    domain_post = scan_entries(
        entries,
        waste_keywords=post_cfg["Waste_Object"],
        tech_keywords=[],
        year_range=post_year_range,
    )

    # 4) 弱信号检测
    weak_signals = detect_weak_signals(
        entries,
        post_tech_keywords=post_cfg["Technology"],
        pre_year_range=pre_year_range,
        post_year_range=post_year_range,
    )

    # 5) 数据充足性判断
    data_sufficient = domain_pre["total_count"] >= 10 and domain_post["total_count"] >= 5

    return {
        "case_id": case_config["case_id"],
        "domain_name": case_config["domain_name"],
        "domain_name_en": case_config["domain_name_en"],
        "category": case_config["category"],
        # pre_shift
        "pre_shift": {
            "time_range": f"{pre_year_range[0]}-{pre_year_range[1]}",
            "query_keywords": pre_cfg,
            "paper_count": pre_result["total_count"],
            "yearly_distribution": pre_result["yearly_distribution"],
            "matched_ids": pre_result["matched_ids"],
            "match_breakdown": pre_result["match_breakdown"],
        },
        # post_shift
        "post_shift": {
            "time_range": f"{post_year_range[0]}-{post_year_range[1]}",
            "query_keywords": post_cfg,
            "paper_count": post_result["total_count"],
            "yearly_distribution": post_result["yearly_distribution"],
            "matched_ids": post_result["matched_ids"],
            "match_breakdown": post_result["match_breakdown"],
        },
        # 领域整体（仅按 Waste_Object）
        "domain_total": {
            "paper_count_pre": domain_pre["total_count"],
            "paper_count_post": domain_post["total_count"],
            "yearly_distribution_pre": domain_pre["yearly_distribution"],
            "yearly_distribution_post": domain_post["yearly_distribution"],
        },
        # 增长率
        "growth_rate_tech": compute_growth_rate(
            pre_result["total_count"], post_result["total_count"]
        ),
        "growth_rate_domain": compute_growth_rate(
            domain_pre["total_count"], domain_post["total_count"]
        ),
        # 弱信号
        "weak_signals": weak_signals,
        # 数据充足性
        "data_sufficiency": data_sufficient,
        # 验证查询参数（供后续直接调用）
        "validation_queries": {
            "pre_shift_query": {
                "Waste_Object": pre_cfg["Waste_Object"],
                "Technology": pre_cfg["Technology"],
                "year_range": list(pre_year_range),
            },
            "post_shift_query": {
                "Waste_Object": post_cfg["Waste_Object"],
                "Technology": post_cfg["Technology"],
                "year_range": list(post_year_range),
            },
        },
    }
