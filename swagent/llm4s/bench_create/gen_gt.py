"""
Phase 2: 基于 post-2020 文献自动生成 Ground Truth (GT)。

输入:
- task2_bench.jsonl
- valid_cases.json
- papers data.jsonl (通过 PaperStore)

输出:
- llm4s_exp_data/task2_bench/ground_truths/case_XX.json

LLM:
- model: gpt-5.2
- base_url: https://www.dmxapi.cn/v1
- api_key: os.environ["OPENAI_API_KEY"]
"""
from __future__ import annotations

import argparse
import json
import os
import time
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

from openai import OpenAI

from swagent.llm4s.config import LLM4SConfig
from swagent.llm4s.retrieval.hybrid_retriever import PaperStore

MODEL_NAME = "gpt-5.2"
BASE_URL = "https://www.dmxapi.cn/v1"
MAX_RETRIES = 3
RETRY_DELAY = 5

SYSTEM_PROMPT = """
你是固废研究趋势分析专家。请基于提供的 post-2020 文献证据，提炼“已经发生的研究趋势”。
要求：
1) 仅依据提供证据，不得编造事实。
2) 输出 3-5 条趋势。
3) 每条趋势包含：trend_id, trend_name, evidence_keywords, growth_rate, description。
4) description 需简洁明确（2-4句），指出该趋势为何可验证。
5) 返回 JSON 对象，必须可被 json.loads 解析。
""".strip()


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if raw:
                items.append(json.loads(raw))
    return items


def _call_llm(client: OpenAI, user_prompt: str) -> Dict[str, Any]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content
            return json.loads(content)
        except Exception as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"LLM call failed after retries: {exc}") from exc
            time.sleep(RETRY_DELAY * attempt)
    raise RuntimeError("Unexpected retry flow")


def _is_valid_gt_file(path: Path) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        required = ["case_id", "gt_trends", "gt_summary_text"]
        return all(k in data for k in required)
    except Exception:
        return False


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _build_prompt(case: Dict[str, Any], abstracts: List[Dict[str, str]]) -> str:
    post = case.get("post_shift", {})
    weak_signals = case.get("weak_signals", [])
    domain_total = case.get("domain_total", {})

    evidence_lines: List[str] = []
    for idx, it in enumerate(abstracts, 1):
        evidence_lines.append(
            f"[{idx}] title: {it['title']}\\n"
            f"year: {it.get('year', '')}\\n"
            f"abstract: {it['abstract'][:1200]}"
        )

    weak_signal_text = json.dumps(weak_signals, ensure_ascii=False)
    yearly_text = json.dumps(post.get("yearly_distribution", {}), ensure_ascii=False)
    domain_text = json.dumps(domain_total, ensure_ascii=False)

    return (
        f"case_id: {case.get('case_id', '')}\\n"
        f"domain_name: {case.get('domain_name', '')}\\n"
        f"domain_name_en: {case.get('domain_name_en', '')}\\n"
        f"post_shift_yearly_distribution: {yearly_text}\\n"
        f"domain_total_stats: {domain_text}\\n"
        f"weak_signals: {weak_signal_text}\\n\\n"
        "post-2020 evidence abstracts:\\n"
        + "\\n\\n".join(evidence_lines)
        + "\\n\\n"
        "请输出 JSON，结构如下：\\n"
        "{\\n"
        "  \"gt_trends\": [\\n"
        "    {\"trend_id\": \"GT1\", \"trend_name\": \"...\", \"evidence_keywords\": [\"...\"], \"growth_rate\": \"...\", \"description\": \"...\"}\\n"
        "  ],\\n"
        "  \"gt_summary_text\": \"...\"\\n"
        "}"
    )


def _collect_abstracts(case: Dict[str, Any], paper_store: PaperStore, max_papers: int = 20) -> List[Dict[str, str]]:
    post = case.get("post_shift", {})
    titles = post.get("matched_ids", [])

    deduped: List[str] = []
    seen = set()
    for t in titles:
        if t and t not in seen:
            seen.add(t)
            deduped.append(t)

    abstracts: List[Dict[str, str]] = []
    for title in deduped[:max_papers]:
        abstract = paper_store.get_abstract(title)
        if not abstract:
            continue
        paper = paper_store.get_paper(title) or {}
        year = str(paper.get("Year", ""))
        abstracts.append({"title": title, "abstract": abstract, "year": year})

    return abstracts


