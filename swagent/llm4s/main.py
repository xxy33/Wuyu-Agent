"""
LLM4S 主入口
AI固废科学家Agent系统 - 命令行接口

用法:
  python -m swagent.llm4s.main experiment "研究问题"
  python -m swagent.llm4s.main trend "技术方向"
  python -m swagent.llm4s.main build-index
"""
import sys
import json
import asyncio
import logging
import argparse
from pathlib import Path

from swagent.llm4s.config import LLM4SConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("llm4s")


async def cmd_experiment(question: str, config: LLM4SConfig, output: str):
    """运行实验方案设计"""
    from swagent.llm4s.task1.workflow import run_experiment_design
    from swagent.llm4s.templates.experiment_report import render_experiment_report

    state = await run_experiment_design(question, config)
    report = render_experiment_report(state)
    print(report)

    if output:
        Path(output).write_text(report, encoding="utf-8")
        json_path = output.rsplit(".", 1)[0] + ".json"
        Path(json_path).write_text(
            json.dumps(state.get("final_scheme", {}), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"实验报告已保存: {output}, {json_path}")


async def cmd_trend(topic: str, config: LLM4SConfig, output: str):
    """运行研究趋势推演"""
    from swagent.llm4s.task2.workflow import run_trend_prediction
    from swagent.llm4s.templates.trend_report import render_trend_report

    state = await run_trend_prediction(topic, config)
    report = render_trend_report(state)
    print(report)

    if output:
        Path(output).write_text(report, encoding="utf-8")
        json_path = output.rsplit(".", 1)[0] + ".json"
        Path(json_path).write_text(
            json.dumps(state.get("final_report", {}), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"趋势报告已保存: {output}, {json_path}")


async def cmd_build_index(config: LLM4SConfig):
    """构建FAISS向量索引（使用本地BGE-M3模型，支持断点续跑）"""
    import os
    import numpy as np
    from swagent.llm4s.llm_client import LocalEmbeddingClient
    from swagent.llm4s.retrieval.kg_retriever import KGRetriever
    from swagent.llm4s.retrieval.vector_retriever import VectorRetriever

    # 断点文件路径
    checkpoint_dir = config.faiss_index_path
    os.makedirs(checkpoint_dir, exist_ok=True)
    checkpoint_file = os.path.join(checkpoint_dir, "checkpoint.npz")

    # 加载KG数据
    kg = KGRetriever(config.kg_path)
    kg.load()

    # 准备文本
    ids = []
    texts = []
    for entry in kg.entries:
        doc_id = str(entry.get("id", ""))
        if not doc_id:
            continue
        ids.append(doc_id)
        texts.append(KGRetriever.entry_to_text(entry))

    total = len(texts)
    logger.info(f"共 {total} 条待嵌入文本")

    # 检查断点
    start_idx = 0
    all_vectors = []
    if os.path.exists(checkpoint_file):
        ckpt = np.load(checkpoint_file, allow_pickle=True)
        saved_vectors = ckpt["vectors"]
        saved_count = int(ckpt["count"])
        # 验证数据一致性（总数相同才能续跑）
        if saved_count <= total:
            start_idx = saved_count
            all_vectors = saved_vectors.tolist()
            logger.info(f"检测到断点: 已完成 {start_idx}/{total}，从断点继续")
        else:
            logger.warning("断点数据与当前KG不一致，从头开始")

    if start_idx >= total:
        logger.info("所有嵌入已完成，直接构建索引")
    else:
        # 加载本地嵌入模型
        embedder = LocalEmbeddingClient(
            config.local_embedding_model_path,
            batch_size=config.embedding_batch_size,
        )

        remaining = texts[start_idx:]
        batch_size = config.embedding_batch_size
        save_interval = 1000  # 每1000批保存一次断点

        for i in range(0, len(remaining), batch_size):
            batch = remaining[i:i + batch_size]
            vecs = embedder.embed_batch(batch)
            all_vectors.extend(vecs)

            done = start_idx + i + len(batch)
            batch_num = i // batch_size
            if batch_num % 50 == 0:
                logger.info(f"  进度: {done}/{total} ({done * 100 // total}%)")

            # 定期保存断点
            if batch_num > 0 and batch_num % save_interval == 0:
                np.savez(
                    checkpoint_file,
                    vectors=np.array(all_vectors, dtype=np.float32),
                    count=done,
                )
                logger.info(f"  断点已保存: {done}/{total}")

    # 构建FAISS索引
    vectors = np.array(all_vectors, dtype=np.float32)
    logger.info(f"嵌入完成，向量形状: {vectors.shape}，开始构建FAISS索引...")

    vector = VectorRetriever(config.faiss_index_path, config.embedding_dim)
    vector.build_index(ids[:len(all_vectors)], vectors)

    # 清理断点文件
    if os.path.exists(checkpoint_file):
        os.remove(checkpoint_file)
        logger.info("断点文件已清理")

    logger.info("索引构建完成!")


def main():
    parser = argparse.ArgumentParser(description="LLM4S - AI固废科学家Agent系统")
    subparsers = parser.add_subparsers(dest="command")

    # 实验方案设计
    exp_parser = subparsers.add_parser("experiment", help="实验方案设计")
    exp_parser.add_argument("question", help="研究问题")
    exp_parser.add_argument("-o", "--output", default="", help="输出文件路径")

    # 研究趋势推演
    trend_parser = subparsers.add_parser("trend", help="研究趋势推演")
    trend_parser.add_argument("topic", help="技术方向")
    trend_parser.add_argument("-o", "--output", default="", help="输出文件路径")

    # 构建索引
    subparsers.add_parser("build-index", help="构建FAISS向量索引")

    args = parser.parse_args()
    config = LLM4SConfig()

    if args.command == "experiment":
        asyncio.run(cmd_experiment(args.question, config, args.output))
    elif args.command == "trend":
        asyncio.run(cmd_trend(args.topic, config, args.output))
    elif args.command == "build-index":
        asyncio.run(cmd_build_index(config))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
