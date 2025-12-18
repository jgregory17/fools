"""
Fake LLM Backend for Testing.

Returns canned responses with simulated token streaming.
Useful for testing the pipeline without real LLM inference.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import AsyncIterator, Optional

from agent_playground.core.events import LLMToken
from agent_playground.core.interfaces import BaseLLMBackend, ChatContext, ToolDefinition

logger = logging.getLogger(__name__)


class FakeLLM(BaseLLMBackend):
    """
    Fake LLM backend for testing and development.

    Features:
    - Configurable response latency
    - Canned responses or echo mode
    - Simulates token streaming
    - Optional tool call simulation
    """

    def __init__(
        self,
        model_name: str = "fake-llm-v1",
        supports_tools: bool = True,
        canned_responses: list[str] | None = None,
        tokens_per_second: float = 50.0,
        ttft_ms: float = 200.0,  # Time to first token
        simulate_thinking: bool = True,
    ):
        super().__init__(model_name, supports_tools)

        self._canned_responses = canned_responses or [
            "I'd be happy to help you with that! Let me think about the best approach.",
            "That's an interesting question. Based on what you've told me, I would suggest considering a few options.",
            "I understand what you're looking for. Here's what I can tell you about that topic.",
            "Great question! Let me provide some information that might be useful.",
            "I see. Let me share some thoughts on that.",
        ]
        self._tokens_per_second = tokens_per_second
        self._ttft_ms = ttft_ms
        self._simulate_thinking = simulate_thinking
        self._closed = False

    async def chat(
        self,
        chat_ctx: ChatContext,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[LLMToken]:
        """
        Generate a fake response with simulated streaming.

        Simulates realistic LLM behavior:
        1. Initial latency (TTFT)
        2. Token-by-token streaming
        3. Variable token timing
        """
        logger.debug(f"FakeLLM: Generating response for {len(chat_ctx.messages)} messages")

        if self._closed:
            return

        # Simulate time to first token
        await asyncio.sleep(self._ttft_ms / 1000)

        # Pick a response (could be smarter based on context)
        response = random.choice(self._canned_responses)

        # Optionally add context-awareness
        if chat_ctx.messages:
            last_user_msg = None
            for msg in reversed(chat_ctx.messages):
                if msg.role == "user":
                    last_user_msg = msg.content
                    break

            if last_user_msg and "weather" in last_user_msg.lower():
                response = "The weather today is sunny with a high of 72 degrees. Perfect for outdoor activities!"
            elif last_user_msg and "help" in last_user_msg.lower():
                response = "Of course, I'm here to help! What would you like assistance with?"

        # Stream tokens
        words = response.split()
        accumulated = ""
        token_delay = 1.0 / self._tokens_per_second

        for i, word in enumerate(words):
            if self._closed:
                break

            # Add word with space
            token = word + " " if i < len(words) - 1 else word
            accumulated += token

            yield LLMToken(
                token=token,
                accumulated_text=accumulated,
                is_tool_call=False,
                token_index=i,
            )

            # Variable delay for more realistic streaming
            delay = token_delay * random.uniform(0.5, 1.5)
            await asyncio.sleep(delay)

        logger.debug(f"FakeLLM: Generated {len(words)} tokens")

    async def close(self) -> None:
        """Stop generating."""
        self._closed = True
        logger.debug("FakeLLM: Closed")

    def reset(self) -> None:
        """Reset state."""
        self._closed = False


class EchoLLM(BaseLLMBackend):
    """
    Echo LLM - simply echoes back the user's last message.

    Useful for basic testing and debugging.
    """

    def __init__(self, model_name: str = "echo-llm"):
        super().__init__(model_name, supports_tools=False)

    async def chat(
        self,
        chat_ctx: ChatContext,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[LLMToken]:
        """Echo the last user message."""
        # Find last user message
        user_msg = "Hello!"
        for msg in reversed(chat_ctx.messages):
            if msg.role == "user":
                user_msg = msg.content
                break

        response = f"You said: {user_msg}"

        # Stream as single token
        yield LLMToken(
            token=response,
            accumulated_text=response,
            is_tool_call=False,
            token_index=0,
        )
