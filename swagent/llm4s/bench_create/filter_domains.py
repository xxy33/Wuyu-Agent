"""
Phase 0: 按领域级样本量过滤有效 case。

过滤规则:
- domain_total.paper_count_pre >= 2
- domain_total.paper_count_post >= 2

输入:
- llm4s_exp_data/task2_bench/task2_bench.jsonl

输出:
- llm4s_exp_data/task2_bench/valid_cases.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def filter_valid_cases(records: List[Dict[str, Any]], min_count: int = 2) -> List[Dict[str, Any]]:
    valid: List[Dict[str, Any]] = []
    for rec in records:
        domain_total = rec.get("domain_total", {})
        pre = int(domain_total.get("paper_count_pre", 0) or 0)
        post = int(domain_total.get("paper_count_post", 0) or 0)
        if pre >= min_count and post >= min_count:
            valid.append(
                {
                    "case_id": rec.get("case_id", ""),
                    "domain_name": rec.get("domain_name", ""),
                    "domain_name_en": rec.get("domain_name_en", ""),
                    "category": rec.get("category", ""),
                    "domain_total": {
                        "paper_count_pre": pre,
                        "paper_count_post": post,
                    },
                    "growth_rate_domain": rec.get("growth_rate_domain", "0.0%"),
                    "growth_rate_tech": rec.get("growth_rate_tech", "0.0%"),
                    "weak_signals": rec.get("weak_signals", []),
                }
            )
    return valid


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter valid hindcast benchmark cases")
    parser.add_argument(
        "--bench_jsonl",
        default=str(Path(__file__).resolve().parents[3] / "llm4s_exp_data" / "task2_bench" / "task2_bench.jsonl"),
        help="Path to task2_bench.jsonl",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[3] / "llm4s_exp_data" / "task2_bench" / "valid_cases.json"),
        help="Output path for valid_cases.json",
    )
    parser.add_argument("--min_count", type=int, default=2, help="Min paper count for pre and post")
    args = parser.parse_args()

    bench_jsonl = Path(args.bench_jsonl)
    output = Path(args.output)

    records = load_jsonl(bench_jsonl)
    valid_cases = filter_valid_cases(records, min_count=args.min_count)

    payload = {
        "meta": {
            "source": str(bench_jsonl),
            "min_count": args.min_count,
            "total_cases": len(records),
            "valid_cases": len(valid_cases),
        },
        "cases": valid_cases,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[filter_domains] total={len(records)} valid={len(valid_cases)} -> {output}")


if __name__ == "__main__":
    main()
