"""
Fake LLM implementation for local testing without real models.
Provides instant responses without any CPU-intensive inference.
"""

import asyncio
import logging
import random
from typing import AsyncGenerator, Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

from .core.interfaces import BaseLLMBackend, ChatContext, ChatMessage, ToolDefinition
from .core.events import LLMToken

logger = logging.getLogger(__name__)


@dataclass
class FakeLLMConfig:
    """Configuration for fake LLM behavior."""
    response_delay_ms: int = 100  # Simulate processing time
    streaming_chunk_delay_ms: int = 20  # Delay between streaming chunks
    response_style: str = "helpful"  # helpful, brief, creative, technical
    error_rate: float = 0.0  # Probability of simulating an error (0.0-1.0)


class FakeLLM(BaseLLMBackend):
    """
    Fake LLM that generates pre-programmed responses for testing.
    Mimics the LLM backend interface without requiring any actual model.
    """

    def __init__(
        self,
        *,
        model: str = "fake-model",
        temperature: float = 0.7,
        config: Optional[FakeLLMConfig] = None,
    ):
        # Initialize parent
        super().__init__(model_name=model, supports_tools=False)
        
        self.config = config or FakeLLMConfig()
        self._response_counter = 0
        
        # Pre-defined responses for common patterns
        self.responses = {
            "greeting": [
                "Hello! How can I help you today?",
                "Hi there! What can I assist you with?",
                "Hey! I'm here to help. What's on your mind?",
                "Good to hear from you! How may I be of assistance?",
            ],
            "farewell": [
                "Goodbye! Have a great day!",
                "Take care! Feel free to come back anytime.",
                "See you later! It was nice chatting with you.",
                "Bye for now! Hope I was helpful!",
            ],
            "question": [
                "That's an interesting question. Let me think about that for a moment.",
                "I understand what you're asking. Here's what I think...",
                "Great question! From my perspective...",
                "Let me help you with that. The answer depends on a few factors.",
            ],
            "command": [
                "I'll help you with that right away.",
                "Sure, I can do that for you.",
                "No problem! Let me take care of that.",
                "Absolutely! Here's what I'll do...",
            ],
            "default": [
                "I understand. Could you tell me more about that?",
                "That's interesting. What would you like to know?",
                "I see what you mean. How can I help further?",
                "Thanks for sharing that. What's your next question?",
            ],
            "error": [
                "I'm having a bit of trouble understanding. Could you rephrase that?",
                "Sorry, I didn't quite catch that. Can you try again?",
                "Hmm, I'm not sure I understood correctly. Could you clarify?",
            ],
            "creative": [
                "Imagine if we could solve that in a completely different way...",
                "Here's a creative take on your question...",
                "Let me paint you a picture of how this could work...",
                "What if we approached this from a unique angle?",
            ],
            "technical": [
                "From a technical standpoint, the implementation would involve...",
                "The technical details are quite interesting here...",
                "Let me break down the technical aspects for you...",
                "Technically speaking, this works because...",
            ],
        }
        
        logger.info(f"Initialized FakeLLM with model={model}, style={self.config.response_style}")
    
    async def chat(
        self,
        chat_ctx: ChatContext,
        tools: Optional[List[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncGenerator[LLMToken, None]:
        """
        Generate a fake chat response based on the input.
        Yields LLMToken events as they are generated.
        """
        # Log incoming request
        logger.debug("[FAKE_LLM] chat() called")
        logger.debug(f"[FAKE_LLM] Message count: {len(chat_ctx.messages)}")

        # Simulate processing delay
        await asyncio.sleep(self.config.response_delay_ms / 1000.0)

        # Check for simulated errors
        if random.random() < self.config.error_rate:
            logger.error("[FAKE_LLM] Simulated error triggered")
            raise Exception("Simulated LLM error for testing")

        # Get the last user message
        last_message = ""
        for msg in reversed(chat_ctx.messages):
            if msg.role == "user":
                last_message = msg.content.lower() if isinstance(msg.content, str) else ""
                break

        logger.debug(f"[FAKE_LLM] User input: '{last_message[:100]}{'...' if len(last_message) > 100 else ''}'")

        # Select response based on message content
        response = self._generate_response(last_message)

        # Log the interaction
        self._response_counter += 1
        logger.debug(f"[FAKE_LLM] Response #{self._response_counter}: '{response[:100]}{'...' if len(response) > 100 else ''}'")

        # Stream tokens
        chunks = self._split_into_chunks(response)
        logger.debug(f"[FAKE_LLM] Streaming {len(chunks)} chunks")
        accumulated = ""

        for idx, chunk in enumerate(chunks):
            await asyncio.sleep(self.config.streaming_chunk_delay_ms / 1000.0)
            accumulated += chunk

            logger.debug(f"[FAKE_LLM] Chunk {idx+1}/{len(chunks)}: '{chunk[:50]}{'...' if len(chunk) > 50 else ''}'")

            yield LLMToken(
                token=chunk,
                accumulated_text=accumulated,
                is_tool_call=False,
                token_index=idx,
            )

        logger.debug(f"[FAKE_LLM] Streaming complete. Total tokens: {len(chunks)}")

    def _split_into_chunks(self, text: str) -> List[str]:
        """Split response into streaming chunks for realistic simulation."""
        # Split into words and create chunks of 3-5 words
        words = text.split()
        chunks = []

        i = 0
        while i < len(words):
            chunk_size = random.randint(3, 5)
            chunk_words = words[i:i + chunk_size]
            chunk = " ".join(chunk_words)

            # Add space unless it's the last chunk
            if i + chunk_size < len(words):
                chunk += " "

            chunks.append(chunk)
            i += chunk_size

        return chunks
    
    def _generate_response(self, user_input: str) -> str:
        """Generate a contextual fake response based on user input."""

        # Check for specific patterns
        response_category = "default"
        if any(word in user_input for word in ["hello", "hi", "hey", "greetings"]):
            responses = self.responses["greeting"]
            response_category = "greeting"
        elif any(word in user_input for word in ["bye", "goodbye", "see you", "farewell"]):
            responses = self.responses["farewell"]
            response_category = "farewell"
        elif "?" in user_input or any(word in user_input for word in ["what", "how", "why", "when", "where", "who"]):
            responses = self.responses["question"]
            response_category = "question"
        elif any(word in user_input for word in ["please", "can you", "could you", "would you", "help me"]):
            responses = self.responses["command"]
            response_category = "command"
        elif self.config.response_style == "creative":
            responses = self.responses["creative"]
            response_category = "creative"
        elif self.config.response_style == "technical":
            responses = self.responses["technical"]
            response_category = "technical"
        else:
            responses = self.responses["default"]

        logger.debug(f"[FAKE_LLM] Selected category: {response_category}")

        # Add variety with random selection
        base_response = random.choice(responses)

        # Add contextual elements based on input length
        if len(user_input) > 100:
            base_response += " I notice you've shared quite a bit of detail."
        elif len(user_input) < 20:
            base_response += " Feel free to elaborate if you'd like."

        # Add timestamp for variety (helps distinguish responses in testing)
        if self.config.response_style == "technical":
            timestamp = datetime.now().strftime("%H:%M:%S")
            base_response += f" [Response generated at {timestamp}]"

        logger.debug(f"[FAKE_LLM] Generated response length: {len(base_response)} chars")
        return base_response


def create_fake_llm(response_style: str = "helpful") -> FakeLLM:
    """
    Factory function to create a fake LLM with specific behavior.
    
    Args:
        response_style: Style of responses - "helpful", "brief", "creative", "technical"
    
    Returns:
        Configured FakeLLM instance
    """
    config = FakeLLMConfig(
        response_delay_ms=50 if response_style == "brief" else 100,
        streaming_chunk_delay_ms=10 if response_style == "brief" else 20,
        response_style=response_style,
        error_rate=0.0,  # No errors by default
    )
    
    return FakeLLM(
        model=f"fake-{response_style}",
        config=config,
    )


def create_test_llm_with_errors(error_rate: float = 0.1) -> FakeLLM:
    """
    Create a fake LLM that occasionally fails for error testing.
    
    Args:
        error_rate: Probability of error (0.0 to 1.0)
    
    Returns:
        FakeLLM configured to simulate errors
    """
    config = FakeLLMConfig(
        response_delay_ms=100,
        streaming_chunk_delay_ms=20,
        response_style="helpful",
        error_rate=error_rate,
    )
    
    return FakeLLM(
        model="fake-with-errors",
        config=config,
    )


# Example usage patterns for testing
if __name__ == "__main__":
    async def test_fake_llm():
        """Test the fake LLM implementation."""

        # Create fake LLM
        llm = create_fake_llm("helpful")

        # Create a chat context
        chat_ctx = ChatContext()
        chat_ctx.add_user_message("Hello! How are you?")

        # Get response stream
        print("Streaming response:")
        full_response = ""
        async for token in llm.chat(chat_ctx=chat_ctx):
            print(token.token, end="", flush=True)
            full_response = token.accumulated_text

        print(f"\n\nFull response: {full_response}")

    # Run test
    asyncio.run(test_fake_llm())