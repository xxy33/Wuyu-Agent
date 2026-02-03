"""
多领域遥感检测主运行器
"""
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from swagent.utils.logger import get_logger
from .workflow import MultiDomainWorkflow
from .core import TaskLoader
from .report import MultiDomainReportGenerator

logger = get_logger(__name__)


async def run_multi_domain_detection(
    mode: str,
    input_path: str,
    city: str,
    tasks: List[str],
    output_dir: str = "./output",
    vl_base_url: str = None,
    vl_api_key: str = None,
    vl_model: str = None,
    llm_base_url: str = None,
    llm_api_key: str = None,
    llm_model: str = None,
    small_model_url: str = None,
    small_model_key: str = None,
    small_model_name: str = None,
    tile_size: int = 512,
    tile_overlap: int = 64,
    progress_callback: Optional[callable] = None,
    create_session_subdir: bool = True
) -> Dict[str, Any]:
    """
    运行多领域遥感检测

    Args:
        mode: 运行模式 (test/prod)
        input_path: 输入路径（图像或目录）
        city: 城市名称
        tasks: 任务列表
        output_dir: 输出目录
        vl_base_url: 视觉模型API地址
        vl_api_key: 视觉模型API密钥
        vl_model: 视觉模型名称
        llm_base_url: 文本模型API地址
        llm_api_key: 文本模型API密钥
        llm_model: 文本模型名称
        small_model_url: 小模型API地址
        small_model_key: 小模型API密钥
        small_model_name: 小模型名称
        tile_size: 切割尺寸
        tile_overlap: 重叠像素
        progress_callback: 进度回调函数 async def callback(current, total, filename, message)
        create_session_subdir: 是否在output_dir下创建session子目录 (CLI调用为True, Web调用为False)

    Returns:
        检测结果
    """
    logger.info("启动多领域遥感检测工作流")
    logger.info(f"  模式: {mode}")
    logger.info(f"  城市: {city}")
    logger.info(f"  输入: {input_path}")
    logger.info(f"  输出: {output_dir}")
    logger.info(f"  任务: {tasks}")
    logger.info(f"  视觉模型: {vl_model}")
    logger.info(f"  文本模型: {llm_model}")

    # 验证任务
    task_loader = TaskLoader()
    valid_tasks = task_loader.get_all_task_names()
    for task in tasks:
        if task not in valid_tasks:
            raise ValueError(f"无效任务: {task}。可用任务: {valid_tasks}")

    # 配置
    vl_config = {
        "base_url": vl_base_url,
        "api_key": vl_api_key,
        "model": vl_model
    }

    llm_config = {
        "base_url": llm_base_url,
        "api_key": llm_api_key,
        "model": llm_model
    }

    small_model_config = {
        "base_url": small_model_url,
        "api_key": small_model_key,
        "model": small_model_name
    }

    # 确保输出目录存在
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # 获取图像列表
    image_paths = _get_image_paths(mode, input_path, output_dir, tile_size, tile_overlap)

    if not image_paths:
        logger.error("未找到图像文件")
        return {"success": False, "error": "未找到图像文件"}

    logger.info(f"共找到 {len(image_paths)} 张图像")

    # 创建工作流
    workflow = MultiDomainWorkflow(
        selected_tasks=tasks,
        region_name=city,
        vl_config=vl_config,
        llm_config=llm_config,
        small_model_config=small_model_config,
        output_dir=output_dir,
        create_session_subdir=create_session_subdir
    )

    # 运行检测（带进度回调）
    if progress_callback:
        result = await _run_workflow_with_progress(workflow, image_paths, progress_callback)
    else:
        result = await workflow.run(image_paths)

    # 生成报告
    logger.info("生成检测报告...")
    if progress_callback:
        await progress_callback(len(image_paths), len(image_paths), "", "生成检测报告...")

    report_generator = MultiDomainReportGenerator(task_loader)
    report = await report_generator.generate_report(
        session_id=workflow.session_id,
        db=workflow.db,
        llm_config=llm_config
    )

    # 保存报告
    report_filename = f"multi_domain_report_{city}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_path = Path(output_dir) / report_filename
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    logger.info(f"报告已保存: {report_path}")

    return {
        "success": True,
        "session_id": workflow.session_id,
        "summary": result.get("summary", {}),
        "report_path": str(report_path),
        "total_images": len(image_paths),
        "samples_count": len(result.get("samples", []))
    }


