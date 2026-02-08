"""
StateGraph core implementation for workflow engine.
Provides StateGraph builder and CompiledGraph executor.
"""
from dataclasses import dataclass, field
from typing import (
    TypedDict, Dict, Any, Optional, Callable, Awaitable, Union, List,
    Type, Set, AsyncIterator, Tuple
)
from enum import Enum
from datetime import datetime
from copy import deepcopy
import asyncio
import uuid

from swagent.stategraph.state import StateManager, StateSnapshot, MergeStrategy
from swagent.stategraph.node import (
    Node, NodeConfig, NodeResult, NodeStatus,
    node as node_decorator, START, END, get_node_name, is_special_node, _SpecialNode
)
from swagent.stategraph.edge import (
    Edge, EdgeType, EdgeCollection,
    edge as edge_factory, conditional_edge, parallel_edge
)


class GraphStatus(Enum):
    """Status of graph execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass
class ExecutionConfig:
    """
    Configuration for graph execution.

    Attributes:
        max_iterations: Maximum iterations to prevent infinite loops
        save_checkpoints: Whether to save state checkpoints
        interrupt_before: Nodes to pause before executing
        interrupt_after: Nodes to pause after executing
        recursion_limit: Limit for nested graph calls
        timeout: Global execution timeout in seconds
    """
    max_iterations: int = 100
    save_checkpoints: bool = True
    interrupt_before: Set[str] = field(default_factory=set)
    interrupt_after: Set[str] = field(default_factory=set)
    recursion_limit: int = 25
    timeout: Optional[float] = None


@dataclass
class ExecutionResult:
    """
    Result of graph execution.

    Attributes:
        status: Final execution status
        state: Final state
        history: Execution history (node results)
        execution_id: Unique execution identifier
        start_time: Execution start time
        end_time: Execution end time
        iterations: Number of iterations executed
        error: Error message if failed
    """
    status: GraphStatus
    state: Dict[str, Any]
    history: List[NodeResult] = field(default_factory=list)
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    iterations: int = 0
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        """Check if execution was successful."""
        return self.status == GraphStatus.COMPLETED

    @property
    def duration(self) -> Optional[float]:
        """Get execution duration in seconds."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "status": self.status.value,
            "state": self.state,
            "history": [r.to_dict() for r in self.history],
            "execution_id": self.execution_id,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration": self.duration,
            "iterations": self.iterations,
            "error": self.error
        }


@dataclass
class StreamEvent:
    """Event emitted during streaming execution."""
    event_type: str  # "node_start", "node_end", "state_update", "error"
    node_name: Optional[str]
    state: Dict[str, Any]
    result: Optional[NodeResult] = None
    timestamp: datetime = field(default_factory=datetime.now)