def _process_one_case(
    client: OpenAI,
    case_id: str,
    case_map: Dict[str, Any],
    paper_store: PaperStore,
    output_dir: Path,
    max_papers: int,
    overwrite: bool,
) -> str:
    """处理单个 case，返回 'done' / 'skipped' / 'skip_no_case' / 'skip_no_abs' / 'failed'。"""
    out_path = output_dir / f"{case_id}.json"
    if out_path.exists() and not overwrite:
        if _is_valid_gt_file(out_path):
            return "skipped"
        print(f"[gen_gt] found invalid output, regenerating: {out_path}")

    case = case_map.get(case_id)
    if not case:
        print(f"[gen_gt] skip {case_id}: case not found")
        return "skip_no_case"

    abstracts = _collect_abstracts(case, paper_store, max_papers=max_papers)
    if not abstracts:
        print(f"[gen_gt] skip {case_id}: no abstracts")
        return "skip_no_abs"

    user_prompt = _build_prompt(case, abstracts)
    llm_result = _call_llm(client, user_prompt)

    gt_trends = llm_result.get("gt_trends", [])
    gt_summary_text = llm_result.get("gt_summary_text", "")

    output_payload = {
        "case_id": case_id,
        "domain_name": case.get("domain_name", ""),
        "domain_name_en": case.get("domain_name_en", ""),
        "source_post_titles": [a["title"] for a in abstracts],
        "gt_trends": gt_trends,
        "gt_summary_text": gt_summary_text,
    }

    _atomic_write_json(out_path, output_payload)
    print(f"[gen_gt] done {case_id} -> {out_path}")
    return "done"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ground truths from post-2020 papers")
    parser.add_argument(
        "--bench_jsonl",
        default=str(Path(__file__).resolve().parents[3] / "llm4s_exp_data" / "task2_bench" / "task2_bench.jsonl"),
    )
    parser.add_argument(
        "--valid_cases",
        default=str(Path(__file__).resolve().parents[3] / "llm4s_exp_data" / "task2_bench" / "valid_cases.json"),
    )
    parser.add_argument(
        "--output_dir",
        default=str(Path(__file__).resolve().parents[3] / "llm4s_exp_data" / "task2_bench" / "ground_truths"),
    )
    parser.add_argument("--max_papers", type=int, default=20)
    parser.add_argument("--overwrite", action="store_true", help="Regenerate existing outputs")
    parser.add_argument("--api_keys", type=str, default="",
                        help="Comma-separated API keys for parallel requests")
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of parallel workers")
    parser.add_argument("--case_ids", type=str, default="",
                        help="Comma-separated case IDs to process (default: all valid cases)")
    args = parser.parse_args()

    # --- 构建 client 列表 ---
    if args.api_keys.strip():
        api_keys = [k.strip() for k in args.api_keys.split(",") if k.strip()]
    else:
        env_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not env_key:
            raise RuntimeError("OPENAI_API_KEY or --api_keys is required")
        api_keys = [env_key]

    clients = [OpenAI(api_key=k, base_url=BASE_URL) for k in api_keys]

    bench_records = _load_jsonl(Path(args.bench_jsonl))
    case_map = {r.get("case_id", ""): r for r in bench_records}

    with open(args.valid_cases, "r", encoding="utf-8") as f:
        valid_payload = json.load(f)
    all_valid_ids = [c["case_id"] for c in valid_payload.get("cases", [])]

    # --- case_ids 过滤 ---
    if args.case_ids.strip():
        filter_set = {c.strip() for c in args.case_ids.split(",") if c.strip()}
        target_ids = [cid for cid in all_valid_ids if cid in filter_set]
        missing = filter_set - set(all_valid_ids)
        if missing:
            print(f"[gen_gt] warning: case_ids not in valid_cases: {missing}")
    else:
        target_ids = all_valid_ids

    cfg = LLM4SConfig()
    paper_store = PaperStore(cfg.papers_path)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    done = 0
    skipped = 0
    failed = 0
    workers = min(args.workers, len(api_keys), len(target_ids)) if target_ids else 1

    def _run(idx: int, case_id: str) -> str:
        client = clients[idx % len(clients)]
        return _process_one_case(
            client, case_id, case_map, paper_store,
            output_dir, args.max_papers, args.overwrite,
        )

    if workers <= 1:
        for i, case_id in enumerate(target_ids):
            try:
                result = _run(i, case_id)
                if result == "done":
                    done += 1
                elif result == "skipped":
                    skipped += 1
            except Exception as exc:
                failed += 1
                print(f"[gen_gt] failed {case_id}: {exc}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_run, i, cid): cid
                for i, cid in enumerate(target_ids)
            }
            for fut in as_completed(futures):
                cid = futures[fut]
                try:
                    result = fut.result()
                    if result == "done":
                        done += 1
                    elif result == "skipped":
                        skipped += 1
                except Exception as exc:
                    failed += 1
                    print(f"[gen_gt] failed {cid}: {exc}")

    print(
        f"[gen_gt] generated={done} skipped_existing={skipped} failed={failed} total_target={len(target_ids)}"
    )


if __name__ == "__main__":
    main()
