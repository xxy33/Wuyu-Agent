"""
多领域遥感检测报告生成器
"""
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from swagent.utils.logger import get_logger
from ..core import TaskLoader
from ..database import DatabaseManager

logger = get_logger(__name__)


class MultiDomainReportGenerator:
    """多领域遥感检测报告生成器"""

    def __init__(self, task_loader: TaskLoader):
        self.task_loader = task_loader
        self.llm_client = None

    async def generate_report(
        self,
        session_id: str,
        db: DatabaseManager,
        llm_config: Dict[str, str] = None
    ) -> str:
        """
        生成检测报告

        Args:
            session_id: 会话ID
            db: 数据库管理器
            llm_config: LLM配置（用于智能分析）

        Returns:
            Markdown格式的报告
        """
        # 获取数据
        session_info = db.get_session_info()
        statistics = db.get_statistics_summary()
        samples = db.get_sample_images(limit=10)

        # 构建报告
        report_parts = []

        # 1. 封面
        report_parts.append(self._generate_cover(session_info))

        # 2. 目录
        report_parts.append(self._generate_toc(session_info))

        # 3. 执行摘要
        report_parts.append(self._generate_summary(session_info, statistics))

        # 4. 天气信息
        if session_info.get("weather_data"):
            report_parts.append(self._generate_weather_section(session_info["weather_data"]))

        # 5. 各任务检测结果
        for task_name in session_info.get("selected_tasks", []):
            task_stats = statistics.get(task_name, {})
            task_samples = [s for s in samples if task_name in s.get("detection_results", {})]
            report_parts.append(self._generate_task_section(task_name, task_stats, task_samples))

        # 6. 样例展示
        report_parts.append(self._generate_sample_showcase(samples))

        # 7. LLM智能分析
        if llm_config:
            llm_analysis = await self._generate_llm_analysis(session_info, statistics, llm_config)
            report_parts.append(llm_analysis)

        # 8. 附录
        report_parts.append(self._generate_appendix(db))

        # 9. 页脚
        report_parts.append(self._generate_footer())

        return "\n".join(report_parts)

    def _generate_cover(self, session_info: Dict[str, Any]) -> str:
        """生成封面"""
        region = session_info.get("region_name", "未知地区")
        date = datetime.now().strftime("%Y-%m-%d")
        tasks = session_info.get("selected_tasks", [])
        task_names = [self.task_loader.get_task(t)["name"] for t in tasks]

        return f"""
# 多领域遥感检测综合报告

---

## {region}

### 监测日期：{date}

---

**报告编号**: MD-{datetime.now().strftime('%Y%m%d')}-{region[:2]}

**检测任务**: {', '.join(task_names)}

**编制单位**: 多领域遥感智能检测系统

---

"""

    def _generate_toc(self, session_info: Dict[str, Any]) -> str:
        """生成目录"""
        tasks = session_info.get("selected_tasks", [])

        toc = """
## 目录

1. [执行摘要](#执行摘要)
2. [监测期间天气状况](#监测期间天气状况)
"""
        for i, task in enumerate(tasks, 3):
            task_name = self.task_loader.get_task(task)["name"]
            toc += f"{i}. [{task_name}检测结果](#{task})\n"

        toc += f"""{len(tasks) + 3}. [样例展示](#样例展示)
{len(tasks) + 4}. [智能分析](#智能分析)
{len(tasks) + 5}. [附录](#附录)

---

"""
        return toc

    def _generate_summary(self, session_info: Dict[str, Any], statistics: Dict[str, Any]) -> str:
        """生成执行摘要"""
        total_images = session_info.get("total_images", 0)
        tasks = session_info.get("selected_tasks", [])

        summary = f"""
## 执行摘要

### 检测概况

| 项目 | 内容 |
|------|------|
| 监测地区 | {session_info.get('region_name', '未知')} |
| 检测任务数 | {len(tasks)} 个 |
| 分析图像总数 | **{total_images}** 张 |
| 检测时间 | {session_info.get('created_at', '未知')} |

### 各任务检测统计

| 任务 | 检测图像 | 检出数 | 检出率 | 目标总数 |
|------|----------|--------|--------|----------|
"""
        for task in tasks:
            task_config = self.task_loader.get_task(task)
            stats = statistics.get(task, {})
            total = stats.get("total_images", 0)
            with_target = stats.get("images_with_target", 0)
            rate = stats.get("detection_rate", 0)
            count = stats.get("target_count", 0)

            summary += f"| {task_config['name']} | {total} | {with_target} | {rate:.2%} | {count} |\n"

        summary += "\n---\n"
        return summary

    def _generate_weather_section(self, weather_data: Dict[str, Any]) -> str:
        """生成天气信息章节"""
        weather = weather_data.get("weather", {}).get("data", {})
        coords = weather_data.get("coordinates", {})

        return f"""
## 监测期间天气状况

| 项目 | 数值 |
|------|------|
| 查询时间 | 2024年9月1日 12:00 |
| 温度 | {weather.get('temperature_2m', 'N/A')} °C |
| 相对湿度 | {weather.get('relative_humidity_2m', 'N/A')} % |
| 降水量 | {weather.get('precipitation', 'N/A')} mm |
| 风速 | {weather.get('wind_speed_10m', 'N/A')} km/h |

*监测坐标: ({coords.get('latitude', 'N/A')}, {coords.get('longitude', 'N/A')})*

---

"""

    def _generate_task_section(
        self,
        task_name: str,
        stats: Dict[str, Any],
        samples: List[Dict[str, Any]]
    ) -> str:
        """生成单个任务的检测结果章节"""
        task_config = self.task_loader.get_task(task_name)

        section = f"""
## {task_config['name']}检测结果 {{#{task_name}}}

**任务描述**: {task_config['description']}

### 检测统计

| 指标 | 数值 |
|------|------|
| 检测图像数 | {stats.get('total_images', 0)} |
| 检测到目标 | {stats.get('images_with_target', 0)} |
| 检出率 | {stats.get('detection_rate', 0):.2%} |
| 目标总数 | {stats.get('target_count', 0)} |

"""
        # 如果有样例，展示前3个
        if samples:
            section += "### 检测样例\n\n"
            for i, sample in enumerate(samples[:3], 1):
                result = sample.get("detection_results", {}).get(task_name, {})
                section += f"""
**样例 {i}**: {sample.get('image_name', '未知')}

> {result.get('description', '无描述')}

"""

        section += "---\n"
        return section

    def _generate_sample_showcase(self, samples: List[Dict[str, Any]]) -> str:
        """生成样例展示章节"""
        section = """
## 样例展示

本章节展示检测到目标的图像样例，包含原始遥感图像与处理后的标注图像对比。

"""
        if not samples:
            section += "*本次监测未检测到目标，无样例可展示*\n\n---\n"
            return section

        section += f"*共检测到 {len(samples)} 张包含目标的图像，展示如下*\n\n"

        for i, sample in enumerate(samples[:10], 1):
            image_name = sample.get("image_name", f"样例-{i}")
            image_path = sample.get("image_path", "")
            processed_path = sample.get("processed_image_path", "")
            results = sample.get("detection_results", {})

            # 转换路径格式
            if image_path and image_path.startswith('/root/swagent/'):
                image_path = '/' + image_path.replace('/root/swagent/', '')
            elif image_path and not image_path.startswith('/'):
                image_path = '/' + image_path

            if processed_path and processed_path.startswith('/root/swagent/'):
                processed_path = '/' + processed_path.replace('/root/swagent/', '')
            elif processed_path and not processed_path.startswith('/'):
                processed_path = '/' + processed_path

            # 获取描述（只从检测到目标的任务中获取）
            description = ""
            detected_task = ""
            for task_name, result in results.items():
                if result.get("has_target") and result.get("description"):
                    description = result.get("description")
                    detected_task = self.task_loader.get_task(task_name)["name"]
                    break

            # 简化文件名显示（移除坐标和时间戳）
            display_name = Path(image_name).stem
            # 尝试提取更简洁的名称（去除处理后缀）
            if "_processed" in display_name:
                display_name = display_name.replace("_processed", "")

            section += f"""
### 样例 {i}: {display_name}

**检测任务**: {detected_task if detected_task else "未知"}

<table>
<tr>
<td width="50%" align="center">

**原始遥感图像**

![原图]({image_path})

</td>
<td width="50%" align="center">

**处理后标注图像**

"""
            if processed_path:
                section += f"![标注]({processed_path})\n"
            else:
                section += "*（处理后图像未生成）*\n"

            section += f"""
</td>
</tr>
</table>

**AI 分析描述**

> {description if description else '无描述'}

---

"""

        return section

    async def _generate_llm_analysis(
        self,
        session_info: Dict[str, Any],
        statistics: Dict[str, Any],
        llm_config: Dict[str, str]
    ) -> str:
        """生成LLM智能分析"""
        try:
            from swagent.llm.openai_client import OpenAIClient

            # 使用create类方法初始化LLM客户端
            client = OpenAIClient.create(
                api_key=llm_config.get("api_key"),
                base_url=llm_config.get("base_url"),
                model=llm_config.get("model")
            )

            # 构建分析prompt
            prompt = self._build_analysis_prompt(session_info, statistics)

            # 调用LLM
            response = await client.chat([
                {"role": "system", "content": "你是一个专业的遥感图像分析专家，请根据检测统计数据提供专业的分析报告。"},
                {"role": "user", "content": prompt}
            ])

            analysis = response.content

            logger.info("LLM智能分析生成成功")

            return f"""
## 智能分析

*以下内容由 AI 模型自动生成*

{analysis}

---

"""

        except Exception as e:
            logger.warning(f"LLM智能分析生成失败: {e}")
            return """
## 智能分析

*智能分析生成失败*

---

"""

    def _build_analysis_prompt(self, session_info: Dict[str, Any], statistics: Dict[str, Any]) -> str:
        """构建分析prompt"""
        region = session_info.get("region_name", "未知地区")
        tasks = session_info.get("selected_tasks", [])
        total_images = session_info.get("total_images", 0)

        prompt = f"""
请根据以下{region}地区的遥感检测统计数据，提供专业的分析报告：

## 检测概况
- 地区：{region}
- 图像总数：{total_images}
- 检测任务：{len(tasks)}个

## 各任务统计数据
"""
        for task in tasks:
            task_config = self.task_loader.get_task(task)
            stats = statistics.get(task, {})
            prompt += f"""
### {task_config['name']}
- 检测到目标的图像：{stats.get('images_with_target', 0)}张
- 检出率：{stats.get('detection_rate', 0):.2%}
- 目标总数：{stats.get('target_count', 0)}
"""

        prompt += """
请提供：
1. 整体环境状况评估
2. 主要发现和风险点分析
3. 具体整改建议（短期、中期、长期）
4. 后续监测建议
"""

        return prompt

    def _generate_appendix(self, db: DatabaseManager) -> str:
        """生成附录"""
        # 获取最近的处理日志
        results = db.get_all_results()

        appendix = """
## 附录

### 处理日志

```
"""
        # 只显示最后20条
        for result in results[-20:]:
            status = "检测到目标" if result.get("has_target") else "清洁区域"
            appendix += f"[{result.get('processed_at', '')}] {result.get('image_name', '')} → {status}\n"

        appendix += """```

### 错误记录

"""
        # 检查是否有错误
        errors = [r for r in results if r.get("detection_results", {}).get("error")]
        if errors:
            for err in errors[:10]:
                appendix += f"- {err.get('image_name')}: {err.get('detection_results', {}).get('error_message', '未知错误')}\n"
        else:
            appendix += "*无错误*\n"

        appendix += "\n---\n"
        return appendix

    def _generate_footer(self) -> str:
        """生成页脚"""
        return f"""
---

*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*

*多领域遥感智能检测系统*
"""
