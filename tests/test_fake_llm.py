"""
Unit tests for FakeLLM implementation.

Tests ensure:
- Type safety
- Proper async generator behavior
- Response generation logic
- Debug logging functionality
"""

import asyncio
import pytest
from typing import List

from src.agent_playground.fake_llm import (
    FakeLLM,
    FakeLLMConfig,
    create_fake_llm,
    create_test_llm_with_errors,
)
from src.agent_playground.core.interfaces import ChatContext
from src.agent_playground.core.events import LLMToken


class TestFakeLLMConfig:
    """Test FakeLLMConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = FakeLLMConfig()
        assert config.response_delay_ms == 100
        assert config.streaming_chunk_delay_ms == 20
        assert config.response_style == "helpful"
        assert config.error_rate == 0.0

    def test_custom_config(self):
        """Test custom configuration values."""
        config = FakeLLMConfig(
            response_delay_ms=50,
            streaming_chunk_delay_ms=10,
            response_style="technical",
            error_rate=0.1,
        )
        assert config.response_delay_ms == 50
        assert config.streaming_chunk_delay_ms == 10
        assert config.response_style == "technical"
        assert config.error_rate == 0.1


class TestFakeLLM:
    """Test FakeLLM class."""

    def test_initialization(self):
        """Test LLM initialization."""
        llm = FakeLLM(model="test-model", temperature=0.8)
        assert llm.model_name == "test-model"
        assert llm.supports_tools is False
        assert llm._response_counter == 0

    def test_initialization_with_config(self):
        """Test initialization with custom config."""
        config = FakeLLMConfig(response_style="creative")
        llm = FakeLLM(model="test", config=config)
        assert llm.config.response_style == "creative"

    @pytest.mark.asyncio
    async def test_chat_returns_async_generator(self):
        """Test that chat() returns an async generator."""
        llm = FakeLLM()
        chat_ctx = ChatContext()
        chat_ctx.add_user_message("Hello")

        result = llm.chat(chat_ctx=chat_ctx)
        # Check it's an async generator
        assert hasattr(result, '__anext__')

    @pytest.mark.asyncio
    async def test_chat_yields_llm_tokens(self):
        """Test that chat yields LLMToken objects."""
        llm = FakeLLM()
        chat_ctx = ChatContext()
        chat_ctx.add_user_message("Hello")

        tokens: List[LLMToken] = []
        async for token in llm.chat(chat_ctx=chat_ctx):
            tokens.append(token)
            # Verify each token is LLMToken type
            assert isinstance(token, LLMToken)
            assert isinstance(token.token, str)
            assert isinstance(token.accumulated_text, str)
            assert isinstance(token.is_tool_call, bool)
            assert isinstance(token.token_index, int)

        # Should have multiple tokens
        assert len(tokens) > 0

    @pytest.mark.asyncio
    async def test_chat_accumulates_text(self):
        """Test that accumulated_text builds up correctly."""
        llm = FakeLLM()
        chat_ctx = ChatContext()
        chat_ctx.add_user_message("Hello")

        accumulated_parts = []
        async for token in llm.chat(chat_ctx=chat_ctx):
            accumulated_parts.append(token.accumulated_text)

        # Each accumulated text should be longer than the previous
        for i in range(1, len(accumulated_parts)):
            assert len(accumulated_parts[i]) >= len(accumulated_parts[i - 1])

        # Last token should have complete response
        assert len(accumulated_parts[-1]) > 0

    @pytest.mark.asyncio
    async def test_chat_greeting_response(self):
        """Test greeting detection and response."""
        llm = FakeLLM()
        chat_ctx = ChatContext()
        chat_ctx.add_user_message("Hello")

        full_response = ""
        async for token in llm.chat(chat_ctx=chat_ctx):
            full_response = token.accumulated_text

        # Should contain greeting-type response
        assert len(full_response) > 0
        # Response should be one of the greeting responses
        assert any(
            word in full_response.lower()
            for word in ["hello", "hi", "help", "assist"]
        )

    @pytest.mark.asyncio
    async def test_chat_question_response(self):
        """Test question detection and response."""
        llm = FakeLLM()
        chat_ctx = ChatContext()
        chat_ctx.add_user_message("What is the weather like?")

        full_response = ""
        async for token in llm.chat(chat_ctx=chat_ctx):
            full_response = token.accumulated_text

        assert len(full_response) > 0

    @pytest.mark.asyncio
    async def test_chat_increments_counter(self):
        """Test that response counter increments."""
        llm = FakeLLM()
        chat_ctx = ChatContext()

        initial_count = llm._response_counter

        chat_ctx.add_user_message("Test 1")
        async for _ in llm.chat(chat_ctx=chat_ctx):
            pass

        assert llm._response_counter == initial_count + 1

        chat_ctx.add_user_message("Test 2")
        async for _ in llm.chat(chat_ctx=chat_ctx):
            pass

        assert llm._response_counter == initial_count + 2

    @pytest.mark.asyncio
    async def test_chat_with_error_rate(self):
        """Test simulated errors."""
        config = FakeLLMConfig(error_rate=1.0)  # Always error
        llm = FakeLLM(config=config)
        chat_ctx = ChatContext()
        chat_ctx.add_user_message("Test")

        with pytest.raises(Exception, match="Simulated LLM error"):
            async for _ in llm.chat(chat_ctx=chat_ctx):
                pass

    @pytest.mark.asyncio
    async def test_response_delay(self):
        """Test that response delay is applied."""
        config = FakeLLMConfig(response_delay_ms=100)
        llm = FakeLLM(config=config)
        chat_ctx = ChatContext()
        chat_ctx.add_user_message("Test")

        start = asyncio.get_event_loop().time()
        async for _ in llm.chat(chat_ctx=chat_ctx):
            break  # Just get first token
        elapsed = asyncio.get_event_loop().time() - start

        # Should have at least response_delay_ms delay
        assert elapsed >= 0.1  # 100ms

    def test_split_into_chunks(self):
        """Test text splitting into chunks."""
        llm = FakeLLM()
        text = "This is a test message with several words in it"
        chunks = llm._split_into_chunks(text)

        # Should produce multiple chunks
        assert len(chunks) > 1

        # Joining chunks should reproduce original (with spaces)
        rejoined = "".join(chunks)
        assert rejoined == text

    def test_generate_response_greeting(self):
        """Test response generation for greetings."""
        llm = FakeLLM()
        response = llm._generate_response("hello")
        assert len(response) > 0

    def test_generate_response_question(self):
        """Test response generation for questions."""
        llm = FakeLLM()
        response = llm._generate_response("what is the weather?")
        assert len(response) > 0

    def test_generate_response_technical_style(self):
        """Test technical response style includes timestamp."""
        config = FakeLLMConfig(response_style="technical")
        llm = FakeLLM(config=config)
        response = llm._generate_response("test question")
        # Technical responses should include timestamp
        assert ":" in response  # Time format includes colons


class TestFactoryFunctions:
    """Test factory functions."""

    def test_create_fake_llm_helpful(self):
        """Test creating helpful LLM."""
        llm = create_fake_llm("helpful")
        assert isinstance(llm, FakeLLM)
        assert llm.config.response_style == "helpful"
        assert llm.model_name == "fake-helpful"

    def test_create_fake_llm_technical(self):
        """Test creating technical LLM."""
        llm = create_fake_llm("technical")
        assert llm.config.response_style == "technical"
        assert llm.model_name == "fake-technical"

    def test_create_test_llm_with_errors(self):
        """Test creating LLM with error rate."""
        llm = create_test_llm_with_errors(error_rate=0.5)
        assert llm.config.error_rate == 0.5
        assert llm.model_name == "fake-with-errors"


class TestIntegration:
    """Integration tests."""

    @pytest.mark.asyncio
    async def test_multiple_messages_in_context(self):
        """Test handling multiple messages in chat context."""
        llm = FakeLLM()
        chat_ctx = ChatContext()
        chat_ctx.add_user_message("First message")
        chat_ctx.add_assistant_message("First response")
        chat_ctx.add_user_message("Second message")

        tokens = []
        async for token in llm.chat(chat_ctx=chat_ctx):
            tokens.append(token)

        # Should process successfully
        assert len(tokens) > 0
        assert tokens[-1].accumulated_text != ""

    @pytest.mark.asyncio
    async def test_empty_context(self):
        """Test handling empty chat context."""
        llm = FakeLLM()
        chat_ctx = ChatContext()

        tokens = []
        async for token in llm.chat(chat_ctx=chat_ctx):
            tokens.append(token)

        # Should still generate a response (default category)
        assert len(tokens) > 0

    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        """Test multiple concurrent chat requests."""
        llm = FakeLLM()

        async def make_request(message: str) -> str:
            chat_ctx = ChatContext()
            chat_ctx.add_user_message(message)
            full_response = ""
            async for token in llm.chat(chat_ctx=chat_ctx):
                full_response = token.accumulated_text
            return full_response

        # Run multiple requests concurrently
        results = await asyncio.gather(
            make_request("Hello"),
            make_request("What is the weather?"),
            make_request("Goodbye"),
        )

        # All should complete successfully
        assert len(results) == 3
        assert all(len(r) > 0 for r in results)


if __name__ == "__main__":
    # Run with: pytest tests/test_fake_llm.py -v
    pytest.main([__file__, "-v"])
