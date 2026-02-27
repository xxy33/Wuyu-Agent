"""
Node definitions for StateGraph workflow engine.
Provides Node class, decorators, and special node constants.
"""
from dataclasses import dataclass, field
from typing import (
    TypedDict, Dict, Any, Optional, Callable, Awaitable, Union,
    TypeVar
)
from enum import Enum
from datetime import datetime
import asyncio
import functools
import inspect


class NodeStatus(Enum):
    """Node execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class NodeConfig:
    """
    Configuration for a node.

    Attributes:
        name: Node name (required)
        description: Node description
        retry_count: Number of retries on failure
        retry_delay: Delay between retries in seconds
        timeout: Execution timeout in seconds
        metadata: Additional metadata
    """
    name: str
    description: str = ""
    retry_count: int = 0
    retry_delay: float = 1.0
    timeout: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeResult:
    """
    Result of node execution.

    Attributes:
        node_name: Name of the node
        status: Execution status
        state_updates: Updates to apply to state
        start_time: Execution start time
        end_time: Execution end time
        error: Error message if failed
        retry_count: Number of retries attempted
        metadata: Additional execution metadata
    """
    node_name: str
    status: NodeStatus
    state_updates: Dict[str, Any] = field(default_factory=dict)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    error: Optional[str] = None
    retry_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> Optional[float]:
        """Get execution duration in seconds."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None

    @property
    def success(self) -> bool:
        """Check if execution was successful."""
        return self.status == NodeStatus.COMPLETED

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "node_name": self.node_name,
            "status": self.status.value,
            "state_updates": self.state_updates,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration": self.duration,
            "error": self.error,
            "retry_count": self.retry_count,
            "metadata": self.metadata
        }


# Type for node functions
StateDict = Dict[str, Any]
NodeFunc = Callable[[StateDict], Awaitable[Dict[str, Any]]]
SyncNodeFunc = Callable[[StateDict], Dict[str, Any]]


class Node:
    """
    A node in the state graph.

    Wraps a function with configuration and execution logic.
    Supports both sync and async functions.
    """

    def __init__(
        self,
        func: Union[NodeFunc, SyncNodeFunc],
        config: Optional[NodeConfig] = None,
        name: Optional[str] = None
    ):
        """
        Initialize node.

        Args:
            func: The function to execute
            config: Node configuration
            name: Node name (overrides config name if provided)
        """
        self._func = func
        self._is_async = asyncio.iscoroutinefunction(func)

        # Determine name
        if name:
            node_name = name
        elif config and config.name:
            node_name = config.name
        else:
            node_name = func.__name__

        # Create or update config
        if config:
            self._config = NodeConfig(
                name=name or config.name,
                description=config.description,
                retry_count=config.retry_count,
                retry_delay=config.retry_delay,
                timeout=config.timeout,
                metadata=config.metadata.copy()
            )
        else:
            self._config = NodeConfig(name=node_name)

        # Copy function metadata
        functools.update_wrapper(self, func)

    @property
    def name(self) -> str:
        """Get node name."""
        return self._config.name

    @property
    def config(self) -> NodeConfig:
        """Get node configuration."""
        return self._config

    @property
    def is_async(self) -> bool:
        """Check if node function is async."""
        return self._is_async

    async def execute(self, state: StateDict) -> NodeResult:
        """
        Execute the node.

        Args:
            state: Current state dictionary

        Returns:
            NodeResult with execution details
        """
        result = NodeResult(
            node_name=self.name,
            status=NodeStatus.RUNNING,
            start_time=datetime.now()
        )

        retry_count = 0
        last_error: Optional[Exception] = None

        while retry_count <= self._config.retry_count:
            try:
                result.retry_count = retry_count

                # Execute the function
                if self._config.timeout:
                    state_updates = await asyncio.wait_for(
                        self._execute_func(state),
                        timeout=self._config.timeout
                    )
                else:
                    state_updates = await self._execute_func(state)

                # Handle None return
                if state_updates is None:
                    state_updates = {}

                result.state_updates = state_updates
                result.status = NodeStatus.COMPLETED
                result.end_time = datetime.now()
                return result

            except asyncio.TimeoutError:
                last_error = TimeoutError(f"Timeout after {self._config.timeout}s")
                retry_count += 1

            except Exception as e:
                last_error = e
                retry_count += 1

            # Wait before retry
            if retry_count <= self._config.retry_count:
                await asyncio.sleep(self._config.retry_delay)

        # All retries exhausted
        result.status = NodeStatus.FAILED
        result.end_time = datetime.now()
        result.retry_count = retry_count - 1
        if last_error:
            result.error = str(last_error)

        return result

    async def _execute_func(self, state: StateDict) -> Dict[str, Any]:
        """Execute the wrapped function."""
        if self._is_async:
            return await self._func(state)
        else:
            # Run sync function in thread pool
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._func, state)

    def __call__(self, state: StateDict) -> Awaitable[NodeResult]:
        """Allow calling node directly."""
        return self.execute(state)

    def __repr__(self) -> str:
        return f"<Node(name={self.name}, async={self._is_async})>"


def node(
    name: Optional[str] = None,
    description: str = "",
    retry_count: int = 0,
    retry_delay: float = 1.0,
    timeout: Optional[float] = None,
    **metadata
) -> Callable[[Union[NodeFunc, SyncNodeFunc]], Node]:
    """
    Decorator to create a node from a function.

    Args:
        name: Node name (defaults to function name)
        description: Node description
        retry_count: Number of retries on failure
        retry_delay: Delay between retries in seconds
        timeout: Execution timeout in seconds
        **metadata: Additional metadata

    Returns:
        Decorator function

    Example:
        @node(name="process", retry_count=3)
        async def process(state: dict) -> dict:
            return {"result": state["input"].upper()}
    """
    def decorator(func: Union[NodeFunc, SyncNodeFunc]) -> Node:
        config = NodeConfig(
            name=name or func.__name__,
            description=description,
            retry_count=retry_count,
            retry_delay=retry_delay,
            timeout=timeout,
            metadata=metadata
        )
        return Node(func, config=config)

    return decorator


class _SpecialNode:
    """Special node marker for START and END."""

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"<{self._name}>"

    def __eq__(self, other) -> bool:
        if isinstance(other, _SpecialNode):
            return self._name == other._name
        if isinstance(other, str):
            return self._name == other
        return False

    def __hash__(self) -> int:
        return hash(self._name)


# Special node constants
START = _SpecialNode("__START__")
END = _SpecialNode("__END__")


def is_special_node(node: Any) -> bool:
    """Check if a node is a special node (START or END)."""
    return isinstance(node, _SpecialNode)


def get_node_name(node: Union[Node, _SpecialNode, str]) -> str:
    """Get the name of a node (handles all node types)."""
    if isinstance(node, str):
        return node
    return node.name
