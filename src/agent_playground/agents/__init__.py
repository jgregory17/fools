"""
Agent Definitions.

Each agent is a composition root that wires together modules
based on configuration.

To create a new agent:
1. Add a YAML config file in configs/
2. (Optional) Create a custom Agent class here for complex behavior
"""

from .base_agent import BasePlaygroundAgent
from .simple_agent import SimpleVoiceAgent

__all__ = [
    "BasePlaygroundAgent",
    "SimpleVoiceAgent",
]
