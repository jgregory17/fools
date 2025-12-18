"""
Fake ASR Backend for Testing.

Returns canned transcripts or echoes audio duration.
Useful for testing the pipeline without real ASR inference.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

from agent_playground.core.events import AudioFrameIn
from agent_playground.core.interfaces import BaseASRBackend, SpeechEvent

logger = logging.getLogger(__name__)


class FakeASR(BaseASRBackend):
    """
    Fake ASR backend for testing and development.

    Features:
    - Configurable latency simulation
    - Canned responses or echo mode
    - Simulates partial transcripts before final
    """

    def __init__(
        self,
        sample_rate: int = 24000,
        num_channels: int = 1,
        canned_responses: list[str] | None = None,
        latency_ms: float = 100.0,
        simulate_partials: bool = True,
        silence_threshold_ms: float = 500.0,
    ):
        super().__init__(sample_rate, num_channels)

        self._canned_responses = canned_responses or [
            "Hello, how can I help you?",
            "That sounds interesting.",
            "Could you tell me more about that?",
            "I understand.",
        ]
        self._response_index = 0
        self._latency_ms = latency_ms
        self._simulate_partials = simulate_partials
        self._silence_threshold_ms = silence_threshold_ms
        self._closed = False

    async def stream(
        self,
        audio_stream: AsyncIterator[AudioFrameIn],
    ) -> AsyncIterator[SpeechEvent]:
        """
        Process audio stream and yield fake transcription events.

        Simulates realistic ASR behavior:
        1. Accumulates audio frames
        2. After silence threshold, emits partial then final
        """
        logger.debug("FakeASR: Starting stream processing")

        accumulated_duration_ms = 0.0
        last_audio_time = time.time()
        is_speaking = False

        async for frame in audio_stream:
            if self._closed:
                break

            current_time = time.time()
            frame_duration = frame.duration_ms

            accumulated_duration_ms += frame_duration

            # Simulate speech detection (any audio = speech)
            if frame.data and len(frame.data) > 0:
                # Check if there's actual audio (not silence)
                # In real impl, would use VAD here
                is_speaking = True
                last_audio_time = current_time

            # Check for silence (end of utterance)
            silence_duration_ms = (current_time - last_audio_time) * 1000
            if is_speaking and silence_duration_ms > self._silence_threshold_ms:
                # End of utterance detected
                is_speaking = False

                # Simulate latency
                await asyncio.sleep(self._latency_ms / 1000)

                # Get next canned response
                response = self._canned_responses[self._response_index]
                self._response_index = (self._response_index + 1) % len(self._canned_responses)

                # Emit partial if configured
                if self._simulate_partials:
                    words = response.split()
                    for i in range(1, len(words)):
                        partial_text = " ".join(words[:i])
                        yield SpeechEvent(
                            text=partial_text,
                            is_final=False,
                            confidence=0.7,
                        )
                        await asyncio.sleep(0.05)  # 50ms between partials

                # Emit final transcript
                yield SpeechEvent(
                    text=response,
                    is_final=True,
                    confidence=0.95,
                )

                logger.debug(f"FakeASR: Yielded transcript: {response}")
                accumulated_duration_ms = 0.0

    async def close(self) -> None:
        """Stop processing."""
        self._closed = True
        logger.debug("FakeASR: Closed")

    def reset(self) -> None:
        """Reset the response index."""
        self._response_index = 0
        self._closed = False
