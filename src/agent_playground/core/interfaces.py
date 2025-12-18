"""
Protocol Interfaces for Pluggable Modules.

Defines the contracts that ASR, LLM, TTS, Tools, Memory, and Policy backends
must implement. Uses Python Protocols for structural subtyping - any class
that implements the required methods is considered compatible.

Design principles:
- All backends support streaming (async iterators)
- No global state - everything is passed explicitly
- Backends are stateless where possible
- Clear separation of concerns
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Optional,
    Protocol,
    TypeVar,
    runtime_checkable,
)

from .events import (
    AudioFrameIn,
    LLMToken,
    TranscriptFinal,
    TranscriptPartial,
    TTSChunk,
)


# ─────────────────────────────────────────────────────────────────────────────
# Common Types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChatMessage:
    """A single message in the conversation history."""

    role: str  # "system", "user", "assistant", "tool"
    content: str
    name: Optional[str] = None  # For tool messages
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None


@dataclass
class ChatContext:
    """
    Conversation context passed to LLM.

    Maintains the full chat history and provides utilities
    for context management.
    """

    messages: list[ChatMessage] = field(default_factory=list)

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the context."""
        self.messages.append(ChatMessage(role=role, content=content, **kwargs))

    def add_user_message(self, content: str) -> None:
        """Convenience: add a user message."""
        self.add_message("user", content)

    def add_assistant_message(self, content: str) -> None:
        """Convenience: add an assistant message."""
        self.add_message("assistant", content)

    def add_system_message(self, content: str) -> None:
        """Convenience: add a system message."""
        self.add_message("system", content)

    def truncate(self, max_messages: int) -> None:
        """Keep only the last N messages (preserving system messages)."""
        system_msgs = [m for m in self.messages if m.role == "system"]
        other_msgs = [m for m in self.messages if m.role != "system"]

        # Keep system messages + last N other messages
        keep_count = max_messages - len(system_msgs)
        self.messages = system_msgs + other_msgs[-keep_count:]

    def clone(self) -> ChatContext:
        """Create a deep copy of this context."""
        return ChatContext(messages=[
            ChatMessage(
                role=m.role,
                content=m.content,
                name=m.name,
                tool_call_id=m.tool_call_id,
                tool_calls=m.tool_calls,
            )
            for m in self.messages
        ])


