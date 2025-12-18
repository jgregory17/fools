"""
Conversation Memory Management.

Provides context window management for LLM conversations:
- Turn tracking with token counting
- Automatic summarization of old turns
- Hard token budget enforcement
- Sliding window with intelligent truncation

Design Goals:
- Prevent context overflow errors
- Maintain conversation coherence across long sessions
- Minimize latency impact from summarization
- Pluggable summarization backends
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional, Any, Protocol, List, Dict
from collections import deque

logger = logging.getLogger(__name__)


class Role(Enum):
    """Conversation participant role."""
    SYSTEM = auto()
    USER = auto()
    ASSISTANT = auto()
    TOOL = auto()


@dataclass
class ConversationTurn:
    """A single turn in the conversation."""

    role: Role
    content: str
    timestamp: float = field(default_factory=time.time)

    # Token count (estimated or actual)
    token_count: int = 0

    # Tool-related fields
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict format for LLM APIs."""
        result = {
            "role": self.role.name.lower(),
            "content": self.content,
        }
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        if self.tool_name:
            result["name"] = self.tool_name
        return result


class SummarizationHook(Protocol):
    """Protocol for summarization backends."""

    async def summarize(
        self,
        turns: List[ConversationTurn],
        max_tokens: int,
    ) -> str:
        """
        Summarize a list of turns into a compact summary.

        Args:
            turns: Turns to summarize
            max_tokens: Target token count for summary

        Returns:
            Summary text
        """
        ...


@dataclass
class ConversationMemoryConfig:
    """Configuration for conversation memory."""

    # Maximum turns to keep in full detail
    max_turns: int = 50

    # When to trigger summarization (as fraction of max_turns)
    summarize_threshold: float = 0.8

    # Target token count for summaries
    summary_max_tokens: int = 500

    # Hard token budget for entire context
    # 0 = no limit
    max_context_tokens: int = 8000

    # Minimum turns to always keep (never summarize)
    min_recent_turns: int = 5

    # System message handling
    preserve_system_messages: bool = True

    # Token estimation (chars per token, rough estimate)
    chars_per_token: float = 4.0

    # Whether to auto-summarize when threshold reached
    auto_summarize: bool = True


