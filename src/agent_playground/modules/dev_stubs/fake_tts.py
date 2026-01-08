"""
Fake TTS Backend for Testing.

Generates silence or simple tones instead of real speech.
Useful for testing the pipeline without real TTS inference.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
from typing import AsyncIterator

from agent_playground.core.events import TTSChunk
from agent_playground.core.interfaces import BaseTTSBackend

logger = logging.getLogger(__name__)


class FakeTTS(BaseTTSBackend):
    """
    Fake TTS backend for testing and development.

    Features:
    - Generates silence or configurable tone
    - Simulates synthesis latency
    - Produces properly formatted audio frames
    """

    def __init__(
        self,
        sample_rate: int = 24000,
        num_channels: int = 1,
        voice: str = "fake-voice",
        output_mode: str = "silence",  # "silence", "tone", "noise"
        tone_frequency: float = 440.0,  # Hz (A4 note)
        latency_ms_per_word: float = 50.0,
        chunk_duration_ms: float = 100.0,
    ):
        super().__init__(sample_rate, num_channels, voice)

        self._output_mode = output_mode
        self._tone_frequency = tone_frequency
        self._latency_ms_per_word = latency_ms_per_word
        self._chunk_duration_ms = chunk_duration_ms
        self._closed = False

    async def synthesize(
        self,
        text_stream: AsyncIterator[str],
    ) -> AsyncIterator[TTSChunk]:
        """
        Generate fake audio from streaming text.

        Accumulates text into sentences, then generates audio chunks.
        """
        logger.debug("FakeTTS: Starting synthesis")

        accumulated_text = ""
        chunk_index = 0

        async for text_chunk in text_stream:
            if self._closed:
                break

            accumulated_text += text_chunk

            # Check for sentence boundaries (simple heuristic)
            sentences = self._split_sentences(accumulated_text)

            for sentence in sentences[:-1]:  # All complete sentences
                # Simulate synthesis time
                word_count = len(sentence.split())
                latency = (word_count * self._latency_ms_per_word) / 1000
                await asyncio.sleep(latency)

                # Generate audio for this sentence
                audio_data = self._generate_audio(sentence)

                yield TTSChunk(
                    audio_data=audio_data,
                    sample_rate=self._sample_rate,
                    num_channels=self._num_channels,
                    text_segment=sentence,
                    is_final=False,
                    chunk_index=chunk_index,
                )
                chunk_index += 1

            # Keep incomplete sentence
            accumulated_text = sentences[-1] if sentences else ""

        # Process remaining text
        if accumulated_text.strip():
            word_count = len(accumulated_text.split())
            latency = (word_count * self._latency_ms_per_word) / 1000
            await asyncio.sleep(latency)

            audio_data = self._generate_audio(accumulated_text)

            yield TTSChunk(
                audio_data=audio_data,
                sample_rate=self._sample_rate,
                num_channels=self._num_channels,
                text_segment=accumulated_text,
                is_final=True,
                chunk_index=chunk_index,
            )

        logger.debug(f"FakeTTS: Generated {chunk_index + 1} chunks")

    async def synthesize_text(self, text: str) -> AsyncIterator[TTSChunk]:
        """Synthesize a complete text string."""
        async def text_iter() -> AsyncIterator[str]:
            yield text

        async for chunk in self.synthesize(text_iter()):
            yield chunk

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences (simple implementation)."""
        import re
        # Split on sentence-ending punctuation
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return sentences

    def _generate_audio(self, text: str) -> bytes:
        """Generate audio data based on output mode."""
        # Calculate duration based on text length
        word_count = max(1, len(text.split()))
        duration_ms = word_count * 150  # ~150ms per word (average speech rate)

        samples_count = int((duration_ms / 1000) * self._sample_rate)

        if self._output_mode == "silence":
            return self._generate_silence(samples_count)
        elif self._output_mode == "tone":
            return self._generate_tone(samples_count)
        elif self._output_mode == "noise":
            return self._generate_noise(samples_count)
        else:
            return self._generate_silence(samples_count)

    def _generate_silence(self, samples_count: int) -> bytes:
        """Generate silent audio (zeros)."""
        return b'\x00\x00' * samples_count

    def _generate_tone(self, samples_count: int) -> bytes:
        """Generate a pure sine tone."""
        samples = []
        for i in range(samples_count):
            t = i / self._sample_rate
            # Sine wave with envelope (fade in/out)
            envelope = min(1.0, min(i, samples_count - i) / (self._sample_rate * 0.01))
            value = int(16000 * envelope * math.sin(2 * math.pi * self._tone_frequency * t))
            samples.append(struct.pack('<h', max(-32768, min(32767, value))))
        return b''.join(samples)

    def _generate_noise(self, samples_count: int) -> bytes:
        """Generate white noise (for testing audio pipeline)."""
        import random
        samples = []
        for _ in range(samples_count):
            value = random.randint(-8000, 8000)  # Low volume noise
            samples.append(struct.pack('<h', value))
        return b''.join(samples)

    async def close(self) -> None:
        """Stop synthesis."""
        self._closed = True
        logger.debug("FakeTTS: Closed")

    def reset(self) -> None:
        """Reset state."""
        self._closed = False
