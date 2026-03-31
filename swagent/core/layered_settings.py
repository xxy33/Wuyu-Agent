"""
分层配置系统 - 多源配置合并
参考 Claude Code settings/ 设计

优先级 (高 → 低):
1. policy_settings  - 管理员强制策略 (国企 IT 管理员)
2. env_settings     - 环境变量覆盖
3. local_settings   - 本地项目配置 (.swagent/local.yaml, gitignored)
4. project_settings - 项目共享配置 (.swagent/config.yaml)
5. user_settings    - 用户全局配置 (~/.swagent/config.yaml)
6. default_settings - 框架内置默认值
"""
import os
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

from swagent.utils.logger import get_logger

logger = get_logger(__name__)


class SettingSource(IntEnum):
    """配置源 (数值越大优先级越高)"""
    DEFAULT = 0
    USER = 1
    PROJECT = 2
    LOCAL = 3
    ENV = 4
    POLICY = 5


# 各源对应的文件路径
SOURCE_PATHS = {
    SettingSource.USER: os.path.join(os.path.expanduser("~"), ".swagent", "config.yaml"),
    SettingSource.PROJECT: os.path.join(".swagent", "config.yaml"),
    SettingSource.LOCAL: os.path.join(".swagent", "local.yaml"),
    SettingSource.POLICY: os.path.join(os.path.expanduser("~"), ".swagent", "policy.yaml"),
}


def _deep_merge(base: Dict, override: Dict) -> Dict:
    """
    深度合并字典 (override 覆盖 base)

    嵌套字典递归合并，其他类型直接覆盖。
    """
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml_file(path: str) -> Dict[str, Any]:
    """安全加载 YAML 文件"""
    if not os.path.exists(path):
        return {}
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except ImportError:
        logger.warning("yaml 模块未安装，跳过配置文件加载")
        return {}
    except Exception as e:
        logger.warning(f"加载配置文件失败 {path}: {e}")
        return {}


def _get_env_settings(prefix: str = "SWAGENT_") -> Dict[str, Any]:
    """从环境变量提取配置 (SWAGENT_XX_YY → xx.yy)"""
    settings: Dict[str, Any] = {}
    for key, value in os.environ.items():
        if key.startswith(prefix):
            # SWAGENT_LLM_MODEL → llm.model
            config_key = key[len(prefix):].lower().replace("__", ".")
            settings[config_key] = value
    return settings


class LayeredSettings:
    """
    分层配置管理器

    多源配置按优先级合并，支持深度嵌套。

    用法:
        settings = LayeredSettings()
        settings.set_defaults({"llm": {"model": "gpt-4", "temperature": 0.7}})
        settings.load()

        model = settings.get("llm.model")  # → 最高优先级源的值
        model, source = settings.get_with_source("llm.model")  # → (值, 来源)
    """

    def __init__(self, project_dir: str = "."):
        """
        Args:
            project_dir: 项目根目录 (用于查找 project/local settings)
        """
        self.project_dir = os.path.abspath(project_dir)
        self._layers: Dict[SettingSource, Dict[str, Any]] = {
            source: {} for source in SettingSource
        }
        self._merged: Dict[str, Any] = {}

    def set_defaults(self, defaults: Dict[str, Any]) -> None:
        """设置框架默认值"""
        self._layers[SettingSource.DEFAULT] = defaults
        self._rebuild_merged()

    def load(self) -> None:
        """从所有源加载配置"""
        # 用户全局配置
        self._layers[SettingSource.USER] = _load_yaml_file(
            SOURCE_PATHS[SettingSource.USER]
        )

        # 项目配置
        self._layers[SettingSource.PROJECT] = _load_yaml_file(
            os.path.join(self.project_dir, ".swagent", "config.yaml")
        )

        # 本地配置 (gitignored)
        self._layers[SettingSource.LOCAL] = _load_yaml_file(
            os.path.join(self.project_dir, ".swagent", "local.yaml")
        )

        # 策略配置
        self._layers[SettingSource.POLICY] = _load_yaml_file(
            SOURCE_PATHS[SettingSource.POLICY]
        )

        # 环境变量
        self._layers[SettingSource.ENV] = _get_env_settings()

        self._rebuild_merged()
        logger.info(f"分层配置已加载 ({sum(1 for v in self._layers.values() if v)} 个源)")

    def _rebuild_merged(self) -> None:
        """重建合并配置"""
        result: Dict[str, Any] = {}
        for source in sorted(SettingSource):
            layer = self._layers.get(source, {})
            if layer:
                result = _deep_merge(result, layer)
        self._merged = result

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值 (点号分隔的键路径)

        Args:
            key: 键路径, 如 "llm.model" 或 "tools.enabled"
            default: 默认值

        Returns:
            最高优先级源提供的值
        """
        parts = key.split(".")
        current = self._merged
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def get_with_source(self, key: str) -> Tuple[Any, Optional[SettingSource]]:
        """
        获取配置值及其来源

        Returns:
            (值, 来源) 元组; 未找到时返回 (None, None)
        """
        parts = key.split(".")

        # 从高优先级到低优先级查找
        for source in sorted(SettingSource, reverse=True):
            current = self._layers.get(source, {})
            found = True
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    found = False
                    break
            if found:
                return current, source

        return None, None

    def set(self, key: str, value: Any, source: SettingSource = SettingSource.LOCAL) -> None:
        """
        设置配置值

        Args:
            key: 键路径
            value: 值
            source: 写入的配置源
        """
        parts = key.split(".")
        layer = self._layers.setdefault(source, {})

        current = layer
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

        self._rebuild_merged()

    def reload(self) -> None:
        """重新加载所有配置"""
        self.load()

    @property
    def all_settings(self) -> Dict[str, Any]:
        """获取完整的合并配置"""
        return dict(self._merged)

    def get_layer(self, source: SettingSource) -> Dict[str, Any]:
        """获取指定源的原始配置"""
        return dict(self._layers.get(source, {}))

    def __repr__(self) -> str:
        sources = [s.name for s in SettingSource if self._layers.get(s)]
        return f"<LayeredSettings sources={sources}>"
