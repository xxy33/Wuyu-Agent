"""
Edge definitions for StateGraph workflow engine.
Provides Edge classes for connecting nodes including fixed, conditional, and parallel edges.
"""
from dataclasses import dataclass, field
from typing import (
    Dict, Any, Optional, Callable, Awaitable, Union, List, Tuple,
    TypeVar, Set
)
from enum import Enum
import asyncio

from swagent.stategraph.node import START, END, get_node_name, _SpecialNode, Node


class EdgeType(Enum):
    """Types of edges in the graph."""
    FIXED = "fixed"           # Simple edge from A to B
    CONDITIONAL = "conditional"  # Edge that routes based on condition
    PARALLEL = "parallel"     # Edge that fans out to multiple nodes


# Type for state dictionary
StateDict = Dict[str, Any]

# Type for condition functions
ConditionFunc = Callable[[StateDict], Union[str, Awaitable[str]]]

# Type for loop condition functions
LoopConditionFunc = Callable[[StateDict], Union[bool, Awaitable[bool]]]


@dataclass
class Edge:
    """
    Base edge class representing a connection between nodes.

    Attributes:
        source: Source node name
        target: Target node name(s)
        edge_type: Type of edge
        condition: Condition function for conditional edges
        target_map: Mapping of condition results to targets
        default_target: Default target for conditional edges
        converge_to: Convergence node after parallel execution
        metadata: Additional edge metadata
    """
    source: str
    target: Union[str, List[str]]
    edge_type: EdgeType = EdgeType.FIXED
    condition: Optional[ConditionFunc] = None
    target_map: Optional[Dict[str, str]] = None
    default_target: Optional[str] = None
    converge_to: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate edge configuration."""
        if self.edge_type == EdgeType.CONDITIONAL:
            if self.condition is None:
                raise ValueError("Conditional edge requires a condition function")
            if self.target_map is None and not isinstance(self.target, str):
                raise ValueError(
                    "Conditional edge requires either target_map or single default target"
                )

        if self.edge_type == EdgeType.PARALLEL:
            if not isinstance(self.target, list):
                raise ValueError("Parallel edge requires a list of targets")
            if len(self.target) < 2:
                raise ValueError("Parallel edge requires at least 2 targets")

    @property
    def targets(self) -> List[str]:
        """Get all possible targets as a list."""
        result = []
        if isinstance(self.target, list):
            result = list(self.target)
        elif self.target_map:
            result = list(self.target_map.values())
            if self.default_target and self.default_target not in result:
                result.append(self.default_target)
        else:
            result = [self.target]
        # Include converge_to for parallel edges
        if self.converge_to and self.converge_to not in result:
            result.append(self.converge_to)
        return result

    async def resolve_target(self, state: StateDict) -> Union[str, List[str]]:
        """
        Resolve the target node(s) based on edge type and state.

        Args:
            state: Current state dictionary

        Returns:
            Target node name(s)
        """
        if self.edge_type == EdgeType.FIXED:
            return self.target

        elif self.edge_type == EdgeType.PARALLEL:
            return self.target  # Return all targets for parallel execution

        elif self.edge_type == EdgeType.CONDITIONAL:
            # Execute condition function
            result = self.condition(state)
            if asyncio.iscoroutine(result):
                result = await result

            # Map result to target
            if self.target_map and result in self.target_map:
                return self.target_map[result]
            elif self.default_target:
                return self.default_target
            elif isinstance(self.target, str):
                return self.target
            else:
                raise ValueError(
                    f"Condition returned '{result}' but no matching target found"
                )

        return self.target

    def __repr__(self) -> str:
        if self.edge_type == EdgeType.FIXED:
            return f"<Edge({self.source} -> {self.target})>"
        elif self.edge_type == EdgeType.PARALLEL:
            return f"<ParallelEdge({self.source} -> {self.target})>"
        else:
            return f"<ConditionalEdge({self.source} -> {self.targets})>"


def edge(
    source: Union[str, Node, _SpecialNode],
    target: Union[str, Node, _SpecialNode],
    **metadata
) -> Edge:
    """
    Create a fixed edge between two nodes.

    Args:
        source: Source node
        target: Target node
        **metadata: Additional metadata

    Returns:
        Edge object

    Example:
        edge("node_a", "node_b")
        edge(START, "first_node")
        edge("last_node", END)
    """
    return Edge(
        source=get_node_name(source),
        target=get_node_name(target),
        edge_type=EdgeType.FIXED,
        metadata=metadata
    )


def conditional_edge(
    source: Union[str, Node, _SpecialNode],
    condition: ConditionFunc,
    target_map: Optional[Dict[str, Union[str, Node, _SpecialNode]]] = None,
    default: Optional[Union[str, Node, _SpecialNode]] = None,
    **metadata
) -> Edge:
    """
    Create a conditional edge that routes based on state.

    Args:
        source: Source node
        condition: Function that takes state and returns target key
        target_map: Mapping of condition results to target nodes
        default: Default target if condition result not in map
        **metadata: Additional metadata

    Returns:
        Edge object

    Example:
        conditional_edge(
            "decision_node",
            lambda state: "approve" if state["score"] > 0.8 else "reject",
            {"approve": "approval_node", "reject": "rejection_node"}
        )
    """
    # Convert target_map values to names
    resolved_map = None
    if target_map:
        resolved_map = {
            k: get_node_name(v) for k, v in target_map.items()
        }

    return Edge(
        source=get_node_name(source),
        target=get_node_name(default) if default else "",
        edge_type=EdgeType.CONDITIONAL,
        condition=condition,
        target_map=resolved_map,
        default_target=get_node_name(default) if default else None,
        metadata=metadata
    )


def parallel_edge(
    source: Union[str, Node, _SpecialNode],
    targets: List[Union[str, Node, _SpecialNode]],
    converge_to: Optional[Union[str, Node, _SpecialNode]] = None,
    **metadata
) -> Edge:
    """
    Create a parallel edge that fans out to multiple nodes.

    Args:
        source: Source node
        targets: List of target nodes (min 2)
        converge_to: Node to converge to after all parallel nodes complete
        **metadata: Additional metadata

    Returns:
        Edge object

    Example:
        parallel_edge(
            "split_node",
            ["processor_a", "processor_b", "processor_c"],
            converge_to="aggregate"
        )
    """
    return Edge(
        source=get_node_name(source),
        target=[get_node_name(t) for t in targets],
        edge_type=EdgeType.PARALLEL,
        converge_to=get_node_name(converge_to) if converge_to else None,
        metadata=metadata
    )


def loop_condition(
    check_func: LoopConditionFunc,
    continue_target: Union[str, Node, _SpecialNode],
    exit_target: Union[str, Node, _SpecialNode]
) -> Tuple[ConditionFunc, Dict[str, str]]:
    """
    Create a loop condition function and target map.

    This is a helper for creating conditional edges that implement loops.

    Args:
        check_func: Function that returns True to continue loop, False to exit
        continue_target: Target node when loop should continue
        exit_target: Target node when loop should exit

    Returns:
        Tuple of (condition_func, target_map)

    Example:
        condition, targets = loop_condition(
            lambda state: state["count"] < 10,
            continue_target="loop_body",
            exit_target=END
        )
        graph.add_conditional_edge("check", condition, targets)
    """
    async def condition(state: StateDict) -> str:
        result = check_func(state)
        if asyncio.iscoroutine(result):
            result = await result
        return "continue" if result else "exit"

    target_map = {
        "continue": get_node_name(continue_target),
        "exit": get_node_name(exit_target)
    }

    return condition, target_map


class EdgeCollection:
    """
    Collection of edges for a graph.

    Provides methods for querying and validating edges.
    """

    def __init__(self):
        self._edges: List[Edge] = []

    def add(self, edge: Edge) -> None:
        """Add an edge to the collection."""
        self._edges.append(edge)

    def get_outgoing(self, node_name: str) -> List[Edge]:
        """Get all edges leaving a node."""
        return [e for e in self._edges if e.source == node_name]

    def get_incoming(self, node_name: str) -> List[Edge]:
        """Get all edges entering a node."""
        incoming = []
        for e in self._edges:
            if node_name in e.targets:
                incoming.append(e)
        return incoming

    def get_all_nodes(self) -> Set[str]:
        """Get all node names referenced by edges."""
        nodes = set()
        for e in self._edges:
            nodes.add(e.source)
            nodes.update(e.targets)
        return nodes

    def has_edge(self, source: str, target: str) -> bool:
        """Check if an edge exists between two nodes."""
        for e in self._edges:
            if e.source == source and target in e.targets:
                return True
        return False

    def validate(self, node_names: Set[str]) -> List[str]:
        """
        Validate edges against available nodes.

        Args:
            node_names: Set of valid node names

        Returns:
            List of validation errors
        """
        errors = []

        # Add special nodes to valid names
        valid_names = node_names | {"__START__", "__END__"}

        for edge in self._edges:
            # Check source
            if edge.source not in valid_names:
                errors.append(f"Edge source '{edge.source}' is not a valid node")

            # Check targets
            for target in edge.targets:
                if target and target not in valid_names:
                    errors.append(f"Edge target '{target}' is not a valid node")

        return errors

    def __iter__(self):
        return iter(self._edges)

    def __len__(self):
        return len(self._edges)

    def __repr__(self):
        return f"<EdgeCollection({len(self._edges)} edges)>"
