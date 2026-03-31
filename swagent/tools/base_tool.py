"""
工具基类
定义所有工具的通用接口和规范
"""
from abc import ABC, abstractmethod
from typing import Callable, Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum


class ToolCategory(Enum):
    """工具类别"""
    COMPUTATION = "computation"     # 计算类
    DATA = "data"                   # 数据处理类
    FILE = "file"                   # 文件操作类
    WEB = "web"                     # 网络类
    CODE = "code"                   # 代码类
    VISUALIZATION = "visualization" # 可视化类
    DOMAIN = "domain"               # 领域专用类


@dataclass
class ToolParameter:
    """工具参数定义"""
    name: str
    type: str  # string, number, boolean, array, object
    description: str
    required: bool = True
    default: Any = None
    enum: Optional[List[Any]] = None
    items: Optional[Dict[str, Any]] = None  # 用于数组类型


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    data: Any
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
        }


@dataclass
class ToolSchema:
    """
    工具模式定义 (用于LLM理解)
    兼容OpenAI Function Calling格式
    """
    name: str
    description: str
    category: ToolCategory
    parameters: List[ToolParameter]
    returns: str
    examples: List[Dict[str, Any]] = field(default_factory=list)

    def to_openai_function(self) -> Dict[str, Any]:
        """
        转换为OpenAI Function Calling格式

        Returns:
            符合OpenAI API的function定义
        """
        properties = {}
        required = []

        for param in self.parameters:
            prop = {
                "type": param.type,
                "description": param.description
            }

            if param.enum:
                prop["enum"] = param.enum

            if param.items:
                prop["items"] = param.items

            properties[param.name] = prop

            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }

    def to_mcp_tool(self) -> Dict[str, Any]:
        """
        转换为MCP (Model Context Protocol)格式

        Returns:
            符合MCP协议的工具定义
        """
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "inputSchema": {
                "type": "object",
                "properties": {
                    param.name: {
                        "type": param.type,
                        "description": param.description,
                        **({"enum": param.enum} if param.enum else {}),
                        **({"default": param.default} if param.default is not None else {})
                    }
                    for param in self.parameters
                },
                "required": [p.name for p in self.parameters if p.required]
            },
            "returns": self.returns,
            "examples": self.examples
        }


class BaseTool(ABC):
    """
    工具基类

    所有工具都需要继承此类并实现必要的方法
    支持OpenAI Function Calling和MCP两种调用方式
    """

    def __init__(self):
        self._schema: Optional[ToolSchema] = None

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称（唯一标识）"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述（给LLM看的）"""
        pass

    @property
    @abstractmethod
    def category(self) -> ToolCategory:
        """工具类别"""
        pass

    @property
    def is_read_only(self) -> bool:
        """是否为只读工具（用于并发控制，只读工具可并发执行）"""
        return False

    @property
    def is_destructive(self) -> bool:
        """是否为破坏性工具（如删除文件、写入数据库等）"""
        return False

    @property
    def timeout(self) -> float:
        """工具执行超时秒数"""
        return 60.0

    @property
    def max_result_size(self) -> int:
        """结果最大字符数（超过时截断），用于 result budget 控制"""
        return 50000

    @property
    def schema(self) -> ToolSchema:
        """获取工具模式"""
        if self._schema is None:
            self._schema = ToolSchema(
                name=self.name,
                description=self.description,
                category=self.category,
                parameters=self.get_parameters(),
                returns=self.get_return_description(),
                examples=self.get_examples()
            )
        return self._schema

    @abstractmethod
    def get_parameters(self) -> List[ToolParameter]:
        """获取参数定义"""
        pass

    @abstractmethod
    def get_return_description(self) -> str:
        """获取返回值描述"""
        pass

    def get_examples(self) -> List[Dict[str, Any]]:
        """获取使用示例（可选）"""
        return []

    @abstractmethod
    async def execute(
        self,
        on_progress: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> ToolResult:
        """
        执行工具

        Args:
            on_progress: 可选的进度回调函数，接收进度描述字符串
            **kwargs: 工具参数

        Returns:
            ToolResult: 执行结果
        """
        pass

    def validate_parameters(self, **kwargs) -> Optional[str]:
        """
        验证参数

        Returns:
            错误信息，None表示验证通过
        """
        for param in self.get_parameters():
            # 检查必需参数
            if param.required and param.name not in kwargs:
                return f"Missing required parameter: {param.name}"

            # 检查枚举值
            if param.enum and param.name in kwargs:
                if kwargs[param.name] not in param.enum:
                    return f"Invalid value for {param.name}. Must be one of: {param.enum}"

        return None

    async def safe_execute(self, **kwargs) -> ToolResult:
        """
        安全执行 (带参数验证和异常处理)
        """
        # 验证参数
        error = self.validate_parameters(**kwargs)
        if error:
            return ToolResult(success=False, data=None, error=error)

        # 执行
        try:
            return await self.execute(**kwargs)
        except Exception as e:
            return ToolResult(
                success=False,
                data=None,
                error=f"Tool execution failed: {str(e)}"
            )

    def to_openai_function(self) -> Dict[str, Any]:
        """转换为OpenAI Function格式"""
        return self.schema.to_openai_function()

    def to_mcp_tool(self) -> Dict[str, Any]:
        """转换为MCP工具格式"""
        return self.schema.to_mcp_tool()

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name}, category={self.category.value})>"