class StateGraph:
    """
    Builder class for constructing state graphs.

    Example:
        graph = StateGraph(MyState)
        graph.add_node("process", process_func)
        graph.add_edge(START, "process")
        graph.add_edge("process", END)
        app = graph.compile()
        result = await app.invoke(initial_state)
    """

    def __init__(
        self,
        state_type: Optional[Type[TypedDict]] = None,
        name: str = "StateGraph"
    ):
        """
        Initialize state graph.

        Args:
            state_type: TypedDict class defining state schema
            name: Graph name for identification
        """
        self.state_type = state_type
        self.name = name
        self._nodes: Dict[str, Node] = {}
        self._edges = EdgeCollection()
        self._entry_point: Optional[str] = None
        self._exit_points: Set[str] = set()
        self._merge_strategies: Dict[str, MergeStrategy] = {}

    def add_node(
        self,
        name: str,
        func: Optional[Callable] = None,
        **config_kwargs
    ) -> "StateGraph":
        """
        Add a node to the graph.

        Args:
            name: Node name
            func: Node function (optional if using as decorator)
            **config_kwargs: Node configuration options

        Returns:
            Self for chaining

        Example:
            # Direct function
            graph.add_node("process", process_func)

            # With configuration
            graph.add_node("retry_node", my_func, retry_count=3)
        """
        if func is not None:
            if isinstance(func, Node):
                # Already a Node object
                self._nodes[name] = Node(func._func, func._config, name=name)
            else:
                # Create Node from function
                config = NodeConfig(name=name, **config_kwargs)
                self._nodes[name] = Node(func, config=config)
        return self

    def node(
        self,
        name: Optional[str] = None,
        **config_kwargs
    ) -> Callable:
        """
        Decorator to add a node to the graph.

        Args:
            name: Node name (defaults to function name)
            **config_kwargs: Node configuration options

        Returns:
            Decorator function

        Example:
            @graph.node()
            async def process(state: dict) -> dict:
                return {"result": state["input"].upper()}

            @graph.node(name="custom_name", retry_count=3)
            async def my_func(state: dict) -> dict:
                return {}
        """
        def decorator(func: Callable) -> Node:
            node_name = name or func.__name__
            config = NodeConfig(name=node_name, **config_kwargs)
            node_obj = Node(func, config=config)
            self._nodes[node_name] = node_obj
            return node_obj

        return decorator

    def add_edge(
        self,
        source: Union[str, Node, _SpecialNode],
        target: Union[str, Node, _SpecialNode]
    ) -> "StateGraph":
        """
        Add a fixed edge between nodes.

        Args:
            source: Source node
            target: Target node

        Returns:
            Self for chaining
        """
        e = edge_factory(source, target)
        self._edges.add(e)
        return self

    def add_conditional_edge(
        self,
        source: Union[str, Node, _SpecialNode],
        condition: Callable,
        target_map: Dict[str, Union[str, Node, _SpecialNode]],
        default: Optional[Union[str, Node, _SpecialNode]] = None
    ) -> "StateGraph":
        """
        Add a conditional edge that routes based on state.

        Args:
            source: Source node
            condition: Function that returns target key
            target_map: Mapping of condition results to targets
            default: Default target if no match

        Returns:
            Self for chaining
        """
        e = conditional_edge(source, condition, target_map, default)
        self._edges.add(e)
        return self

    def add_parallel_edge(
        self,
        source: Union[str, Node, _SpecialNode],
        targets: List[Union[str, Node, _SpecialNode]],
        converge_to: Optional[Union[str, Node, _SpecialNode]] = None
    ) -> "StateGraph":
        """
        Add a parallel edge that fans out to multiple nodes.

        Args:
            source: Source node
            targets: List of target nodes
            converge_to: Node to converge to after all parallel nodes complete.
                         If None, execution ends after parallel nodes finish.

        Returns:
            Self for chaining

        Example:
            graph.add_parallel_edge(
                "analyst",
                ["tech_eval", "env_eval", "econ_eval"],
                converge_to="synthesizer"
            )
        """
        e = parallel_edge(source, targets, converge_to=converge_to)
        self._edges.add(e)
        return self

    def set_entry_point(self, node_name: str) -> "StateGraph":
        """
        Set the entry point node.

        Equivalent to add_edge(START, node_name).

        Args:
            node_name: Name of entry node

        Returns:
            Self for chaining
        """
        self._entry_point = get_node_name(node_name)
        self.add_edge(START, node_name)
        return self

    def set_exit_point(self, node_name: str) -> "StateGraph":
        """
        Set an exit point node.

        Equivalent to add_edge(node_name, END).

        Args:
            node_name: Name of exit node

        Returns:
            Self for chaining
        """
        exit_name = get_node_name(node_name)
        self._exit_points.add(exit_name)
        self.add_edge(node_name, END)
        return self

    def set_merge_strategy(
        self,
        field: str,
        strategy: MergeStrategy
    ) -> "StateGraph":
        """
        Set merge strategy for a state field.

        Args:
            field: State field name
            strategy: Merge strategy

        Returns:
            Self for chaining
        """
        self._merge_strategies[field] = strategy
        return self

    def validate(self) -> List[str]:
        """
        Validate the graph structure.

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        # Check for nodes
        if not self._nodes:
            errors.append("Graph has no nodes")

        # Check for entry point
        entry_edges = self._edges.get_outgoing("__START__")
        if not entry_edges:
            errors.append("Graph has no entry point (no edge from START)")

        # Validate edges reference valid nodes
        node_names = set(self._nodes.keys())
        edge_errors = self._edges.validate(node_names)
        errors.extend(edge_errors)

        # Check all nodes are reachable from START
        reachable = self._get_reachable_nodes("__START__")
        for node_name in self._nodes:
            if node_name not in reachable:
                errors.append(f"Node '{node_name}' is not reachable from START")

        # Check all nodes can reach END (or have no outgoing edges)
        for node_name in self._nodes:
            outgoing = self._edges.get_outgoing(node_name)
            if not outgoing:
                # Node has no outgoing edges - might be an implicit end
                pass  # This is okay, will be treated as END

        return errors

    def _get_reachable_nodes(self, start: str) -> Set[str]:
        """Get all nodes reachable from a starting point."""
        reachable = set()
        to_visit = [start]

        while to_visit:
            current = to_visit.pop()
            if current in reachable:
                continue
            reachable.add(current)

            for edge in self._edges.get_outgoing(current):
                for target in edge.targets:
                    if target not in reachable:
                        to_visit.append(target)

        return reachable

    def compile(
        self,
        config: Optional[ExecutionConfig] = None
    ) -> "CompiledGraph":
        """
        Compile the graph for execution.

        Args:
            config: Execution configuration

        Returns:
            CompiledGraph instance

        Raises:
            ValueError: If graph validation fails
        """
        errors = self.validate()
        if errors:
            raise ValueError(f"Graph validation failed: {errors}")

        return CompiledGraph(
            state_type=self.state_type,
            nodes=self._nodes.copy(),
            edges=self._edges,
            merge_strategies=self._merge_strategies.copy(),
            config=config or ExecutionConfig(),
            name=self.name
        )

    def visualize(self) -> str:
        """
        Generate a text visualization of the graph.

        Returns:
            Text representation of the graph
        """
        lines = [f"Graph: {self.name}", "=" * 40]

        lines.append("\nNodes:")
        for name, node in self._nodes.items():
            lines.append(f"  - {name}")

        lines.append("\nEdges:")
        for edge in self._edges:
            if edge.edge_type == EdgeType.FIXED:
                lines.append(f"  {edge.source} -> {edge.target}")
            elif edge.edge_type == EdgeType.PARALLEL:
                converge = f" -> {edge.converge_to}" if edge.converge_to else ""
                lines.append(f"  {edge.source} -> [{', '.join(edge.target)}]{converge}")
            else:
                lines.append(f"  {edge.source} -> {edge.targets} (conditional)")

        return "\n".join(lines)


class CompiledGraph:
    """
    Compiled graph ready for execution.

    Created by StateGraph.compile().
    """

    def __init__(
        self,
        state_type: Optional[Type[TypedDict]],
        nodes: Dict[str, Node],
        edges: EdgeCollection,
        merge_strategies: Dict[str, MergeStrategy],
        config: ExecutionConfig,
        name: str = "CompiledGraph"
    ):
        self.state_type = state_type
        self._nodes = nodes
        self._edges = edges
        self._merge_strategies = merge_strategies
        self._config = config
        self.name = name

    async def invoke(
        self,
        initial_state: Dict[str, Any],
        config: Optional[ExecutionConfig] = None
    ) -> ExecutionResult:
        """
        Execute the graph synchronously (complete execution).

        Args:
            initial_state: Initial state dictionary
            config: Override execution configuration

        Returns:
            ExecutionResult with final state
        """
        exec_config = config or self._config

        # Initialize state manager
        state_manager = StateManager(
            state_type=self.state_type,
            initial_state=initial_state,
            default_merge_strategy=MergeStrategy.OVERWRITE
        )

        # Apply merge strategies
        for field, strategy in self._merge_strategies.items():
            state_manager.set_merge_strategy(field, strategy)

        # Create result
        result = ExecutionResult(
            status=GraphStatus.RUNNING,
            state={},
            start_time=datetime.now()
        )

        try:
            # Execute with timeout if configured
            if exec_config.timeout:
                await asyncio.wait_for(
                    self._execute(state_manager, result, exec_config),
                    timeout=exec_config.timeout
                )
            else:
                await self._execute(state_manager, result, exec_config)

            # Only set COMPLETED if not already INTERRUPTED
            if result.status != GraphStatus.INTERRUPTED:
                result.status = GraphStatus.COMPLETED
            result.state = state_manager.state

        except asyncio.TimeoutError:
            result.status = GraphStatus.FAILED
            result.error = f"Execution timed out after {exec_config.timeout}s"
            result.state = state_manager.state

        except Exception as e:
            result.status = GraphStatus.FAILED
            result.error = str(e)
            result.state = state_manager.state

        result.end_time = datetime.now()
        return result

    async def _execute(
        self,
        state_manager: StateManager,
        result: ExecutionResult,
        config: ExecutionConfig
    ) -> None:
        """Execute the graph."""
        current_node = "__START__"
        iterations = 0

        while iterations < config.max_iterations:
            iterations += 1
            result.iterations = iterations

            # Get outgoing edges
            outgoing = self._edges.get_outgoing(current_node)

            if not outgoing:
                # No outgoing edges - end execution
                break

            # For simplicity, take first edge (TODO: handle multiple)
            edge = outgoing[0]

            # Resolve target(s)
            target = await edge.resolve_target(state_manager.state)

            # Handle parallel execution
            if edge.edge_type == EdgeType.PARALLEL:
                await self._execute_parallel(
                    targets=target,
                    state_manager=state_manager,
                    result=result,
                    config=config
                )
                # Converge to next node if specified
                if edge.converge_to:
                    current_node = edge.converge_to
                    # Execute the convergence node
                    if current_node == "__END__":
                        break
                    if current_node in self._nodes:
                        node = self._nodes[current_node]
                        if current_node in config.interrupt_before:
                            result.status = GraphStatus.INTERRUPTED
                            return
                        if config.save_checkpoints:
                            state_manager.save_snapshot(node_name=current_node)
                        node_result = await node.execute(state_manager.state)
                        result.history.append(node_result)
                        if node_result.success:
                            state_manager.update(node_result.state_updates)
                        else:
                            raise RuntimeError(
                                f"Node '{current_node}' failed: {node_result.error}"
                            )
                        if current_node in config.interrupt_after:
                            result.status = GraphStatus.INTERRUPTED
                            return
                    continue
                else:
                    break

            # Check for END
            if target == "__END__":
                break

            # Execute node
            if target in self._nodes:
                node = self._nodes[target]

                # Check interrupt_before
                if target in config.interrupt_before:
                    result.status = GraphStatus.INTERRUPTED
                    return

                # Save checkpoint
                if config.save_checkpoints:
                    state_manager.save_snapshot(node_name=target)

                # Execute
                node_result = await node.execute(state_manager.state)
                result.history.append(node_result)

                if node_result.success:
                    # Update state
                    state_manager.update(node_result.state_updates)
                else:
                    raise RuntimeError(
                        f"Node '{target}' failed: {node_result.error}"
                    )

                # Check interrupt_after
                if target in config.interrupt_after:
                    result.status = GraphStatus.INTERRUPTED
                    return

            current_node = target

        if iterations >= config.max_iterations:
            raise RuntimeError(
                f"Maximum iterations ({config.max_iterations}) exceeded"
            )

    async def _execute_parallel(
        self,
        targets: List[str],
        state_manager: StateManager,
        result: ExecutionResult,
        config: ExecutionConfig
    ) -> None:
        """Execute multiple nodes in parallel."""
        # Filter to actual nodes (not END)
        node_targets = [t for t in targets if t in self._nodes]

        if not node_targets:
            return

        # Create tasks for parallel execution
        async def execute_one(target: str) -> Tuple[str, NodeResult]:
            node = self._nodes[target]
            node_result = await node.execute(state_manager.state)
            return target, node_result

        # Execute all in parallel
        tasks = [execute_one(t) for t in node_targets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and merge state
        all_updates = {}
        for item in results:
            if isinstance(item, Exception):
                raise item
            target, node_result = item
            result.history.append(node_result)
            if node_result.success:
                all_updates.update(node_result.state_updates)
            else:
                raise RuntimeError(
                    f"Parallel node '{target}' failed: {node_result.error}"
                )

        # Apply all updates
        state_manager.update(all_updates)

    async def stream(
        self,
        initial_state: Dict[str, Any],
        config: Optional[ExecutionConfig] = None
    ) -> AsyncIterator[StreamEvent]:
        """
        Execute the graph with streaming events.

        Args:
            initial_state: Initial state dictionary
            config: Override execution configuration

        Yields:
            StreamEvent for each execution step
        """
        exec_config = config or self._config

        # Initialize state manager
        state_manager = StateManager(
            state_type=self.state_type,
            initial_state=initial_state,
            default_merge_strategy=MergeStrategy.OVERWRITE
        )

        for field, strategy in self._merge_strategies.items():
            state_manager.set_merge_strategy(field, strategy)

        current_node = "__START__"
        iterations = 0

        while iterations < exec_config.max_iterations:
            iterations += 1

            outgoing = self._edges.get_outgoing(current_node)
            if not outgoing:
                break

            edge = outgoing[0]
            target = await edge.resolve_target(state_manager.state)

            if isinstance(target, list):
                # Parallel - yield start for all, then results
                for t in target:
                    if t in self._nodes:
                        yield StreamEvent(
                            event_type="node_start",
                            node_name=t,
                            state=state_manager.state
                        )
                # Execute parallel
                parallel_result = ExecutionResult(
                    status=GraphStatus.RUNNING,
                    state={}
                )
                await self._execute_parallel(
                    targets=target,
                    state_manager=state_manager,
                    result=parallel_result,
                    config=exec_config
                )
                yield StreamEvent(
                    event_type="state_update",
                    node_name=None,
                    state=state_manager.state
                )
                # Converge to next node if specified
                if edge.converge_to:
                    current_node = edge.converge_to
                    if current_node == "__END__":
                        break
                    if current_node in self._nodes:
                        node = self._nodes[current_node]
                        yield StreamEvent(
                            event_type="node_start",
                            node_name=current_node,
                            state=state_manager.state
                        )
                        if exec_config.save_checkpoints:
                            state_manager.save_snapshot(node_name=current_node)
                        node_result = await node.execute(state_manager.state)
                        yield StreamEvent(
                            event_type="node_end",
                            node_name=current_node,
                            state=state_manager.state,
                            result=node_result
                        )
                        if node_result.success:
                            state_manager.update(node_result.state_updates)
                            yield StreamEvent(
                                event_type="state_update",
                                node_name=current_node,
                                state=state_manager.state
                            )
                        else:
                            yield StreamEvent(
                                event_type="error",
                                node_name=current_node,
                                state=state_manager.state,
                                result=node_result
                            )
                            return
                    continue
                else:
                    break

            if target == "__END__":
                break

            if target in self._nodes:
                node = self._nodes[target]

                yield StreamEvent(
                    event_type="node_start",
                    node_name=target,
                    state=state_manager.state
                )

                if exec_config.save_checkpoints:
                    state_manager.save_snapshot(node_name=target)

                node_result = await node.execute(state_manager.state)

                yield StreamEvent(
                    event_type="node_end",
                    node_name=target,
                    state=state_manager.state,
                    result=node_result
                )

                if node_result.success:
                    state_manager.update(node_result.state_updates)
                    yield StreamEvent(
                        event_type="state_update",
                        node_name=target,
                        state=state_manager.state
                    )
                else:
                    yield StreamEvent(
                        event_type="error",
                        node_name=target,
                        state=state_manager.state,
                        result=node_result
                    )
                    return

            current_node = target

    def get_state_schema(self) -> Optional[Dict[str, Any]]:
        """Get the state schema if available."""
        if self.state_type:
            return getattr(self.state_type, "__annotations__", {})
        return None

    def __repr__(self) -> str:
        return f"<CompiledGraph(name={self.name}, nodes={len(self._nodes)})>"
