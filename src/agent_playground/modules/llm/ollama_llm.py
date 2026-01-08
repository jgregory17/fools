"""
Ollama LLM Backend.

Adapter for Ollama, a local LLM server that supports many open models
including Llama, Mistral, Phi, etc.

Installation:
    1. Install Ollama: https://ollama.ai/download
    2. Pull a model: ollama pull llama3.2
    3. Start server: ollama serve (usually auto-starts)

Requirements:
    - pip install httpx (for async HTTP)
    - Ollama server running on localhost:11434
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

from ...core.events import LLMToken
from ...core.interfaces import BaseLLMBackend, ChatContext, ToolDefinition

logger = logging.getLogger(__name__)

# Import httpx - required dependency
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    logger.warning("httpx not installed. Run: pip install httpx")


class OllamaLLM(BaseLLMBackend):
    """
    Ollama LLM backend for local inference.

    Connects to a local Ollama server for LLM inference.
    Supports streaming responses and tool calling (for supported models).

    Features:
    - Multiple model support (llama3.2, mistral, phi, etc.)
    - Streaming token generation
    - Tool/function calling (model-dependent)
    - Customizable system prompts
    - Cancellation support for barge-in
    """

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
        supports_tools: bool = True,
        timeout: float = 60.0,
        num_ctx: int = 4096,  # Context window size
        num_predict: int = 512,  # Max tokens to generate
    ):
        super().__init__(model, supports_tools)

        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._num_ctx = num_ctx
        self._num_predict = num_predict
        self._client: Optional[httpx.AsyncClient] = None
        self._closed = False
        self._current_request: Optional[asyncio.Task] = None

        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for OllamaLLM. Run: pip install httpx")

        logger.info(f"OllamaLLM initialized: model={model}, base_url={base_url}")

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Lazy-initialize the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout, connect=10.0),
            )
        return self._client

    async def check_health(self) -> bool:
        """Check if Ollama server is available and model is loaded."""
        try:
            client = await self._ensure_client()
            response = await client.get("/api/tags")
            if response.status_code == 200:
                data = response.json()
                models = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
                if self._model_name.split(":")[0] in models:
                    logger.info(f"Ollama model {self._model_name} is available")
                    return True
                else:
                    logger.warning(
                        f"Model {self._model_name} not found. "
                        f"Available: {models}. Run: ollama pull {self._model_name}"
                    )
                    return False
            return False
        except Exception as e:
            logger.error(f"Ollama health check failed: {e}")
            return False

    async def chat(
        self,
        chat_ctx: ChatContext,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[LLMToken]:
        """
        Generate a response using Ollama.

        Uses the /api/chat endpoint with streaming.
        Supports cancellation for barge-in scenarios.
        """
        if self._closed:
            return

        client = await self._ensure_client()
        logger.debug(f"OllamaLLM: Generating response with {len(chat_ctx.messages)} messages")

        # Convert ChatContext to Ollama format
        messages = self._convert_messages(chat_ctx)

        # Build request payload
        payload = {
            "model": self._model_name,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_ctx": self._num_ctx,
                "num_predict": max_tokens or self._num_predict,
            },
        }

        # Add tools if supported and provided
        if tools and self._supports_tools:
            payload["tools"] = self._convert_tools(tools)

        accumulated = ""
        token_index = 0

        try:
            async with client.stream(
                "POST",
                "/api/chat",
                json=payload,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if self._closed:
                        logger.debug("OllamaLLM: Cancelled during streaming")
                        break

                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Extract token from response
                    if "message" in data and "content" in data["message"]:
                        token = data["message"]["content"]
                        if token:
                            accumulated += token
                            yield LLMToken(
                                token=token,
                                accumulated_text=accumulated,
                                is_tool_call=False,
                                token_index=token_index,
                            )
                            token_index += 1

                    # Check for tool calls
                    if "message" in data and "tool_calls" in data["message"]:
                        for tool_call in data["message"]["tool_calls"]:
                            func = tool_call.get("function", {})
                            yield LLMToken(
                                token="",
                                accumulated_text=accumulated,
                                is_tool_call=True,
                                tool_name=func.get("name"),
                                tool_arguments=func.get("arguments", {}),
                                token_index=token_index,
                            )
                            token_index += 1

                    # Check for done
                    if data.get("done"):
                        break

            logger.debug(f"OllamaLLM: Generated {token_index} tokens")

        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.ConnectError as e:
            logger.error(f"Ollama connection failed. Is Ollama running? Error: {e}")
            raise ConnectionError(
                f"Cannot connect to Ollama at {self._base_url}. "
                "Ensure Ollama is running: ollama serve"
            ) from e
        except asyncio.CancelledError:
            logger.debug("OllamaLLM: Request cancelled")
            raise

    def cancel(self) -> None:
        """Cancel current generation (for barge-in)."""
        self._closed = True

    def reset(self) -> None:
        """Reset for new generation."""
        self._closed = False

    def _convert_messages(self, chat_ctx: ChatContext) -> list[dict[str, Any]]:
        """Convert ChatContext to Ollama message format."""
        messages = []

        for msg in chat_ctx.messages:
            ollama_msg = {
                "role": msg.role,
                "content": msg.content,
            }

            # Handle tool results
            if msg.role == "tool" and msg.tool_call_id:
                ollama_msg["tool_call_id"] = msg.tool_call_id

            messages.append(ollama_msg)

        return messages

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """Convert ToolDefinitions to Ollama tool format."""
        ollama_tools = []

        for tool in tools:
            ollama_tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })

        return ollama_tools

    async def close(self) -> None:
        """Close the HTTP client."""
        self._closed = True

        if self._client is not None:
            await self._client.aclose()
            self._client = None

        logger.debug("OllamaLLM: Closed")


class VLLMAdapter(BaseLLMBackend):
    """
    vLLM Backend using OpenAI-compatible API.

    Adapter for vLLM local server, optimized for high-throughput inference.
    Uses the OpenAI-compatible /v1/chat/completions endpoint.

    Installation:
        pip install vllm
        # Start server:
        vllm serve meta-llama/Llama-3.2-3B-Instruct --port 8000
    """

    def __init__(
        self,
        model: str = "meta-llama/Llama-3.2-3B-Instruct",
        base_url: str = "http://localhost:8000",
        api_key: str = "EMPTY",  # vLLM doesn't require real API key
        timeout: float = 60.0,
        max_tokens: int = 512,
    ):
        super().__init__(model, supports_tools=True)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._client: Optional[httpx.AsyncClient] = None
        self._closed = False

        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for VLLMAdapter. Run: pip install httpx")

        logger.info(f"VLLMAdapter initialized: model={model}, base_url={base_url}")

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Lazy-initialize the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout, connect=10.0),
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        return self._client

    async def chat(
        self,
        chat_ctx: ChatContext,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[LLMToken]:
        """Generate response using vLLM's OpenAI-compatible API."""
        if self._closed:
            return

        client = await self._ensure_client()

        # Convert to OpenAI format
        messages = [{"role": m.role, "content": m.content} for m in chat_ctx.messages]

        payload = {
            "model": self._model_name,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens or self._max_tokens,
        }

        accumulated = ""
        token_index = 0

        try:
            async with client.stream(
                "POST",
                "/v1/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if self._closed:
                        break

                    if not line or not line.startswith("data: "):
                        continue

                    data_str = line[6:]  # Remove "data: " prefix
                    if data_str == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        token = delta.get("content", "")

                        if token:
                            accumulated += token
                            yield LLMToken(
                                token=token,
                                accumulated_text=accumulated,
                                is_tool_call=False,
                                token_index=token_index,
                            )
                            token_index += 1
                    except json.JSONDecodeError:
                        continue

        except httpx.HTTPStatusError as e:
            logger.error(f"vLLM HTTP error: {e}")
            raise
        except httpx.ConnectError as e:
            logger.error(f"vLLM connection failed: {e}")
            raise ConnectionError(
                f"Cannot connect to vLLM at {self._base_url}. "
                "Ensure vLLM server is running."
            ) from e

    def cancel(self) -> None:
        """Cancel current generation."""
        self._closed = True

    def reset(self) -> None:
        """Reset for new generation."""
        self._closed = False

    async def close(self) -> None:
        """Close the HTTP client."""
        self._closed = True
        if self._client:
            await self._client.aclose()
            self._client = None
