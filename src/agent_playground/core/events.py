"""
Event Model for Agent Playground.

Defines all events that flow through the voice pipeline, enabling pub/sub
communication between modules. Events are immutable dataclasses with
timestamps for tracing and debugging.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional


class AgentState(Enum):
    """States an agent can be in during its lifecycle."""

    INITIALIZING = auto()
    LISTENING = auto()
    THINKING = auto()
    SPEAKING = auto()
    AWAY = auto()
    CLOSED = auto()


@dataclass(frozen=True)
class AgentEvent:
    """
    Base class for all pipeline events.

    All events are immutable and carry:
    - A unique event_id for tracing
    - A timestamp for latency analysis
    - An optional agent_id for multi-agent scenarios
    - An optional utterance_id for e2e latency tracking
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.time)
    agent_id: Optional[str] = None
    utterance_id: Optional[str] = None  # For e2e latency tracking

    def elapsed_ms(self) -> float:
        """Milliseconds since this event was created."""
        return (time.time() - self.timestamp) * 1000

    def with_utterance_id(self, utterance_id: str) -> "AgentEvent":
        """Create a copy with utterance_id set (for propagation)."""
        # Note: frozen dataclass, so we use __class__ constructor
        return self.__class__(
            **{k: v if k != "utterance_id" else utterance_id
               for k, v in self.__dict__.items()}
        )


# ─────────────────────────────────────────────────────────────────────────────
# Audio Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AudioFrameIn(AgentEvent):
    """
    Raw audio frame received from user (via LiveKit track subscription).

    Audio should be normalized to:
    - Sample rate: 24000 Hz (LiveKit default)
    - Channels: 1 (mono)
    - Format: 16-bit signed PCM (bytes)
    - Frame duration: 20-50ms
    """

    data: bytes = b""
    sample_rate: int = 24000
    num_channels: int = 1
    samples_per_channel: int = 0
    participant_identity: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        """Duration of this audio frame in milliseconds."""
        if self.sample_rate == 0:
            return 0.0
        return (self.samples_per_channel / self.sample_rate) * 1000


@dataclass(frozen=True)
class AudioFrameOut(AgentEvent):
    """
    Synthesized audio frame to publish back to room.

    Same format constraints as AudioFrameIn.
    """

    data: bytes = b""
    sample_rate: int = 24000
    num_channels: int = 1
    samples_per_channel: int = 0

    # For TTS-aligned transcription
    text_segment: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        """Duration of this audio frame in milliseconds."""
        if self.sample_rate == 0:
            return 0.0
        return (self.samples_per_channel / self.sample_rate) * 1000


# ─────────────────────────────────────────────────────────────────────────────
# ASR / Transcription Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TranscriptPartial(AgentEvent):
    """
    Partial (interim) transcript from ASR - may change as more audio arrives.

    Use for:
    - Real-time display to users
    - Preemptive LLM generation
    - Turn detection hints
    """

    text: str = ""
    confidence: float = 0.0
    language: Optional[str] = None

    # Is this a stable partial (unlikely to change)?
    is_stable: bool = False


@dataclass(frozen=True)
class TranscriptFinal(AgentEvent):
    """
    Final transcript from ASR - utterance is complete.

    This triggers LLM inference. The text will not change.
    """

    text: str = ""
    confidence: float = 0.0
    language: Optional[str] = None

    # Duration of the speech that produced this transcript
    speech_duration_ms: float = 0.0

    # End-of-utterance probability (if turn detector provided it)
    eou_probability: float = 1.0


# ─────────────────────────────────────────────────────────────────────────────
# LLM Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LLMToken(AgentEvent):
    """
    Single token streamed from LLM during generation.

    Tokens are accumulated and forwarded to TTS as sentences complete.
    """

    token: str = ""

    # Accumulated text so far (for convenience)
    accumulated_text: str = ""

    # Is this a tool call token (vs regular text)?
    is_tool_call: bool = False

    # Token generation metrics
    token_index: int = 0


@dataclass(frozen=True)
class UtteranceFinal(AgentEvent):
    """
    Complete LLM response - all tokens have been generated.

    Contains the full text and optional tool call results.
    """

    text: str = ""

    # Tool calls made during this response
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    # Generation metrics
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    # Time to first token (TTFT) in milliseconds
    ttft_ms: float = 0.0

    # Was generation interrupted by user?
    interrupted: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# TTS Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TTSChunk(AgentEvent):
    """
    Audio chunk from TTS synthesis.

    Emitted as TTS produces audio incrementally. Multiple TTSChunks
    combine to form a complete spoken response.
    """

    audio_data: bytes = b""
    sample_rate: int = 24000
    num_channels: int = 1

    # The text segment this audio corresponds to
    text_segment: str = ""

    # Is this the last chunk for the current utterance?
    is_final: bool = False

    # Chunk sequence number (for ordering)
    chunk_index: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Agent State Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentStateChange(AgentEvent):
    """
    Agent state transition event.

    Published when agent moves between states (listening, thinking, speaking).
    Useful for UI updates and debugging.
    """

    previous_state: AgentState = AgentState.INITIALIZING
    new_state: AgentState = AgentState.INITIALIZING

    # Optional reason for the state change
    reason: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Tool Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolCallStart(AgentEvent):
    """Tool execution has started."""

    tool_name: str = ""
    tool_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallEnd(AgentEvent):
    """Tool execution has completed."""

    tool_name: str = ""
    tool_id: str = ""
    result: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Control Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InterruptionDetected(AgentEvent):
    """User has started speaking while agent was speaking - interrupt TTS."""

    # How much of the agent's response was played before interruption
    played_duration_ms: float = 0.0


@dataclass(frozen=True)
class VADEvent(AgentEvent):
    """Voice Activity Detection event."""

    is_speech: bool = False
    probability: float = 0.0


@dataclass(frozen=True)
class VADSpeechStart(AgentEvent):
    """
    VAD detected start of speech.

    This event creates a new utterance_id that should be propagated
    through ASR → LLM → TTS for e2e latency tracking.
    """
    pass


@dataclass(frozen=True)
class VADSpeechEnd(AgentEvent):
    """VAD detected end of speech."""

    # Duration of the speech segment in milliseconds
    speech_duration_ms: float = 0.0


@dataclass(frozen=True)
class EndOfTurnDetected(AgentEvent):
    """Turn detector has determined user has finished speaking."""

    confidence: float = 0.0
