"""
bench_create CLI 入口

用法:
    python -m swagent.llm4s.bench_create.run_bench [--kg_path PATH] [--output_dir DIR] [--cases CASE_IDS]

功能:
    1. 加载 KG JSONL 全量数据
    2. 读取 cases_config.json 中 50 个领域配置
    3. 对每个 case 做全量扫描统计
    4. 输出 JSONL + 汇总 Markdown 报告到 output_dir
"""
import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

from .extractor import build_case_data, load_kg_entries

logger = logging.getLogger(__name__)

# 默认路径
_THIS_DIR = Path(__file__).parent
_DEFAULT_CONFIG = _THIS_DIR / "cases_config.json"
_DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "llm4s_exp_data" / "task2_bench"

# KG 路径从 config.py 的默认值读取
_DEFAULT_KG_PATH = r"C:\Users\CHENXY\xwechat_files\wxid_5jqa2znrhivm21_8a25\msg\file\2026-02\New_Output_Remote.jsonl"


def load_cases_config(config_path: Path) -> dict:
    """加载 cases_config.json"""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_bench(
    kg_path: str,
    output_dir: Path,
    config_path: Path,
    case_ids: Optional[List[str]] = None,
) -> None:
    """执行 benchmark 构建主流程"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 1) 加载配置
    config = load_cases_config(config_path)
    time_windows = config["time_windows"]
    pre_range = tuple(time_windows["pre_shift"])
    post_range = tuple(time_windows["post_shift"])
    cases = config["cases"]

    # 可选过滤指定 case
    if case_ids:
        id_set = set(case_ids)
        cases = [c for c in cases if c["case_id"] in id_set]
        logger.info(f"仅处理指定 case: {case_ids}")

    logger.info(f"待处理 case 数量: {len(cases)}")

    # 2) 加载 KG 全量数据（一次性加载到内存）
    logger.info(f"加载 KG: {kg_path}")
    entries = load_kg_entries(kg_path)

    # 3) 创建输出目录
    output_dir.mkdir(parents=True, exist_ok=True)

    # 4) 逐 case 构建
    results = []
    for i, case_cfg in enumerate(cases, 1):
        cid = case_cfg["case_id"]
        logger.info(f"[{i}/{len(cases)}] 处理 {cid}: {case_cfg['domain_name']}")
        try:
            record = build_case_data(case_cfg, entries, pre_range, post_range)
            results.append(record)
        except Exception:
            logger.exception(f"处理 {cid} 时出错，跳过")

    # 5) 写入 JSONL
    jsonl_path = output_dir / "task2_bench.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for rec in results:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info(f"JSONL 输出: {jsonl_path} ({len(results)} 条)")

    # 6) 生成汇总报告
    report_path = output_dir / "bench_summary.md"
    _write_summary_report(results, report_path, pre_range, post_range)
    logger.info(f"汇总报告: {report_path}")


def _write_summary_report(
    results: List[dict],
    report_path: Path,
    pre_range: tuple,
    post_range: tuple,
) -> None:
    """生成 Markdown 汇总报告"""
    lines = [
        "# Task2 Benchmark 数据集汇总报告\n",
        f"- 前窗口: {pre_range[0]}-{pre_range[1]}",
        f"- 后窗口: {post_range[0]}-{post_range[1]}",
        f"- 总 case 数: {len(results)}",
        f"- 数据充足 case 数: {sum(1 for r in results if r['data_sufficiency'])}",
        "",
        "## 各 Case 概览\n",
        "| case_id | 领域 | 类别 | pre论文数 | post论文数 | 技术增长率 | 领域增长率 | 弱信号数 | 数据充足 |",
        "|---------|------|------|----------|----------|-----------|-----------|---------|---------|",
    ]

    for r in results:
        ws = len(r.get("weak_signals", []))
        suf = "✅" if r["data_sufficiency"] else "❌"
        lines.append(
            f"| {r['case_id']} | {r['domain_name']} | {r['category']} "
            f"| {r['pre_shift']['paper_count']} | {r['post_shift']['paper_count']} "
            f"| {r['growth_rate_tech']} | {r['growth_rate_domain']} "
            f"| {ws} | {suf} |"
        )

    # 按类别统计
    cat_stats: dict = {}
    for r in results:
        cat = r["category"]
        if cat not in cat_stats:
            cat_stats[cat] = {"total": 0, "sufficient": 0, "pre_papers": 0, "post_papers": 0}
        cat_stats[cat]["total"] += 1
        cat_stats[cat]["sufficient"] += 1 if r["data_sufficiency"] else 0
        cat_stats[cat]["pre_papers"] += r["pre_shift"]["paper_count"]
        cat_stats[cat]["post_papers"] += r["post_shift"]["paper_count"]

    lines.extend([
        "",
        "## 按类别统计\n",
        "| 类别 | case数 | 数据充足数 | pre总论文 | post总论文 |",
        "|------|--------|-----------|----------|----------|",
    ])
    for cat, s in cat_stats.items():
        lines.append(f"| {cat} | {s['total']} | {s['sufficient']} | {s['pre_papers']} | {s['post_papers']} |")

    # 数据不足的 case 列表
    insufficient = [r for r in results if not r["data_sufficiency"]]
    if insufficient:
        lines.extend([
            "",
            "## 数据不足的 Case（需关注）\n",
        ])
        for r in insufficient:
            lines.append(
                f"- **{r['case_id']}** {r['domain_name']}: "
                f"领域 pre={r['domain_total']['paper_count_pre']}, "
                f"post={r['domain_total']['paper_count_post']}"
            )

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Task2 Benchmark 数据集构建")
    parser.add_argument(
        "--kg_path",
        default=_DEFAULT_KG_PATH,
        help="KG JSONL 数据文件路径",
    )
    parser.add_argument(
        "--output_dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        help="输出目录",
    )
    parser.add_argument(
        "--config",
        default=str(_DEFAULT_CONFIG),
        help="cases_config.json 路径",
    )
    parser.add_argument(
        "--cases",
        nargs="*",
        default=None,
        help="仅处理指定 case_id，例如 case_01 case_11",
    )
    args = parser.parse_args()

    run_bench(
        kg_path=args.kg_path,
        output_dir=Path(args.output_dir),
        config_path=Path(args.config),
        case_ids=args.cases,
    )


if __name__ == "__main__":
    main()
