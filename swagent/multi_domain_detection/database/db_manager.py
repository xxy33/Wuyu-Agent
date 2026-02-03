"""
数据库管理器
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from contextlib import contextmanager

from swagent.utils.logger import get_logger
from .schema import SCHEMA_SQL

logger = get_logger(__name__)


class DatabaseManager:
    """数据库管理器"""

    def __init__(self, session_id: str, db_path: str = "./output/detection.db"):
        self.session_id = session_id
        self.db_path = db_path

        # 确保目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # 初始化数据库
        self._init_database()

    @contextmanager
    def _get_connection(self):
        """获取数据库连接（上下文管理器）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def _init_database(self):
        """初始化数据库表"""
        with self._get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
        logger.info(f"数据库初始化完成: {self.db_path}")

    def create_session(self, region_name: str, selected_tasks: List[str]):
        """创建检测会话"""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO detection_sessions (session_id, region_name, selected_tasks)
                VALUES (?, ?, ?)
            """, (self.session_id, region_name, json.dumps(selected_tasks)))

        logger.info(f"创建检测会话: {self.session_id}, 地区: {region_name}")

    def save_image_result(
        self,
        image_name: str,
        image_path: str,
        detection_results: Dict[str, Any],
        has_target: bool,
        processed_image_path: Optional[str] = None
    ):
        """保存图像检测结果"""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO image_results
                (session_id, image_name, image_path, detection_results, has_target, processed_image_path)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                self.session_id,
                image_name,
                image_path,
                json.dumps(detection_results, ensure_ascii=False),
                has_target,
                processed_image_path
            ))

    def save_statistics(self, statistics: Dict[str, Any]):
        """保存统计数据"""
        with self._get_connection() as conn:
            for task_name, metrics in statistics.items():
                for metric_name, metric_value in metrics.items():
                    conn.execute("""
                        INSERT OR REPLACE INTO task_statistics
                        (session_id, task_name, metric_name, metric_value, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        self.session_id,
                        task_name,
                        metric_name,
                        json.dumps(metric_value, ensure_ascii=False),
                        datetime.now()
                    ))

    def update_session_status(self, status: str, total_images: int = None):
        """更新会话状态"""
        with self._get_connection() as conn:
            if total_images is not None:
                conn.execute("""
                    UPDATE detection_sessions
                    SET status = ?, total_images = ?, completed_at = ?
                    WHERE session_id = ?
                """, (status, total_images, datetime.now(), self.session_id))
            else:
                conn.execute("""
                    UPDATE detection_sessions
                    SET status = ?, completed_at = ?
                    WHERE session_id = ?
                """, (status, datetime.now(), self.session_id))

    def save_weather_data(self, weather_data: Dict[str, Any]):
        """保存天气数据"""
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE detection_sessions
                SET weather_data = ?
                WHERE session_id = ?
            """, (json.dumps(weather_data, ensure_ascii=False), self.session_id))

    def get_session_info(self) -> Dict[str, Any]:
        """获取会话信息"""
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT * FROM detection_sessions WHERE session_id = ?
            """, (self.session_id,)).fetchone()

            if row:
                return {
                    "session_id": row["session_id"],
                    "region_name": row["region_name"],
                    "selected_tasks": json.loads(row["selected_tasks"]),
                    "created_at": row["created_at"],
                    "total_images": row["total_images"],
                    "status": row["status"],
                    "weather_data": json.loads(row["weather_data"]) if row["weather_data"] else None
                }
            return {}

    def get_statistics_summary(self) -> Dict[str, Any]:
        """获取统计汇总"""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT task_name, metric_name, metric_value
                FROM task_statistics
                WHERE session_id = ?
            """, (self.session_id,)).fetchall()

            summary = {}
            for row in rows:
                task_name = row["task_name"]
                if task_name not in summary:
                    summary[task_name] = {}
                summary[task_name][row["metric_name"]] = json.loads(row["metric_value"])

            return summary

    def get_sample_images(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取样例图像（检测到目标的前N张）"""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT image_name, image_path, detection_results, processed_image_path
                FROM image_results
                WHERE session_id = ? AND has_target = 1
                ORDER BY processed_at
                LIMIT ?
            """, (self.session_id, limit)).fetchall()

            samples = []
            for row in rows:
                samples.append({
                    "image_name": row["image_name"],
                    "image_path": row["image_path"],
                    "detection_results": json.loads(row["detection_results"]),
                    "processed_image_path": row["processed_image_path"]
                })

            return samples

    def get_all_results(self) -> List[Dict[str, Any]]:
        """获取所有检测结果"""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM image_results WHERE session_id = ?
                ORDER BY processed_at
            """, (self.session_id,)).fetchall()

            results = []
            for row in rows:
                results.append({
                    "image_name": row["image_name"],
                    "image_path": row["image_path"],
                    "detection_results": json.loads(row["detection_results"]),
                    "has_target": bool(row["has_target"]),
                    "processed_image_path": row["processed_image_path"],
                    "processed_at": row["processed_at"]
                })

            return results

    def save_model_call(
        self,
        image_name: str,
        call_type: str,
        model_name: str,
        prompt: str,
        image_path: str,
        raw_response: str,
        parsed_result: Any,
        success: bool,
        error_message: str = None,
        latency_ms: int = None
    ):
        """保存模型调用记录"""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO model_calls
                (session_id, image_name, call_type, model_name, prompt, image_path,
                 raw_response, parsed_result, success, error_message, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.session_id,
                image_name,
                call_type,
                model_name,
                prompt,
                image_path,
                raw_response,
                json.dumps(parsed_result, ensure_ascii=False) if parsed_result else None,
                success,
                error_message,
                latency_ms
            ))

    def get_model_calls(self, image_name: str = None) -> List[Dict[str, Any]]:
        """获取模型调用记录"""
        with self._get_connection() as conn:
            if image_name:
                rows = conn.execute("""
                    SELECT * FROM model_calls
                    WHERE session_id = ? AND image_name = ?
                    ORDER BY created_at
                """, (self.session_id, image_name)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM model_calls
                    WHERE session_id = ?
                    ORDER BY created_at
                """, (self.session_id,)).fetchall()

            calls = []
            for row in rows:
                calls.append({
                    "id": row["id"],
                    "image_name": row["image_name"],
                    "call_type": row["call_type"],
                    "model_name": row["model_name"],
                    "prompt": row["prompt"],
                    "image_path": row["image_path"],
                    "raw_response": row["raw_response"],
                    "parsed_result": json.loads(row["parsed_result"]) if row["parsed_result"] else None,
                    "success": bool(row["success"]),
                    "error_message": row["error_message"],
                    "latency_ms": row["latency_ms"],
                    "created_at": row["created_at"]
                })

            return calls
