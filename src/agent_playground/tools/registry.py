"""
Tool Registry - Manages tool registration and execution.

Provides a decorator-based tool definition system similar to
LiveKit's @function_tool decorator.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar, get_type_hints

from ..core.interfaces import ToolDefinition

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class ToolContext:
    """Context passed to tool execution."""

    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    room_name: Optional[str] = None
    participant_identity: Optional[str] = None
    userdata: dict[str, Any] = field(default_factory=dict)

    # Set by registry during execution
    allow_interruptions: bool = True


def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Callable[[F], F]:
    """
    Decorator to define a tool function.

    Usage:
        @tool(description="Get the current weather")
        async def get_weather(location: str) -> dict:
            '''Get weather for a location.

            Args:
                location: City name or coordinates
            '''
            return {"temp": 72, "condition": "sunny"}

    The function's type hints and docstring are used to generate
    the tool's JSON schema automatically.
    """
    def decorator(func: F) -> F:
        # Extract function metadata
        func_name = name or func.__name__
        func_description = description or func.__doc__ or f"Execute {func_name}"

        # Parse first line of docstring as description
        if func_description and "\n" in func_description:
            func_description = func_description.split("\n")[0].strip()

        # Generate parameter schema from type hints
        parameters = _generate_parameter_schema(func)

        # Create tool definition
        tool_def = ToolDefinition(
            name=func_name,
            description=func_description,
            parameters=parameters,
            handler=func,
        )

        # Attach metadata to function
        func._tool_definition = tool_def  # type: ignore
        func._is_tool = True  # type: ignore

        return func

    return decorator


def _generate_parameter_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Generate JSON Schema for function parameters."""
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}

    sig = inspect.signature(func)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        # Skip self, cls, and context parameters
        if param_name in ("self", "cls", "context", "ctx"):
            continue

        param_schema: dict[str, Any] = {}

        # Get type from hints
        if param_name in hints:
            param_type = hints[param_name]
            param_schema = _type_to_json_schema(param_type)

        # Check if required (no default value)
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

        # Try to extract description from docstring
        doc_desc = _extract_param_description(func, param_name)
        if doc_desc:
            param_schema["description"] = doc_desc

        properties[param_name] = param_schema

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _type_to_json_schema(python_type: Any) -> dict[str, Any]:
    """Convert Python type hint to JSON Schema."""
    # Handle basic types
    type_mapping = {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        list: {"type": "array"},
        dict: {"type": "object"},
    }

    if python_type in type_mapping:
        return type_mapping[python_type]

    # Handle Optional
    origin = getattr(python_type, "__origin__", None)
    if origin is type(None):
        return {"type": "null"}

    # Handle Union (including Optional)
    if origin is type(None) or str(origin) == "typing.Union":
        args = getattr(python_type, "__args__", ())
        if len(args) == 2 and type(None) in args:
            # This is Optional[T]
            inner_type = args[0] if args[1] is type(None) else args[1]
            return _type_to_json_schema(inner_type)

    # Handle List[T]
    if origin is list:
        args = getattr(python_type, "__args__", ())
        if args:
            return {"type": "array", "items": _type_to_json_schema(args[0])}
        return {"type": "array"}

    # Handle Dict[K, V]
    if origin is dict:
        return {"type": "object"}

    # Default to string
    return {"type": "string"}


def _extract_param_description(func: Callable[..., Any], param_name: str) -> Optional[str]:
    """Extract parameter description from docstring."""
    if not func.__doc__:
        return None

    # Look for Google-style docstring: "param_name: description"
    lines = func.__doc__.split("\n")
    in_args_section = False

    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("args:"):
            in_args_section = True
            continue
        if in_args_section:
            if stripped.startswith(f"{param_name}:"):
                return stripped.split(":", 1)[1].strip()
            if stripped and not stripped.startswith(" ") and ":" in stripped:
                # Might be next section
                if not stripped.split(":")[0].strip().lower() in ["returns", "raises", "yields"]:
                    continue
                in_args_section = False

    return None


class ToolRegistry:
    """
    Registry for managing tools available to agents.

    Supports:
    - Registering tools from decorated functions
    - Loading tools from modules
    - Tool execution with context
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool_def: ToolDefinition) -> None:
        """Register a tool definition."""
        self._tools[tool_def.name] = tool_def
        logger.debug(f"Registered tool: {tool_def.name}")

    def register_function(self, func: Callable[..., Any]) -> None:
        """Register a function decorated with @tool."""
        if not getattr(func, "_is_tool", False):
            raise ValueError(f"Function {func.__name__} is not decorated with @tool")

        tool_def = func._tool_definition  # type: ignore
        self.register(tool_def)

    def unregister(self, name: str) -> None:
        """Remove a tool by name."""
        if name in self._tools:
            del self._tools[name]
            logger.debug(f"Unregistered tool: {name}")

    def get(self, name: str) -> Optional[ToolDefinition]:
        """Get a tool by name."""
        return self._tools.get(name)

    @property
    def tools(self) -> list[ToolDefinition]:
        """Get all registered tools."""
        return list(self._tools.values())

    def get_schemas(self) -> list[dict[str, Any]]:
        """Get tool schemas for LLM function calling."""
        schemas = []
        for tool in self._tools.values():
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
        return schemas

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Optional[ToolContext] = None,
    ) -> Any:
        """
        Execute a tool by name with given arguments.

        Args:
            name: Tool name
            arguments: Arguments to pass to the tool
            context: Execution context

        Returns:
            Tool result (will be converted to string for LLM)
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")

        logger.info(f"Executing tool: {name}")

        try:
            # Check if handler expects context
            sig = inspect.signature(tool.handler)
            if "context" in sig.parameters or "ctx" in sig.parameters:
                context_param = "context" if "context" in sig.parameters else "ctx"
                arguments[context_param] = context or ToolContext()

            # Execute (handle both sync and async)
            if asyncio.iscoroutinefunction(tool.handler):
                result = await tool.handler(**arguments)
            else:
                result = tool.handler(**arguments)

            logger.info(f"Tool {name} completed successfully")
            return result

        except Exception as e:
            logger.exception(f"Tool {name} failed: {e}")
            raise


# Global registry instance
_global_registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    """Get the global tool registry."""
    return _global_registry
