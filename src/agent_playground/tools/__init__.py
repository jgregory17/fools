"""
Tool Registry and Built-in Tools.

Provides:
- Tool registration and discovery
- Built-in utility tools
- Tool execution context
"""

from .registry import ToolRegistry, tool
from .builtin import (
    end_conversation,
    transfer_to_agent,
    get_current_time,
    search_memory,
)

__all__ = [
    "ToolRegistry",
    "tool",
    "end_conversation",
    "transfer_to_agent",
    "get_current_time",
    "search_memory",
]
