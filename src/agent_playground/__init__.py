"""
Agent Playground: A modular, production-quality voice agent framework on LiveKit.

This package provides:
- Pluggable ASR/LLM/TTS backends with clear Protocol interfaces
- Event-driven architecture for streaming audio pipelines
- Multi-agent support with config-driven composition
- Local-first inference (no hosted APIs required)
"""

from .core.events import (
    AgentEvent,
    AudioFrameIn,
    AudioFrameOut,
    TranscriptPartial,
    TranscriptFinal,
    LLMToken,
    UtteranceFinal,
    TTSChunk,
    AgentStateChange,
    AgentState,
)
from .core.interfaces import (
    ASRBackend,
    LLMBackend,
    TTSBackend,
    ToolBackend,
    MemoryBackend,
    PolicyBackend,
)
from .core.config import AgentConfig, ModuleConfig
from .core.agent_manager import AgentManager
from .core.event_bus import EventBus

__version__ = "0.1.0"

__all__ = [
    # Events
    "AgentEvent",
    "AudioFrameIn",
    "AudioFrameOut",
    "TranscriptPartial",
    "TranscriptFinal",
    "LLMToken",
    "UtteranceFinal",
    "TTSChunk",
    "AgentStateChange",
    "AgentState",
    # Interfaces
    "ASRBackend",
    "LLMBackend",
    "TTSBackend",
    "ToolBackend",
    "MemoryBackend",
    "PolicyBackend",
    # Config
    "AgentConfig",
    "ModuleConfig",
    # Manager
    "AgentManager",
    "EventBus",
]
