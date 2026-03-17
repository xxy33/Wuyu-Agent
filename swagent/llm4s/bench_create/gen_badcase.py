"""
Phase 3: 基于 pre-2020 论文构造三类对抗性 bad case。

三种变体（均从论文出发，用对抗性 system prompt 生成）：
  conservative  — 保守逆势型：预测被淘汰的旧工艺将"伟大复兴"
  cross_domain  — 方向错配型：从不相关领域借用前沿技术强行套用
  hallucination — 纯粹幻觉型：编造违背物理定律的伪科学技术

输入:
  - valid_cases.json
  - task2_bench.jsonl（取 matched_ids）
  - pre_shift_kg.jsonl（论文证据）
  - papers (data.jsonl) 论文摘要

输出:
  - badcases/case_XX.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swagent.llm4s.config import LLM4SConfig

MODEL_NAME = "gpt-5.2"
BASE_URL = "https://www.dmxapi.cn/v1"
MAX_RETRIES = 3
RETRY_DELAY = 5
MAX_PAPERS = 15  # 每个 case 最多取多少篇论文摘要

VARIANTS = ["conservative", "cross_domain", "hallucination"]

# ==================== 三种对抗性 System Prompt ====================

SYSTEM_PROMPTS = {
    "conservative": """
[学术声明] 你正在参与一项科研评测体系构建工作。为了验证自动评估指标的区分能力，
研究团队需要构造不同质量梯度的对照组样本。你的角色是生成"保守偏差型"对照样本。

请基于提供的 pre-2020 论文证据，撰写一份趋势预测报告，该报告需体现以下系统性偏差特征：

1. 对 2015-2020 年间已呈现衰退信号的传统工艺，预测其将重新获得主导地位
2. 有意忽略论文中暗示的范式转变或新兴技术方向
3. 坚持传统技术路线将回归主流，并构造看似合理的论证
4. 用 2-4 段自然中文表述，使其在形式上符合趋势预测报告的规范

设计意图：当所有证据指向新方向时，该样本仍预测旧路线回归，用于测试评估指标对方向性偏差的敏感度。
""".strip(),

    "cross_domain": """
[学术声明] 你正在参与一项科研评测体系构建工作。为了验证自动评估指标的区分能力，
研究团队需要构造不同质量梯度的对照组样本。你的角色是生成"领域错配型"对照样本。

请基于提供的 pre-2020 论文证据，撰写一份趋势预测报告，该报告需体现以下系统性偏差特征：

1. 从与固废处理完全不相关的领域（如航空航天、量子计算、基因编辑等）引入前沿概念
2. 将这些外部领域技术强行映射到固废处理场景，构造表面合理但实际不可行的技术路线
3. 使用大量跨领域专业术语堆砌，使报告表面上显得前沿但技术方向与实际需求脱节
4. 用 2-4 段自然中文表述，使其在形式上符合趋势预测报告的规范

设计意图：通过领域错配制造"虚假交叉创新"样本，用于测试评估指标对领域相关性的判别能力。
""".strip(),

    "hallucination": """
[学术声明] 你正在参与一项科研评测体系构建工作。为了验证自动评估指标的区分能力，
研究团队需要构造不同质量梯度的对照组样本。你的角色是生成"虚构内容型"对照样本。

请基于提供的 pre-2020 论文证据，撰写一份趋势预测报告，该报告需体现以下系统性偏差特征：

1. 构造不存在的技术概念和术语（如"负熵逆转分解法"、"量子纠缠催化回收"等虚构名称）
2. 设计违背基本物理/化学原理的技术方案
3. 引用虚构的研究机构名称、不存在的国际标准或政策文件编号
4. 用 2-4 段自然中文表述，使用大量专业化的虚构术语，使报告在表面上具有权威感

