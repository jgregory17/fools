"""
Piper TTS Backend.

Adapter for Piper, a fast neural TTS system that runs locally.

Installation:
    pip install piper-tts

Or for offline use:
    Download models from https://github.com/rhasspy/piper/releases

Requirements:
    - piper-tts Python package
    - Voice model files (.onnx + .json) - auto-downloaded if not present
"""

from __future__ import annotations

import asyncio
import io
import logging
import struct
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import AsyncIterator, Optional

from ...core.events import TTSChunk
from ...core.interfaces import BaseTTSBackend

logger = logging.getLogger(__name__)

# Import piper-tts
try:
    from piper import PiperVoice
    from piper.download import ensure_voice_exists, find_voice, get_voices
    PIPER_AVAILABLE = True
except ImportError:
    PIPER_AVAILABLE = False
    PiperVoice = None
    logger.warning("piper-tts not installed. Run: pip install piper-tts")


class PiperTTS(BaseTTSBackend):
    """
    Piper TTS backend for local neural speech synthesis.

    Piper is a fast, local neural TTS system with many voices.
    Audio is synthesized locally without any API calls.

    Features:
    - Multiple voices and languages
    - Fast inference (real-time on CPU)
    - Consistent output quality
    - ONNX-based models
    - Streaming sentence-by-sentence synthesis
    - Cancellation support for barge-in
    """

    def __init__(
        self,
        voice: str = "en_US-lessac-medium",
        model_path: Optional[str] = None,
        data_dir: Optional[str] = None,
        sample_rate: int = 22050,  # Piper typically outputs 22050Hz
        num_channels: int = 1,
        length_scale: float = 1.0,  # Speaking rate (lower = faster)
        noise_scale: float = 0.667,
        noise_w: float = 0.8,
        sentence_silence: float = 0.2,  # Silence between sentences
    ):
        # Note: Piper outputs at 22050Hz typically
        # Pipeline may need to resample to 24kHz canonical format
        super().__init__(sample_rate, num_channels, voice)

        self._voice_name = voice
        self._model_path = model_path
        self._data_dir = data_dir or str(Path.home() / ".local" / "share" / "piper")
        self._length_scale = length_scale
        self._noise_scale = noise_scale
        self._noise_w = noise_w
        self._sentence_silence = sentence_silence

        self._piper_voice: Optional[PiperVoice] = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._closed = False

        if not PIPER_AVAILABLE:
            raise ImportError(
                "piper-tts is required for PiperTTS. "
                "Run: pip install piper-tts"
            )

        logger.info(f"PiperTTS initialized: voice={voice}")

    def _ensure_model_loaded(self) -> PiperVoice:
        """Lazy-load the Piper voice model."""
        if self._piper_voice is not None:
            return self._piper_voice

        logger.info(f"Loading Piper voice: {self._voice_name}")

        if self._model_path:
            # Load from specific path
            model_path = Path(self._model_path)
            config_path = model_path.with_suffix(".json")

            if not model_path.exists():
                raise FileNotFoundError(f"Piper model not found: {model_path}")

            self._piper_voice = PiperVoice.load(
                str(model_path),
                config_path=str(config_path) if config_path.exists() else None,
            )
        else:
            # Auto-download voice
            data_dir = Path(self._data_dir)
            data_dir.mkdir(parents=True, exist_ok=True)

            # Ensure voice is downloaded
            try:
                model_path, config_path = ensure_voice_exists(
                    self._voice_name,
                    [str(data_dir)],
                    str(data_dir),
                    None,  # voices dict (will be fetched)
                )
                self._piper_voice = PiperVoice.load(model_path, config_path=config_path)
            except Exception as e:
                logger.error(f"Failed to download/load voice {self._voice_name}: {e}")
                raise RuntimeError(
                    f"Failed to load Piper voice '{self._voice_name}'. "
                    f"Try: piper --download-dir {self._data_dir} --model {self._voice_name}"
                ) from e

        # Update sample rate from loaded model
        if hasattr(self._piper_voice, 'config') and self._piper_voice.config:
            self._sample_rate = self._piper_voice.config.sample_rate
            logger.info(f"Piper voice sample rate: {self._sample_rate}")

        logger.info(f"Piper voice loaded: {self._voice_name}")
        return self._piper_voice

    async def synthesize(
        self,
        text_stream: AsyncIterator[str],
    ) -> AsyncIterator[TTSChunk]:
        """
        Synthesize speech from streaming text.

        Implementation strategy:
        1. Buffer text until sentence boundary
        2. Synthesize each sentence
        3. Yield audio chunks
        4. Support cancellation at sentence boundaries
        """
        self._ensure_model_loaded()
        logger.debug("PiperTTS: Starting synthesis")

        accumulated_text = ""
        chunk_index = 0

        async for text_chunk in text_stream:
            if self._closed:
                logger.debug("PiperTTS: Cancelled during synthesis")
                break

            accumulated_text += text_chunk

            # Look for sentence boundaries
            sentences = self._split_sentences(accumulated_text)

            # Process complete sentences (all but the last, which may be incomplete)
            for sentence in sentences[:-1]:
                if not sentence.strip():
                    continue

                if self._closed:
                    break

                # Synthesize in thread pool
                audio_data = await self._synthesize_sentence(sentence)

                if audio_data:
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

        # Process remaining text (final sentence)
        if accumulated_text.strip() and not self._closed:
            audio_data = await self._synthesize_sentence(accumulated_text)

            if audio_data:
                yield TTSChunk(
                    audio_data=audio_data,
                    sample_rate=self._sample_rate,
                    num_channels=self._num_channels,
                    text_segment=accumulated_text,
                    is_final=True,
                    chunk_index=chunk_index,
                )

        logger.debug(f"PiperTTS: Generated {chunk_index + 1} chunks")

    async def _synthesize_sentence(self, text: str) -> bytes:
        """Synthesize a single sentence using Piper."""
        if not text.strip():
            return b""

        loop = asyncio.get_event_loop()

        def _sync_synthesize() -> bytes:
            # Synthesize to raw PCM16 audio
            audio_bytes = b""

            for audio_chunk in self._piper_voice.synthesize_stream_raw(
                text,
                length_scale=self._length_scale,
                noise_scale=self._noise_scale,
                noise_w=self._noise_w,
                sentence_silence=self._sentence_silence,
            ):
                audio_bytes += audio_chunk

            return audio_bytes

        return await loop.run_in_executor(self._executor, _sync_synthesize)

    async def synthesize_text(self, text: str) -> bytes:
        """
        Synthesize a complete text string to audio.

        Convenience method for non-streaming synthesis.
        """
        self._ensure_model_loaded()

        async def _text_gen():
            yield text

        audio_chunks = []
        async for chunk in self.synthesize(_text_gen()):
            audio_chunks.append(chunk.audio_data)

        return b"".join(audio_chunks)

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences at natural boundaries."""
        import re
        # Split on sentence-ending punctuation followed by space or end
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return sentences

    def cancel(self) -> None:
        """Cancel current synthesis (for barge-in)."""
        self._closed = True

    def reset(self) -> None:
        """Reset for new synthesis."""
        self._closed = False

    async def close(self) -> None:
        """Release resources."""
        self._closed = True
        self._executor.shutdown(wait=False)
        self._piper_voice = None
        logger.debug("PiperTTS: Closed")


class CoquiTTS(BaseTTSBackend):
    """
    Coqui TTS Backend.

    Alternative neural TTS using Coqui TTS library.
    Supports many models including VITS, YourTTS, etc.

    Installation:
        pip install TTS
    """

    def __init__(
        self,
        model_name: str = "tts_models/en/ljspeech/vits",
        sample_rate: int = 22050,
        num_channels: int = 1,
        voice: str = "default",
        gpu: bool = False,
    ):
        super().__init__(sample_rate, num_channels, voice)
        self._model_name = model_name
        self._gpu = gpu
        self._tts = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._closed = False

        logger.info(f"CoquiTTS initialized: {model_name}")

    def _ensure_model_loaded(self):
        """Load the Coqui TTS model."""
        if self._tts is not None:
            return self._tts

        try:
            from TTS.api import TTS
            self._tts = TTS(self._model_name, gpu=self._gpu)
            logger.info(f"Coqui TTS model loaded: {self._model_name}")
        except ImportError:
            raise ImportError(
                "TTS (Coqui) is required for CoquiTTS. "
                "Run: pip install TTS"
            )

        return self._tts

    async def synthesize(
        self,
        text_stream: AsyncIterator[str],
    ) -> AsyncIterator[TTSChunk]:
        """Synthesize speech from streaming text."""
        self._ensure_model_loaded()

        accumulated_text = ""
        chunk_index = 0

        async for text_chunk in text_stream:
            if self._closed:
                break

            accumulated_text += text_chunk

            # Process on sentence boundaries
            sentences = self._split_sentences(accumulated_text)

            for sentence in sentences[:-1]:
                if not sentence.strip() or self._closed:
                    continue

                audio_data = await self._synthesize_sentence(sentence)

                if audio_data:
                    yield TTSChunk(
                        audio_data=audio_data,
                        sample_rate=self._sample_rate,
                        num_channels=self._num_channels,
                        text_segment=sentence,
                        is_final=False,
                        chunk_index=chunk_index,
                    )
                    chunk_index += 1

            accumulated_text = sentences[-1] if sentences else ""

        # Final chunk
        if accumulated_text.strip() and not self._closed:
            audio_data = await self._synthesize_sentence(accumulated_text)

            if audio_data:
                yield TTSChunk(
                    audio_data=audio_data,
                    sample_rate=self._sample_rate,
                    num_channels=self._num_channels,
                    text_segment=accumulated_text,
                    is_final=True,
                    chunk_index=chunk_index,
                )

    async def _synthesize_sentence(self, text: str) -> bytes:
        """Synthesize using Coqui TTS."""
        loop = asyncio.get_event_loop()

        def _sync():
            # TTS returns numpy array
            wav = self._tts.tts(text)

            # Convert float32 to PCM16 bytes
            import numpy as np
            wav_int16 = (np.array(wav) * 32767).astype(np.int16)
            return wav_int16.tobytes()

        return await loop.run_in_executor(self._executor, _sync)

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences."""
        import re
        return re.split(r'(?<=[.!?])\s+', text)

    def cancel(self) -> None:
        """Cancel current synthesis."""
        self._closed = True

    def reset(self) -> None:
        """Reset for new synthesis."""
        self._closed = False

    async def close(self) -> None:
        """Release resources."""
        self._closed = True
        self._executor.shutdown(wait=False)
        self._tts = None
