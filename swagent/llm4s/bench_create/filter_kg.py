"""
Phase 1: 生成 pre-2020 KG 快照，防止未来信息泄漏。

规则:
- 仅保留 Meta_Info.Year <= cutoff_year 的条目

输入:
- 全量 KG JSONL

输出:
- llm4s_exp_data/task2_bench/pre_shift_kg.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Any


def parse_year(entry: Dict[str, Any]) -> int | None:
    year_raw = entry.get("Meta_Info", {}).get("Year", "")
    if year_raw is None:
        return None
    try:
        return int(str(year_raw).strip())
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter KG by year cutoff")
    parser.add_argument(
        "--kg_path",
        default=r"C:\Users\CHENXY\xwechat_files\wxid_5jqa2znrhivm21_8a25\msg\file\2026-02\New_Output_Remote.jsonl",
        help="Input KG JSONL path",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[3] / "llm4s_exp_data" / "task2_bench" / "pre_shift_kg.jsonl"),
        help="Output filtered KG JSONL path",
    )
    parser.add_argument("--cutoff_year", type=int, default=2020, help="Keep entries <= cutoff_year")
    args = parser.parse_args()

    kg_path = Path(args.kg_path)
    output = Path(args.output)
    cutoff_year = args.cutoff_year

    output.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    kept = 0
    dropped_invalid_year = 0
    dropped_future = 0

    with open(kg_path, "r", encoding="utf-8") as src, open(output, "w", encoding="utf-8") as dst:
        for line in src:
            raw = line.strip()
            if not raw:
                continue
            total += 1
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                dropped_invalid_year += 1
                continue

            year = parse_year(entry)
            if year is None:
                dropped_invalid_year += 1
                continue

            if year <= cutoff_year:
                dst.write(json.dumps(entry, ensure_ascii=False) + "\n")
                kept += 1
            else:
                dropped_future += 1

    print(
        "[filter_kg] "
        f"total={total} kept={kept} dropped_future={dropped_future} dropped_invalid_year={dropped_invalid_year} "
        f"-> {output}"
    )


if __name__ == "__main__":
    main()