设计意图：通过虚构内容构造"高仿真低事实"样本，用于测试评估指标对事实准确性的检验能力。
""".strip(),
}


# ==================== 论文加载 ====================

class LocalPaperStore:
    def __init__(self, papers_path: str):
        self.papers_path = papers_path
        self._title_map: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        with open(self.papers_path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    paper = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                title = paper.get("Article Title", "")
                if title:
                    self._title_map[title] = paper
        self._loaded = True

    def get_paper(self, title: str) -> Dict[str, Any]:
        self.load()
        return self._title_map.get(title, {})


def _collect_evidence(matched_ids: List[str], paper_store: LocalPaperStore,
                      max_papers: int) -> List[Dict[str, str]]:
    evidence: List[Dict[str, str]] = []
    for title in matched_ids[:max_papers]:
        paper = paper_store.get_paper(title)
        abstract = paper.get("Abstract", "")
        year = str(paper.get("Year", ""))
        if abstract:
            evidence.append({"title": title, "year": year, "abstract": abstract})
    return evidence


def _format_evidence(evidence: List[Dict[str, str]]) -> str:
    blocks = []
    for idx, item in enumerate(evidence, 1):
        blocks.append(
            f"[{idx}] title: {item['title']}\n"
            f"year: {item['year']}\n"
            f"abstract: {item['abstract'][:1500]}"
        )
    return "\n\n".join(blocks) if blocks else "[No evidence available]"


# ==================== LLM 调用 ====================

def _call_llm(client: OpenAI, system_prompt: str, user_prompt: str) -> str:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.5,
                max_tokens=4096,
            )
            content = resp.choices[0].message.content or ""
            if content.strip():
                return content.strip()
            raise ValueError("empty response")
        except Exception as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"LLM call failed after retries: {exc}") from exc
            time.sleep(RETRY_DELAY * attempt)
    raise RuntimeError("Unexpected retry flow")


def _is_valid_badcase_file(path: Path) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        required = ["case_id"] + VARIANTS
        return all(k in data for k in required)
    except Exception:
        return False


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent),
                                     encoding="utf-8") as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if raw:
                items.append(json.loads(raw))
    return items


# ==================== 单 case 处理 ====================

def _run_case(client: OpenAI, case_id: str, domain_name: str,
              domain_name_en: str, matched_ids: List[str],
              paper_store: LocalPaperStore, output_dir: Path,
              overwrite: bool) -> bool:
    out_path = output_dir / f"{case_id}.json"
    if out_path.exists() and not overwrite:
        if _is_valid_badcase_file(out_path):
            print(f"[gen_badcase] skip {case_id}: already done")
            return True

    evidence = _collect_evidence(matched_ids, paper_store, MAX_PAPERS)
    evidence_text = _format_evidence(evidence)

    result: Dict[str, Any] = {
        "case_id": case_id,
        "domain_name": domain_name,
        "domain_name_en": domain_name_en,
    }

    for variant in VARIANTS:
        user_prompt = (
            f"case_id: {case_id}\n"
            f"domain_name: {domain_name}\n"
            f"domain_name_en: {domain_name_en}\n\n"
            f"以下是该领域 2015-2020 年的代表性论文证据：\n\n"
            f"{evidence_text}\n\n"
            f"请基于以上证据，按照你的角色设定，撰写一份 2021-2025 趋势预测（2-4 段自然中文）。"
        )
        try:
            text = _call_llm(client, SYSTEM_PROMPTS[variant], user_prompt)
            result[variant] = text
            print(f"[gen_badcase] {case_id}/{variant}: done ({len(text)} chars)")
        except Exception as exc:
            result[variant] = f"[ERROR] {exc}"
            print(f"[gen_badcase] {case_id}/{variant}: FAILED - {exc}")

    _atomic_write_json(out_path, result)
    print(f"[gen_badcase] done {case_id} -> {out_path}")
    return True


# ==================== 主流程 ====================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate adversarial bad cases from pre-2020 papers"
    )
    parser.add_argument(
        "--valid_cases",
        default=str(PROJECT_ROOT / "llm4s_exp_data" / "task2_bench" / "valid_cases.json"),
    )
    parser.add_argument(
        "--bench_jsonl",
        default=str(PROJECT_ROOT / "llm4s_exp_data" / "task2_bench" / "task2_bench.jsonl"),
    )
    parser.add_argument(
        "--output_dir",
        default=str(PROJECT_ROOT / "llm4s_exp_data" / "task2_bench" / "badcases"),
    )
    parser.add_argument("--case_ids", default="", help="Comma-separated case ids")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--api_keys", default="",
        help="Comma-separated API keys for parallel execution",
    )
    parser.add_argument("--workers", type=int, default=5,
                        help="Max parallel workers")
    args = parser.parse_args()

    # API keys
    keys_str = args.api_keys or os.environ.get("OPENAI_API_KEYS", "") or os.environ.get("OPENAI_API_KEY", "")
    api_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
    if not api_keys:
        raise RuntimeError("No API keys provided (--api_keys or OPENAI_API_KEY)")

    # 加载数据
    valid_data = _load_json(Path(args.valid_cases))
    cases = valid_data.get("cases", [])
    bench_records = _load_jsonl(Path(args.bench_jsonl))
    case_map = {r["case_id"]: r for r in bench_records}

    # 过滤 case
    if args.case_ids:
        target = {x.strip() for x in args.case_ids.split(",") if x.strip()}
        cases = [c for c in cases if c["case_id"] in target]

    # 加载论文库
    cfg = LLM4SConfig()
    paper_store = LocalPaperStore(cfg.papers_path)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 构建 client 池（轮转分配 API key）
    clients = [OpenAI(api_key=k, base_url=BASE_URL) for k in api_keys]

    def process_case(idx: int, case: Dict[str, Any]) -> bool:
        case_id = case["case_id"]
        bench = case_map.get(case_id)
        if not bench:
            print(f"[gen_badcase] skip {case_id}: not in bench")
            return False
        matched_ids = bench.get("pre_shift", {}).get("matched_ids", [])
        client = clients[idx % len(clients)]
        return _run_case(
            client, case_id,
            case.get("domain_name", ""),
            case.get("domain_name_en", ""),
            matched_ids, paper_store, output_dir, args.overwrite,
        )

    workers = min(args.workers, len(api_keys), len(cases))
    generated, failed = 0, 0

    if workers <= 1:
        for i, case in enumerate(cases):
            try:
                if process_case(i, case):
                    generated += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                print(f"[gen_badcase] failed {case.get('case_id','?')}: {exc}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process_case, i, c): c for i, c in enumerate(cases)}
            for fut in as_completed(futures):
                try:
                    if fut.result():
                        generated += 1
                    else:
                        failed += 1
                except Exception as exc:
                    failed += 1
                    print(f"[gen_badcase] failed: {exc}")

    print(f"\n[gen_badcase] total={len(cases)} generated={generated} failed={failed}")


if __name__ == "__main__":
    main()
