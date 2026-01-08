"""Core components: interfaces, events, config, and dependency injection."""

from .events import (
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
    VADEvent,
    VADSpeechStart,
    VADSpeechEnd,
    InterruptionDetected,
)
from .interfaces import (
    ASRBackend,
    LLMBackend,
    TTSBackend,
    ToolBackend,
    MemoryBackend,
    PolicyBackend,
)
from .config import AgentConfig, ModuleConfig
from .event_bus import EventBus
from .agent_manager import AgentManager
from .metrics import (
    MetricsCollector,
    get_metrics,
    set_metrics,
    PipelineStage,
    DropReason,
    LLMFailureReason,
)

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
    "VADEvent",
    "VADSpeechStart",
    "VADSpeechEnd",
    "InterruptionDetected",
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
    # Core
    "EventBus",
    "AgentManager",
    # Metrics
    "MetricsCollector",
    "get_metrics",
    "set_metrics",
    "PipelineStage",
    "DropReason",
    "LLMFailureReason",
]