class ConversationMemory:
    """
    Manages conversation history with context window limits.

    Provides automatic summarization of older turns to stay within
    token budgets while preserving conversation coherence.

    Usage:
        memory = ConversationMemory(
            config=ConversationMemoryConfig(max_context_tokens=4096),
            summarizer=my_summarizer,  # Optional
        )

        # Add turns
        memory.add_user_turn("Hello!")
        memory.add_assistant_turn("Hi there! How can I help?")

        # Get context for LLM
        messages = memory.get_messages()

        # Check if summarization needed
        if memory.should_summarize():
            await memory.summarize_old_turns()
    """

    def __init__(
        self,
        config: Optional[ConversationMemoryConfig] = None,
        summarizer: Optional[SummarizationHook] = None,
    ):
        self.config = config or ConversationMemoryConfig()
        self._summarizer = summarizer

        # Turns storage
        self._system_messages: List[ConversationTurn] = []
        self._turns: deque[ConversationTurn] = deque()
        self._summaries: List[str] = []

        # Metrics
        self._total_turns_added = 0
        self._summarizations_performed = 0
        self._turns_summarized = 0

    @property
    def turn_count(self) -> int:
        """Number of turns in memory (excluding system messages)."""
        return len(self._turns)

    @property
    def estimated_tokens(self) -> int:
        """Estimated total token count."""
        total = 0

        # System messages
        for turn in self._system_messages:
            total += turn.token_count or self._estimate_tokens(turn.content)

        # Summaries
        for summary in self._summaries:
            total += self._estimate_tokens(summary)

        # Turns
        for turn in self._turns:
            total += turn.token_count or self._estimate_tokens(turn.content)

        return total

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count from text length."""
        return int(len(text) / self.config.chars_per_token)

    def add_system_message(self, content: str, token_count: int = 0) -> None:
        """Add a system message (preserved across summarization)."""
        turn = ConversationTurn(
            role=Role.SYSTEM,
            content=content,
            token_count=token_count or self._estimate_tokens(content),
        )
        self._system_messages.append(turn)

    def add_user_turn(
        self,
        content: str,
        token_count: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a user turn."""
        self._add_turn(Role.USER, content, token_count, metadata)

    def add_assistant_turn(
        self,
        content: str,
        token_count: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add an assistant turn."""
        self._add_turn(Role.ASSISTANT, content, token_count, metadata)

    def add_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result: str,
        token_count: int = 0,
    ) -> None:
        """Add a tool result."""
        turn = ConversationTurn(
            role=Role.TOOL,
            content=result,
            token_count=token_count or self._estimate_tokens(result),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )
        self._turns.append(turn)
        self._total_turns_added += 1

    def _add_turn(
        self,
        role: Role,
        content: str,
        token_count: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Internal method to add a turn."""
        turn = ConversationTurn(
            role=role,
            content=content,
            token_count=token_count or self._estimate_tokens(content),
            metadata=metadata or {},
        )
        self._turns.append(turn)
        self._total_turns_added += 1

        # Check for auto-summarization
        if self.config.auto_summarize and self.should_summarize():
            logger.info("ConversationMemory: triggering auto-summarization")
            # Note: async summarization should be triggered externally
            # This just enforces hard limits synchronously
            self._enforce_hard_limits()

    def should_summarize(self) -> bool:
        """Check if summarization is recommended."""
        if self._summarizer is None:
            return False

        # Check turn count threshold
        threshold = int(self.config.max_turns * self.config.summarize_threshold)
        if len(self._turns) >= threshold:
            return True

        # Check token budget
        if self.config.max_context_tokens > 0:
            if self.estimated_tokens >= self.config.max_context_tokens * 0.9:
                return True

        return False

    async def summarize_old_turns(self) -> Optional[str]:
        """
        Summarize older turns to reduce context size.

        Returns:
            Summary text, or None if no summarization performed
        """
        if self._summarizer is None:
            logger.warning("ConversationMemory: no summarizer configured")
            self._enforce_hard_limits()
            return None

        # Determine how many turns to summarize
        turns_to_keep = max(
            self.config.min_recent_turns,
            len(self._turns) // 2,  # Keep at least half
        )
        turns_to_summarize = len(self._turns) - turns_to_keep

        if turns_to_summarize <= 0:
            return None

        # Extract turns for summarization
        old_turns = list(self._turns)[:turns_to_summarize]

        # Perform summarization
        try:
            summary = await self._summarizer.summarize(
                old_turns,
                max_tokens=self.config.summary_max_tokens,
            )

            # Update state
            for _ in range(turns_to_summarize):
                self._turns.popleft()

            self._summaries.append(summary)
            self._summarizations_performed += 1
            self._turns_summarized += turns_to_summarize

            logger.info(
                f"ConversationMemory: summarized {turns_to_summarize} turns "
                f"into {self._estimate_tokens(summary)} tokens"
            )

            return summary

        except Exception as e:
            logger.error(f"ConversationMemory: summarization failed: {e}")
            self._enforce_hard_limits()
            return None

    def _enforce_hard_limits(self) -> None:
        """Enforce hard limits by dropping oldest turns."""
        # Enforce turn limit
        while len(self._turns) > self.config.max_turns:
            self._turns.popleft()

        # Enforce token limit (rough)
        if self.config.max_context_tokens > 0:
            while (
                self.estimated_tokens > self.config.max_context_tokens
                and len(self._turns) > self.config.min_recent_turns
            ):
                self._turns.popleft()

    def get_messages(self) -> List[Dict[str, Any]]:
        """
        Get conversation messages in LLM API format.

        Returns messages in order:
        1. System messages
        2. Summary (as system message)
        3. Recent turns
        """
        messages = []

        # System messages
        for turn in self._system_messages:
            messages.append(turn.to_dict())

        # Summaries (as system context)
        if self._summaries:
            combined_summary = "\n\n".join([
                f"[Previous conversation summary {i+1}]: {s}"
                for i, s in enumerate(self._summaries)
            ])
            messages.append({
                "role": "system",
                "content": f"Context from earlier in conversation:\n{combined_summary}",
            })

        # Recent turns
        for turn in self._turns:
            messages.append(turn.to_dict())

        return messages

    def get_last_n_turns(self, n: int) -> List[ConversationTurn]:
        """Get the last N turns."""
        turns = list(self._turns)
        return turns[-n:] if n < len(turns) else turns

    def clear(self) -> None:
        """Clear all conversation history."""
        self._turns.clear()
        self._summaries.clear()
        # System messages are preserved

    def reset(self) -> None:
        """Reset everything including system messages."""
        self._system_messages.clear()
        self._turns.clear()
        self._summaries.clear()

    @property
    def metrics(self) -> Dict[str, Any]:
        """Get memory metrics."""
        return {
            "turn_count": len(self._turns),
            "system_messages": len(self._system_messages),
            "summaries": len(self._summaries),
            "estimated_tokens": self.estimated_tokens,
            "max_tokens": self.config.max_context_tokens,
            "total_turns_added": self._total_turns_added,
            "summarizations_performed": self._summarizations_performed,
            "turns_summarized": self._turns_summarized,
        }


class SimpleSummarizer:
    """
    Simple summarization implementation using truncation.

    For production, replace with an LLM-based summarizer.
    """

    async def summarize(
        self,
        turns: List[ConversationTurn],
        max_tokens: int,
    ) -> str:
        """Create a simple summary by extracting key points."""
        if not turns:
            return ""

        # Simple approach: take first and last few turns
        parts = []

        # Context
        parts.append(f"Conversation with {len(turns)} exchanges:")

        # First turn
        if turns:
            first = turns[0]
            parts.append(f"- Started with: {first.content[:100]}...")

        # Last turn
        if len(turns) > 1:
            last = turns[-1]
            parts.append(f"- Ended with: {last.content[:100]}...")

        # Topic hints (extract from user messages)
        user_turns = [t for t in turns if t.role == Role.USER]
        if user_turns:
            topics = [t.content[:50] for t in user_turns[:3]]
            parts.append(f"- Topics discussed: {', '.join(topics)}")

        summary = "\n".join(parts)

        # Truncate to max tokens (rough)
        max_chars = max_tokens * 4
        if len(summary) > max_chars:
            summary = summary[:max_chars] + "..."

        return summary


class LLMSummarizer:
    """
    LLM-based summarization (placeholder for real implementation).

    In production, this would call the LLM to generate a proper summary.
    """

    def __init__(self, llm_backend: Any = None):
        self._llm = llm_backend

    async def summarize(
        self,
        turns: List[ConversationTurn],
        max_tokens: int,
    ) -> str:
        """Summarize using LLM."""
        if self._llm is None:
            # Fallback to simple summarizer
            simple = SimpleSummarizer()
            return await simple.summarize(turns, max_tokens)

        # Build prompt
        conversation_text = "\n".join([
            f"{t.role.name}: {t.content}"
            for t in turns
        ])

        prompt = f"""Summarize the following conversation in {max_tokens} tokens or less.
Focus on key topics, decisions, and context needed for continuation.

Conversation:
{conversation_text}

Summary:"""

        # TODO: Call LLM with the prompt
        # For now, fall back to simple summarizer
        simple = SimpleSummarizer()
        return await simple.summarize(turns, max_tokens)
