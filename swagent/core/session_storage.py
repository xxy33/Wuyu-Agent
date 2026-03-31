"""
会话持久化 - 完整的对话记录与恢复
参考 Claude Code sessionStorage.ts 设计

存储结构:
    ~/.swagent/sessions/{session_id}/
    ├── main.jsonl                      # 主对话记录
    ├── subagents/
    │   └── {agent_id}.jsonl            # 子 Agent 对话
    ├── tool-results/
    │   └── {tool_use_id}.json          # 持久化的大结果
    └── metadata.json                   # 会话元数据
"""
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SessionMetadata:
    """会话元数据"""
    session_id: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    message_count: int = 0
    agent_ids: List[str] = field(default_factory=list)
    domain: str = ""
    description: str = ""


class SessionStorage:
    """
    会话持久化管理器

    用法:
        storage = SessionStorage()
        storage.record_message(session_id, {"role": "user", "content": "..."})
        messages = storage.load_session(session_id)
    """

    def __init__(self, base_dir: Optional[str] = None):
        """
        Args:
            base_dir: 存储根目录 (默认 ~/.swagent/sessions/)
        """
        if base_dir is None:
            base_dir = os.path.join(os.path.expanduser("~"), ".swagent", "sessions")
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _session_dir(self, session_id: str) -> str:
        """获取会话目录路径"""
        return os.path.join(self.base_dir, session_id)

    def _ensure_session_dir(self, session_id: str) -> str:
        """确保会话目录存在"""
        d = self._session_dir(session_id)
        os.makedirs(d, exist_ok=True)
        os.makedirs(os.path.join(d, "subagents"), exist_ok=True)
        os.makedirs(os.path.join(d, "tool-results"), exist_ok=True)
        return d

    def record_message(self, session_id: str, message: Dict[str, Any]) -> None:
        """
        记录主对话消息

        Args:
            session_id: 会话 ID
            message: 消息字典 (OpenAI 格式)
        """
        d = self._ensure_session_dir(session_id)
        filepath = os.path.join(d, "main.jsonl")

        entry = {
            **message,
            "_timestamp": time.time(),
            "_session_id": session_id,
        }

        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

        self._update_metadata(session_id, increment_messages=1)

    def record_subagent_message(
        self, session_id: str, agent_id: str, message: Dict[str, Any]
    ) -> None:
        """记录子 Agent 对话消息"""
        d = self._ensure_session_dir(session_id)
        filepath = os.path.join(d, "subagents", f"{agent_id}.jsonl")

        entry = {
            **message,
            "_timestamp": time.time(),
            "_agent_id": agent_id,
        }

        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

        # 记录 agent_id
        meta = self._load_metadata(session_id)
        if meta and agent_id not in meta.agent_ids:
            meta.agent_ids.append(agent_id)
            self._save_metadata(session_id, meta)

    def load_session(self, session_id: str) -> List[Dict[str, Any]]:
        """
        加载会话的所有消息

        Returns:
            消息列表 (按时间排序)
        """
        filepath = os.path.join(self._session_dir(session_id), "main.jsonl")
        if not os.path.exists(filepath):
            return []

        messages = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return messages

    def load_subagent_session(self, session_id: str, agent_id: str) -> List[Dict[str, Any]]:
        """加载子 Agent 的对话记录"""
        filepath = os.path.join(self._session_dir(session_id), "subagents", f"{agent_id}.jsonl")
        if not os.path.exists(filepath):
            return []

        messages = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return messages

    def list_sessions(self) -> List[SessionMetadata]:
        """
        列出所有会话

        Returns:
            会话元数据列表 (按更新时间倒序)
        """
        sessions = []
        if not os.path.exists(self.base_dir):
            return sessions

        for name in os.listdir(self.base_dir):
            session_dir = os.path.join(self.base_dir, name)
            if os.path.isdir(session_dir):
                meta = self._load_metadata(name)
                if meta:
                    sessions.append(meta)
                else:
                    # 从文件系统推断
                    sessions.append(SessionMetadata(
                        session_id=name,
                        created_at=datetime.fromtimestamp(
                            os.path.getctime(session_dir)
                        ).isoformat(),
                    ))

        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def export_session(self, session_id: str, format: str = "json") -> str:
        """
        导出会话

        Args:
            session_id: 会话 ID
            format: 导出格式 ("json" 或 "markdown")

        Returns:
            格式化的会话内容
        """
        messages = self.load_session(session_id)

        if format == "markdown":
            lines = [f"# 会话记录: {session_id}\n"]
            for msg in messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                ts = msg.get("_timestamp", "")
                if ts:
                    ts = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"## [{role}] {ts}\n\n{content}\n")
            return "\n".join(lines)
        else:
            return json.dumps(messages, ensure_ascii=False, indent=2, default=str)

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        import shutil
        d = self._session_dir(session_id)
        if os.path.exists(d):
            shutil.rmtree(d)
            return True
        return False

    def _update_metadata(self, session_id: str, increment_messages: int = 0):
        """更新会话元数据"""
        meta = self._load_metadata(session_id)
        if meta is None:
            meta = SessionMetadata(session_id=session_id)
        meta.updated_at = datetime.now().isoformat()
        meta.message_count += increment_messages
        self._save_metadata(session_id, meta)

    def _load_metadata(self, session_id: str) -> Optional[SessionMetadata]:
        """加载元数据"""
        filepath = os.path.join(self._session_dir(session_id), "metadata.json")
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return SessionMetadata(**data)
        except Exception:
            return None

    def _save_metadata(self, session_id: str, meta: SessionMetadata):
        """保存元数据"""
        d = self._ensure_session_dir(session_id)
        filepath = os.path.join(d, "metadata.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(asdict(meta), f, ensure_ascii=False, indent=2)