async def _run_workflow_with_progress(
    workflow: MultiDomainWorkflow,
    image_paths: List[str],
    progress_callback: callable
) -> Dict[str, Any]:
    """带进度回调的工作流执行"""
    # 创建会话
    workflow.db.create_session(workflow.region_name, workflow.selected_tasks)

    # 获取天气数据
    await workflow.fetch_weather_data()

    total_images = len(image_paths)
    logger.info(f"开始处理 {total_images} 张图像")

    # 分层任务
    simple_tasks = workflow.task_loader.get_simple_tasks(workflow.selected_tasks)
    complex_tasks = workflow.task_loader.get_complex_tasks(workflow.selected_tasks)

    logger.info(f"  简单任务（合并调用）: {simple_tasks}")
    logger.info(f"  复杂任务（单独调用）: {complex_tasks}")

    # 处理每张图像
    for idx, image_path in enumerate(image_paths):
        try:
            image_name = Path(image_path).name

            # 调用进度回调
            await progress_callback(idx + 1, total_images, image_name, f"处理图像: {image_name}")

            log_msg = f"[{datetime.now().strftime('%H:%M:%S')}] [{idx+1}/{total_images}] 处理: {image_name}"
            workflow.logs.append(log_msg)

            # 检测
            detection_results = await workflow._process_single_image(
                image_path, simple_tasks, complex_tasks
            )

            # 判断是否检测到目标
            has_target = workflow._has_any_target(detection_results)

            # SAM2处理
            processed_image_path = None
            if has_target:
                processed_image_path = await workflow._process_with_sam2(
                    image_path, detection_results
                )
                workflow.logs.append(f"  → 检测到目标，已保存处理后图像")
            else:
                workflow.logs.append(f"  → 未检测到目标")

            # 保存到数据库
            workflow.db.save_image_result(
                image_name=image_name,
                image_path=image_path,
                detection_results=detection_results,
                has_target=has_target,
                processed_image_path=processed_image_path
            )

            # 更新统计
            workflow.aggregator.update(image_name, image_path, detection_results, processed_image_path)

            # 定期输出进度
            if (idx + 1) % 10 == 0:
                logger.info(f"处理进度: {idx+1}/{total_images}")

        except InterruptedError:
            raise
        except Exception as e:
            logger.error(f"处理图像失败: {image_path}, 错误: {e}")
            workflow.logs.append(f"  → 处理失败: {str(e)[:50]}")

    # 保存统计
    summary = workflow.aggregator.get_summary()
    workflow.db.save_statistics(summary)

    # 更新会话状态
    workflow.db.update_session_status("completed", total_images)

    # 汇总日志
    workflow._log_summary(summary)

    return {
        "session_id": workflow.session_id,
        "summary": summary,
        "samples": workflow.aggregator.get_all_samples(),
        "logs": workflow.logs
    }


def _get_image_paths(
    mode: str,
    input_path: str,
    output_dir: str,
    tile_size: int,
    tile_overlap: int
) -> List[str]:
    """获取图像路径列表"""
    input_path = Path(input_path)

    if mode == "test":
        # 测试模式：单张图像，需要切割
        if input_path.is_file():
            return _split_image(str(input_path), output_dir, tile_size, tile_overlap)
        else:
            logger.error(f"测试模式需要输入单张图像: {input_path}")
            return []

    elif mode == "prod":
        # 生产模式：目录中的所有图像
        if input_path.is_dir():
            image_paths = []
            for ext in ['*.png', '*.jpg', '*.jpeg', '*.tif', '*.tiff']:
                image_paths.extend(sorted(input_path.glob(ext)))
            return [str(p) for p in image_paths]
        else:
            logger.error(f"生产模式需要输入目录: {input_path}")
            return []

    return []


def _split_image(
    image_path: str,
    output_dir: str,
    tile_size: int,
    tile_overlap: int
) -> List[str]:
    """切割大图"""
    try:
        from swagent.waste_monitoring.processors.image_tiler import split_image
        import asyncio

        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 如果已经在异步环境中
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    split_image(image_path, output_dir, tile_size, tile_overlap)
                )
                return future.result()
        else:
            return asyncio.run(split_image(image_path, output_dir, tile_size, tile_overlap))

    except Exception as e:
        logger.error(f"图像切割失败: {e}")
        return []


def print_result(result: Dict[str, Any], city: str, mode: str):
    """打印结果"""
    print("\n" + "=" * 60)
    print("多领域遥感检测执行结果")
    print("=" * 60)

    if result.get("success"):
        print(f"\n状态: ✓ 成功")
    else:
        print(f"\n状态: ✗ 失败")
        print(f"错误: {result.get('error', '未知错误')}")
        return

    print(f"城市: {city}")
    print(f"模式: {mode}")

    print("\n--- 检测统计 ---")
    print(f"总图像数:     {result.get('total_images', 0)}")
    print(f"样例数:       {result.get('samples_count', 0)}")

    summary = result.get("summary", {})
    for task, stats in summary.items():
        print(f"\n{task}:")
        print(f"  检出率:     {stats.get('detection_rate', 0):.2%}")
        print(f"  目标数:     {stats.get('target_count', 0)}")

    print(f"\n报告路径: {result.get('report_path', '未生成')}")
    print("\n" + "=" * 60)
