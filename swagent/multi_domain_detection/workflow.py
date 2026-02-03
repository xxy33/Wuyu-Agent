"""
多领域遥感检测工作流
"""
import asyncio
import base64
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import uuid

from swagent.utils.logger import get_logger
from .core import TaskLoader, PromptBuilder
from .database import DatabaseManager
from .utils import RobustResultParser

logger = get_logger(__name__)


class MultiDomainVLDetector:
    """支持动态prompt的视觉语言模型检测器"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str,
        max_retries: int = 3
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self.max_retries = max_retries
        self._client = None

    async def _get_client(self):
        """获取LLM客户端"""
        if self._client is None:
            from swagent.llm.openai_client import OpenAIClient
            self._client = OpenAIClient.create(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model
            )
        return self._client

    async def detect(self, image_path: str) -> Dict[str, Any]:
        """
        对图片进行检测

        Args:
            image_path: 图片路径

        Returns:
            检测结果字典
        """
        client = await self._get_client()
        if client is None:
            return {"error": True, "raw_response": "Client not initialized"}

        # 读取并编码图片
        image_base64 = self._encode_image(image_path)

        # 重试机制
        last_error = None
        for attempt in range(self.max_retries):
            try:
                result = await self._call_vl(client, image_base64)
                if result is not None:
                    return result
            except Exception as e:
                last_error = str(e)
                logger.warning(f"VL检测第 {attempt + 1} 次失败: {e}")

        return {"error": True, "raw_response": f"Failed after {self.max_retries} attempts: {last_error}"}

    async def _call_vl(self, client, image_base64: str) -> Dict[str, Any]:
        """调用VL模型"""
        messages = [
            {
                "role": "system",
                "content": self.system_prompt
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}"
                        }
                    }
                ]
            }
        ]

        response = await client.chat(messages, temperature=0.1, max_tokens=2000)
        content = response.content

        return {
            "error": False,
            "raw_response": content
        }

    def _encode_image(self, image_path: str) -> str:
        """将图片编码为base64"""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")


class StatisticsAggregator:
    """统计汇总器"""

    def __init__(self, selected_tasks: List[str]):
        self.selected_tasks = selected_tasks
        self.stats = {
            task: {
                "total_images": 0,
                "images_with_target": 0,
                "target_count": 0,
                "type_distribution": {},
                "sample_images": []
            }
            for task in selected_tasks
        }

    def update(self, image_name: str, image_path: str, detection_results: Dict[str, Any], processed_image_path: str = None):
        """更新统计信息"""
        for task in self.selected_tasks:
            task_result = detection_results.get(task, {})
            stats = self.stats[task]

            stats["total_images"] += 1

            if task_result.get("has_target"):
                stats["images_with_target"] += 1
                # 处理不同任务的count字段名称差异（tank使用total_count，其他使用count）
                count = task_result.get("count", task_result.get("total_count", 0))
                stats["target_count"] += count

                # 保存前10张样例
                if len(stats["sample_images"]) < 10:
                    stats["sample_images"].append({
                        "image_name": image_name,
                        "image_path": image_path,
                        "processed_image_path": processed_image_path,
                        "result": task_result
                    })

    def get_summary(self) -> Dict[str, Any]:
        """获取汇总统计"""
        summary = {}
        for task, stats in self.stats.items():
            total = stats["total_images"]
            with_target = stats["images_with_target"]
            summary[task] = {
                "total_images": total,
                "images_with_target": with_target,
                "detection_rate": with_target / total if total > 0 else 0,
                "target_count": stats["target_count"],
                "sample_count": len(stats["sample_images"])
            }
        return summary

    def get_all_samples(self) -> List[Dict[str, Any]]:
        """获取所有样例（去重，最多10张）"""
        all_samples = []
        seen_images = set()

        for task, stats in self.stats.items():
            for sample in stats["sample_images"]:
                if sample["image_name"] not in seen_images:
                    seen_images.add(sample["image_name"])
                    sample["task"] = task
                    all_samples.append(sample)

                if len(all_samples) >= 10:
                    return all_samples

        return all_samples


class MultiDomainWorkflow:
    """多领域遥感检测工作流"""

    def __init__(
        self,
        selected_tasks: List[str],
        region_name: str,
        vl_config: Dict[str, str],
        llm_config: Dict[str, str],
        small_model_config: Dict[str, str],
        output_dir: str = "./output",
        create_session_subdir: bool = True
    ):
        self.selected_tasks = selected_tasks
        self.region_name = region_name
        self.vl_config = vl_config
        self.llm_config = llm_config
        self.small_model_config = small_model_config

        # 生成会话ID
        self.session_id = f"{region_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # 根据参数决定是否创建 session 子目录
        if create_session_subdir:
            # CLI 调用：在 output_dir 下创建 session 子目录
            self.session_output_dir = Path(output_dir) / self.session_id
        else:
            # Web 调用：output_dir 已经是 session 专用目录
            self.session_output_dir = Path(output_dir)

        self.session_output_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir = str(self.session_output_dir)

        # 初始化组件
        self.task_loader = TaskLoader()
        self.prompt_builder = PromptBuilder(self.task_loader)
        self.result_parser = RobustResultParser()
        # 数据库存储在 session 独立目录中
        self.db = DatabaseManager(self.session_id, f"{self.output_dir}/detection.db")

        # 统计汇总器
        self.aggregator = StatisticsAggregator(selected_tasks)

        # 日志
        self.logs = []

        logger.info(f"初始化多领域检测工作流 - 会话: {self.session_id}")
        logger.info(f"  选中任务: {selected_tasks}")
        logger.info(f"  地区: {region_name}")
        logger.info(f"  输出目录: {self.output_dir}")

    async def run(self, image_paths: List[str]) -> Dict[str, Any]:
        """
        运行检测工作流

        Args:
            image_paths: 图像路径列表

        Returns:
            检测结果汇总
        """
        # 创建会话
        self.db.create_session(self.region_name, self.selected_tasks)

        # 获取天气数据（在创建会话后）
        await self.fetch_weather_data()

        total_images = len(image_paths)
        logger.info(f"开始处理 {total_images} 张图像")

        # 分层任务
        simple_tasks = self.task_loader.get_simple_tasks(self.selected_tasks)
        complex_tasks = self.task_loader.get_complex_tasks(self.selected_tasks)

        logger.info(f"  简单任务（合并调用）: {simple_tasks}")
        logger.info(f"  复杂任务（单独调用）: {complex_tasks}")

        # 处理每张图像
        for idx, image_path in enumerate(image_paths):
            try:
                image_name = Path(image_path).name
                log_msg = f"[{datetime.now().strftime('%H:%M:%S')}] [{idx+1}/{total_images}] 处理: {image_name}"
                self.logs.append(log_msg)

                # 检测
                detection_results = await self._process_single_image(
                    image_path, simple_tasks, complex_tasks
                )

                # 判断是否检测到目标
                has_target = self._has_any_target(detection_results)

                # SAM2处理
                processed_image_path = None
                if has_target:
                    processed_image_path = await self._process_with_sam2(
                        image_path, detection_results
                    )
                    self.logs.append(f"  → 检测到目标，已保存处理后图像")
                else:
                    self.logs.append(f"  → 未检测到目标")

                # 保存到数据库
                self.db.save_image_result(
                    image_name=image_name,
                    image_path=image_path,
                    detection_results=detection_results,
                    has_target=has_target,
                    processed_image_path=processed_image_path
                )

                # 更新统计
                self.aggregator.update(image_name, image_path, detection_results, processed_image_path)

                # 定期输出进度
                if (idx + 1) % 10 == 0:
                    logger.info(f"处理进度: {idx+1}/{total_images}")

            except Exception as e:
                logger.error(f"处理图像失败: {image_path}, 错误: {e}")
                self.logs.append(f"  → 处理失败: {str(e)[:50]}")

        # 保存统计
        summary = self.aggregator.get_summary()
        self.db.save_statistics(summary)

        # 更新会话状态
        self.db.update_session_status("completed", total_images)

        # 汇总日志
        self._log_summary(summary)

        return {
            "session_id": self.session_id,
            "summary": summary,
            "samples": self.aggregator.get_all_samples(),
            "logs": self.logs
        }

    async def _process_single_image(
        self,
        image_path: str,
        simple_tasks: List[str],
        complex_tasks: List[str]
    ) -> Dict[str, Any]:
        """处理单张图像"""
        results = {}

        # 处理简单任务（合并调用）
        if simple_tasks:
            simple_results = await self._call_vl_model_multi_task(image_path, simple_tasks)
            results.update(simple_results)

        # 处理复杂任务（单独调用）
        for task in complex_tasks:
            task_result = await self._call_vl_model_single_task(image_path, task)
            results[task] = task_result

        return results

    async def _call_vl_model_single_task(self, image_path: str, task_name: str) -> Dict[str, Any]:
        """调用VL模型处理单个任务"""
        import time
        image_name = Path(image_path).name
        prompt = self.prompt_builder.build_single_task_prompt(task_name)

        detector = MultiDomainVLDetector(
            base_url=self.vl_config.get("base_url"),
            api_key=self.vl_config.get("api_key"),
            model=self.vl_config.get("model"),
            system_prompt=prompt
        )

        start_time = time.time()
        result = await detector.detect(image_path)
        latency_ms = int((time.time() - start_time) * 1000)

        raw_response = result.get("raw_response", "")
        success = not result.get("error", False)

        # 解析结果
        parsed = self.result_parser.parse(raw_response, [task_name])

        # 处理单任务结果格式
        if "has_target" in parsed:
            final_result = parsed
        elif task_name in parsed:
            final_result = parsed[task_name]
        else:
            final_result = {
                "has_target": False,
                "error": True,
                "raw_response": raw_response,
                "description": "解析失败"
            }

        # 记录模型调用日志
        self.db.save_model_call(
            image_name=image_name,
            call_type="vl_complex",
            model_name=self.vl_config.get("model"),
            prompt=prompt,
            image_path=image_path,
            raw_response=raw_response,
            parsed_result=final_result,
            success=success,
            error_message=raw_response if not success else None,
            latency_ms=latency_ms
        )

        return final_result

    async def _call_vl_model_multi_task(self, image_path: str, task_names: List[str]) -> Dict[str, Any]:
        """调用VL模型处理多个任务"""
        import time
        image_name = Path(image_path).name
        prompt = self.prompt_builder.build_multi_task_prompt(task_names)

        detector = MultiDomainVLDetector(
            base_url=self.vl_config.get("base_url"),
            api_key=self.vl_config.get("api_key"),
            model=self.vl_config.get("model"),
            system_prompt=prompt
        )

        start_time = time.time()
        result = await detector.detect(image_path)
        latency_ms = int((time.time() - start_time) * 1000)

        raw_response = result.get("raw_response", "")
        success = not result.get("error", False)

        # 解析结果
        parsed = self.result_parser.parse(raw_response, task_names)

        # 确保所有任务都有结果
        for task in task_names:
            if task not in parsed:
                parsed[task] = {
                    "has_target": False,
                    "error": True,
                    "description": "任务结果缺失"
                }

        # 记录模型调用日志
        self.db.save_model_call(
            image_name=image_name,
            call_type="vl_simple",
            model_name=self.vl_config.get("model"),
            prompt=prompt,
            image_path=image_path,
            raw_response=raw_response,
            parsed_result=parsed,
            success=success,
            error_message=raw_response if not success else None,
            latency_ms=latency_ms
        )

        return parsed

    async def _process_with_sam2(self, image_path: str, detection_results: Dict[str, Any]) -> Optional[str]:
        """使用SAM2处理图像"""
        import time
        import numpy as np
        from swagent.waste_monitoring.processors.small_model_detector import call_small_model_api

        def convert_to_serializable(obj):
            """将对象转换为可JSON序列化的格式"""
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, dict):
                return {k: convert_to_serializable(v) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [convert_to_serializable(item) for item in obj]
            elif isinstance(obj, (np.integer, np.floating)):
                return obj.item()
            return obj

        image_name = Path(image_path).name

        # 收集所有boundingbox
        all_bboxes = []
        for task_name, result in detection_results.items():
            if result.get("has_target") and result.get("boundingbox"):
                all_bboxes.extend(result["boundingbox"])

        if not all_bboxes:
            return None

        start_time = time.time()
        try:
            result = await call_small_model_api(
                image_path=image_path,
                boundingboxes=all_bboxes,
                output_dir=self.output_dir,
                base_url=self.small_model_config.get("base_url"),
                api_key=self.small_model_config.get("api_key"),
                model=self.small_model_config.get("model")
            )
            latency_ms = int((time.time() - start_time) * 1000)

            success = result.get("success", False)
            output_path = result.get("output_path") if success else None

            # 转换 result 为可序列化格式
            serializable_result = convert_to_serializable(result)

            # 记录模型调用日志
            self.db.save_model_call(
                image_name=image_name,
                call_type="sam2",
                model_name=self.small_model_config.get("model"),
                prompt=json.dumps({"boundingboxes": all_bboxes}, ensure_ascii=False),
                image_path=image_path,
                raw_response=json.dumps(serializable_result, ensure_ascii=False),
                parsed_result={"output_path": output_path},
                success=success,
                error_message=result.get("error") if not success else None,
                latency_ms=latency_ms
            )

            return output_path

        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            logger.warning(f"SAM2处理失败: {e}")

            # 记录失败的调用
            self.db.save_model_call(
                image_name=image_name,
                call_type="sam2",
                model_name=self.small_model_config.get("model"),
                prompt=json.dumps({"boundingboxes": all_bboxes}, ensure_ascii=False),
                image_path=image_path,
                raw_response=None,
                parsed_result=None,
                success=False,
                error_message=str(e),
                latency_ms=latency_ms
            )

        return None

        return None

    def _has_any_target(self, detection_results: Dict[str, Any]) -> bool:
        """检查是否有任何任务检测到目标"""
        for task_name, result in detection_results.items():
            if result.get("has_target"):
                return True
        return False

    def _log_summary(self, summary: Dict[str, Any]):
        """输出汇总日志"""
        logger.info("=" * 50)
        logger.info("检测完成汇总")
        logger.info("=" * 50)

        for task, stats in summary.items():
            task_config = self.task_loader.get_task(task)
            logger.info(f"\n{task_config['name']}:")
            logger.info(f"  - 检测图像: {stats['total_images']} 张")
            logger.info(f"  - 检测到目标: {stats['images_with_target']} 张")
            logger.info(f"  - 检出率: {stats['detection_rate']:.2%}")
            logger.info(f"  - 目标总数: {stats['target_count']}")

    async def fetch_weather_data(self) -> Dict[str, Any]:
        """获取天气数据"""
        try:
            from swagent.tools.domain.location_tool import LocationTool
            from swagent.tools.domain.weather_tool import WeatherTool

            # 获取坐标
            location_tool = LocationTool()
            location_result = await location_tool.execute(address=self.region_name)

            if location_result.success and location_result.data:
                lat = location_result.data["latitude"]
                lon = location_result.data["longitude"]

                # 获取天气
                weather_tool = WeatherTool()
                weather_result = await weather_tool.execute(
                    latitude=lat,
                    longitude=lon
                )

                weather_data = {
                    "coordinates": {"latitude": lat, "longitude": lon},
                    "weather": weather_result.data if weather_result.success else {}
                }

                # 保存到数据库
                self.db.save_weather_data(weather_data)

                logger.info(f"天气数据获取成功: {weather_data}")
                return weather_data

        except Exception as e:
            logger.warning(f"获取天气数据失败: {e}")

        return {}


async def run_detection_workflow(
    image_dir: str,
    output_dir: str,
    tasks: List[str],
    city_name: str,
    vl_base_url: str,
    vl_api_key: str,
    vl_model: str,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    sam2_url: Optional[str] = None,
    sam2_api_key: Optional[str] = None,
    sam2_model: Optional[str] = None,
    progress_callback: Optional[callable] = None
) -> Dict[str, Any]:
    """
    Web API 入口函数 - 运行检测工作流并支持进度回调

    Args:
        image_dir: 图像目录路径
        output_dir: 输出目录路径
        tasks: 选中的任务ID列表
        city_name: 城市名称
        vl_base_url: VL模型API地址
        vl_api_key: VL API密钥
        vl_model: VL模型名称
        llm_base_url: LLM API地址
        llm_api_key: LLM API密钥
        llm_model: LLM模型名称
        sam2_url: SAM2服务URL（可选）
        sam2_api_key: SAM2 API密钥（可选）
        sam2_model: SAM2模型名称（可选）
        progress_callback: 进度回调函数 async def callback(current, filename, message)

    Returns:
        检测结果汇总
    """
    from pathlib import Path

    # 构建VL配置
    vl_config = {
        "base_url": vl_base_url,
        "api_key": vl_api_key,
        "model": vl_model
    }

    # 构建LLM配置
    llm_config = {
        "base_url": llm_base_url,
        "api_key": llm_api_key,
        "model": llm_model
    }

    # 构建SAM2配置
    small_model_config = {
        "base_url": sam2_url or "",
        "api_key": sam2_api_key or "",
        "model": sam2_model or "sam2"
    }

    # 获取图像文件列表
    image_dir_path = Path(image_dir)
    image_extensions = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
    image_paths = [
        str(f) for f in image_dir_path.iterdir()
        if f.is_file() and f.suffix.lower() in image_extensions
    ]

    if not image_paths:
        raise ValueError("No valid image files found in the directory")

    # 创建工作流
    workflow = MultiDomainWorkflow(
        selected_tasks=tasks,
        region_name=city_name,
        vl_config=vl_config,
        llm_config=llm_config,
        small_model_config=small_model_config,
        output_dir=output_dir
    )

    # 如果有进度回调，包装run方法
    if progress_callback:
        return await _run_with_progress(workflow, image_paths, progress_callback)
    else:
        return await workflow.run(image_paths)


async def _run_with_progress(
    workflow: 'MultiDomainWorkflow',
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

    # 处理每张图像
    for idx, image_path in enumerate(image_paths):
        try:
            image_name = Path(image_path).name

            # 调用进度回调
            if progress_callback:
                await progress_callback(idx + 1, image_name, f"处理图像: {image_name}")

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