@dataclass
class ToolDefinition:
    """Definition of a callable tool/function."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable[..., Any]


@dataclass
class SpeechEvent:
    """Event from ASR - either partial or final transcript."""

    text: str
    is_final: bool
    confidence: float = 0.0
    language: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# ASR Backend Protocol
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class ASRBackend(Protocol):
    """
    Protocol for Automatic Speech Recognition backends.

    Implementations should:
    - Accept streaming audio input
    - Yield partial transcripts as they become available
    - Yield a final transcript when utterance is complete
    - Handle sample rate conversion if needed

    Example implementations:
    - FakeASR: Returns canned responses for testing
    - FasterWhisperASR: Local faster-whisper inference
    - WhisperCppASR: whisper.cpp via ctypes/subprocess
    """

    @property
    def sample_rate(self) -> int:
        """Expected input audio sample rate (Hz)."""
        ...

    @property
    def num_channels(self) -> int:
        """Expected number of audio channels."""
        ...

    async def stream(
        self,
        audio_stream: AsyncIterator[AudioFrameIn],
    ) -> AsyncIterator[SpeechEvent]:
        """
        Process streaming audio and yield transcription events.

        Args:
            audio_stream: Async iterator of audio frames from user

        Yields:
            SpeechEvent objects (partial and final transcripts)
        """
        ...

    async def close(self) -> None:
        """Release any resources held by this backend."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# LLM Backend Protocol
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """Accumulated response from LLM."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: Optional[str] = None

    # Usage stats
    prompt_tokens: int = 0
    completion_tokens: int = 0


@runtime_checkable
class LLMBackend(Protocol):
    """
    Protocol for Large Language Model backends.

    Implementations should:
    - Support streaming token generation
    - Handle tool/function calling (if supported)
    - Accept a ChatContext for conversation history

    Example implementations:
    - FakeLLM: Returns canned responses for testing
    - OllamaLLM: Local Ollama server
    - VLLMLlm: vLLM local server
    """

    @property
    def model_name(self) -> str:
        """Name/ID of the model being used."""
        ...

    @property
    def supports_tools(self) -> bool:
        """Whether this backend supports tool/function calling."""
        ...

    @property
    def supports_streaming(self) -> bool:
        """Whether this backend supports streaming responses."""
        ...

    async def chat(
        self,
        chat_ctx: ChatContext,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[LLMToken]:
        """
        Generate a response given conversation context.

        Args:
            chat_ctx: Conversation history
            tools: Available tools for function calling
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Yields:
            LLMToken events as tokens are generated
        """
        ...

    async def close(self) -> None:
        """Release any resources held by this backend."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# TTS Backend Protocol
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class TTSBackend(Protocol):
    """
    Protocol for Text-to-Speech backends.

    Implementations should:
    - Support streaming synthesis (yield audio as it's generated)
    - Handle text chunking (sentence-level is recommended)
    - Produce audio in a consistent format

    Example implementations:
    - FakeTTS: Returns silence or a tone for testing
    - PiperTTS: Local Piper neural TTS
    - CoquiTTS: Local Coqui TTS
    """

    @property
    def sample_rate(self) -> int:
        """Output audio sample rate (Hz)."""
        ...

    @property
    def num_channels(self) -> int:
        """Output number of audio channels."""
        ...

    @property
    def voice(self) -> str:
        """Current voice identifier."""
        ...

    async def synthesize(
        self,
        text_stream: AsyncIterator[str],
    ) -> AsyncIterator[TTSChunk]:
        """
        Synthesize speech from streaming text.

        Args:
            text_stream: Async iterator of text chunks (tokens or sentences)

        Yields:
            TTSChunk events containing audio data
        """
        ...

    async def synthesize_text(self, text: str) -> AsyncIterator[TTSChunk]:
        """
        Convenience method: synthesize a complete text string.

        Args:
            text: Complete text to synthesize

        Yields:
            TTSChunk events containing audio data
        """
        ...

    async def close(self) -> None:
        """Release any resources held by this backend."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Tool Backend Protocol
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class ToolBackend(Protocol):
    """
    Protocol for tool execution backends.

    Tools are functions the LLM can call to take actions or retrieve data.
    The ToolBackend manages tool registration and execution.
    """

    @property
    def tools(self) -> list[ToolDefinition]:
        """List of available tools."""
        ...

    def register_tool(self, tool: ToolDefinition) -> None:
        """Register a new tool."""
        ...

    def unregister_tool(self, name: str) -> None:
        """Remove a tool by name."""
        ...

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: Optional[Any] = None,
    ) -> Any:
        """
        Execute a tool with the given arguments.

        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments to pass to the tool
            context: Optional execution context (session, agent, etc.)

        Returns:
            Result of tool execution (will be converted to string for LLM)
        """
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Memory Backend Protocol (Optional)
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class MemoryBackend(Protocol):
    """
    Protocol for conversation memory backends.

    Optional module for persistent memory, RAG, or advanced
    context management beyond simple ChatContext.
    """

    async def store(
        self,
        session_id: str,
        key: str,
        value: Any,
    ) -> None:
        """Store a value in memory."""
        ...

    async def retrieve(
        self,
        session_id: str,
        key: str,
    ) -> Optional[Any]:
        """Retrieve a value from memory."""
        ...

    async def search(
        self,
        session_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Semantic search over memory (for RAG)."""
        ...

    async def get_context(
        self,
        session_id: str,
        max_messages: int = 50,
    ) -> ChatContext:
        """Retrieve conversation context."""
        ...

    async def save_context(
        self,
        session_id: str,
        context: ChatContext,
    ) -> None:
        """Persist conversation context."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Policy Backend Protocol (Optional)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PolicyResult:
    """Result of a policy check."""

    allowed: bool
    reason: Optional[str] = None
    modified_content: Optional[str] = None  # If content was sanitized


@runtime_checkable
class PolicyBackend(Protocol):
    """
    Protocol for content policy/guardrail backends.

    Optional module for content filtering, safety checks,
    and response modification.
    """

    async def check_input(
        self,
        text: str,
        context: Optional[ChatContext] = None,
    ) -> PolicyResult:
        """Check user input against policy."""
        ...

    async def check_output(
        self,
        text: str,
        context: Optional[ChatContext] = None,
    ) -> PolicyResult:
        """Check agent output against policy."""
        ...

    async def filter_tools(
        self,
        tools: list[ToolDefinition],
        context: Optional[ChatContext] = None,
    ) -> list[ToolDefinition]:
        """Filter available tools based on policy."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Abstract Base Classes (for inheritance-based implementations)
# ─────────────────────────────────────────────────────────────────────────────

class BaseASRBackend(ABC):
    """Abstract base class for ASR implementations."""

    def __init__(self, sample_rate: int = 24000, num_channels: int = 1):
        self._sample_rate = sample_rate
        self._num_channels = num_channels

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def num_channels(self) -> int:
        return self._num_channels

    @abstractmethod
    async def stream(
        self,
        audio_stream: AsyncIterator[AudioFrameIn],
    ) -> AsyncIterator[SpeechEvent]:
        ...

    async def close(self) -> None:
        """Override to release resources."""
        pass


class BaseLLMBackend(ABC):
    """Abstract base class for LLM implementations."""

    def __init__(self, model_name: str, supports_tools: bool = False):
        self._model_name = model_name
        self._supports_tools = supports_tools

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def supports_tools(self) -> bool:
        return self._supports_tools

    @property
    def supports_streaming(self) -> bool:
        return True

    @abstractmethod
    async def chat(
        self,
        chat_ctx: ChatContext,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[LLMToken]:
        ...

    async def close(self) -> None:
        """Override to release resources."""
        pass


class BaseTTSBackend(ABC):
    """Abstract base class for TTS implementations."""

    def __init__(
        self,
        sample_rate: int = 24000,
        num_channels: int = 1,
        voice: str = "default",
    ):
        self._sample_rate = sample_rate
        self._num_channels = num_channels
        self._voice = voice

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def num_channels(self) -> int:
        return self._num_channels

    @property
    def voice(self) -> str:
        return self._voice

    @abstractmethod
    async def synthesize(
        self,
        text_stream: AsyncIterator[str],
    ) -> AsyncIterator[TTSChunk]:
        ...

    async def synthesize_text(self, text: str) -> AsyncIterator[TTSChunk]:
        """Default implementation: wrap text in async iterator."""
        async def text_iter() -> AsyncIterator[str]:
            yield text

        async for chunk in self.synthesize(text_iter()):
            yield chunk

    async def close(self) -> None:
        """Override to release resources."""
        pass
