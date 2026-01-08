"""
Faster-Whisper ASR Backend.

Adapter for faster-whisper, a high-performance Whisper implementation
using CTranslate2 for inference.

Installation:
    pip install faster-whisper

Requirements:
    - CUDA for GPU acceleration (optional but recommended)
    - Model downloaded locally (auto-downloads on first use)
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Optional
import numpy as np

from ...core.events import AudioFrameIn
from ...core.interfaces import BaseASRBackend, SpeechEvent

logger = logging.getLogger(__name__)

# Import faster-whisper
try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False
    WhisperModel = None
    logger.warning("faster-whisper not installed. Run: pip install faster-whisper")


class FasterWhisperASR(BaseASRBackend):
    """
    Faster-Whisper ASR backend for local speech recognition.

    Uses CTranslate2-optimized Whisper models for fast inference.
    Supports streaming via chunked processing with VAD.

    Features:
    - GPU acceleration via CUDA
    - Multiple model sizes (tiny, base, small, medium, large-v2, large-v3)
    - Language detection
    - VAD-based chunking for better accuracy
    - Cancellation support for barge-in

    Note: Whisper is not truly streaming - we simulate streaming with
    chunked processing and partial results.
    """

    def __init__(
        self,
        model_size: str = "base.en",
        device: str = "auto",  # "auto", "cuda", "cpu"
        compute_type: str = "auto",  # "auto", "float16", "int8", "float32"
        sample_rate: int = 16000,  # Whisper expects 16kHz
        num_channels: int = 1,
        language: Optional[str] = "en",
        beam_size: int = 5,
        vad_filter: bool = True,
        chunk_duration_ms: float = 2000.0,  # Process in 2s chunks
        min_chunk_duration_ms: float = 500.0,  # Minimum chunk to process
    ):
        # Note: Whisper models expect 16kHz audio
        # The pipeline should resample from 24kHz canonical format
        super().__init__(sample_rate, num_channels)

        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._beam_size = beam_size
        self._vad_filter = vad_filter
        self._chunk_duration_ms = chunk_duration_ms
        self._min_chunk_duration_ms = min_chunk_duration_ms

        # Model will be loaded lazily
        self._model: Optional[WhisperModel] = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._closed = False

        if not FASTER_WHISPER_AVAILABLE:
            raise ImportError(
                "faster-whisper is required for FasterWhisperASR. "
                "Run: pip install faster-whisper"
            )

        logger.info(
            f"FasterWhisperASR initialized: model={model_size}, "
            f"device={device}, compute_type={compute_type}"
        )

    def _ensure_model_loaded(self) -> WhisperModel:
        """Lazy-load the Whisper model."""
        if self._model is not None:
            return self._model

        logger.info(f"Loading Whisper model: {self._model_size}")
        start = time.time()

        # Determine device
        device = self._device
        if device == "auto":
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"

        # Determine compute type
        compute_type = self._compute_type
        if compute_type == "auto":
            compute_type = "float16" if device == "cuda" else "float32"

        self._model = WhisperModel(
            self._model_size,
            device=device,
            compute_type=compute_type,
        )

        elapsed = time.time() - start
        logger.info(f"Whisper model loaded in {elapsed:.1f}s: {self._model_size} on {device}")

        return self._model

    async def stream(
        self,
        audio_stream: AsyncIterator[AudioFrameIn],
    ) -> AsyncIterator[SpeechEvent]:
        """
        Process streaming audio and yield transcription events.

        Implementation strategy:
        1. Buffer audio chunks (2 seconds by default)
        2. Run Whisper transcription on each chunk
        3. Emit partial results during speech
        4. Emit final result when silence detected or chunk complete
        """
        self._ensure_model_loaded()
        logger.debug("FasterWhisperASR: Starting stream processing")

        # Audio buffer (list of PCM16 samples)
        audio_buffer: list[int] = []
        buffer_duration_ms = 0.0
        last_transcript = ""
        speech_id = 0

        # Resampler for 24kHz -> 16kHz if needed
        from ...core.audio import CANONICAL_FORMAT

        async for frame in audio_stream:
            if self._closed:
                break

            # Convert PCM16 bytes to samples
            num_samples = len(frame.data) // 2
            samples = struct.unpack(f'<{num_samples}h', frame.data)

            # Resample if needed (24kHz -> 16kHz)
            if frame.sample_rate != self._sample_rate:
                samples = self._resample(
                    samples,
                    frame.sample_rate,
                    self._sample_rate
                )

            audio_buffer.extend(samples)
            buffer_duration_ms += frame.duration_ms

            # Process when buffer reaches chunk duration
            if buffer_duration_ms >= self._chunk_duration_ms:
                # Run transcription
                transcript, confidence = await self._transcribe_buffer(audio_buffer)

                if transcript and transcript.strip():
                    is_different = transcript.strip() != last_transcript.strip()

                    if is_different:
                        yield SpeechEvent(
                            text=transcript,
                            is_final=False,
                            confidence=confidence,
                            language=self._language,
                            speech_id=f"speech_{speech_id}",
                        )
                        last_transcript = transcript

                # Keep some overlap for continuity (25%)
                overlap_samples = len(audio_buffer) // 4
                audio_buffer = audio_buffer[-overlap_samples:] if overlap_samples > 0 else []
                buffer_duration_ms = len(audio_buffer) / self._sample_rate * 1000

        # Process remaining audio
        if audio_buffer and buffer_duration_ms >= self._min_chunk_duration_ms:
            transcript, confidence = await self._transcribe_buffer(audio_buffer)

            if transcript and transcript.strip():
                yield SpeechEvent(
                    text=transcript,
                    is_final=True,
                    confidence=confidence,
                    language=self._language,
                    speech_id=f"speech_{speech_id}",
                )

        logger.debug("FasterWhisperASR: Stream processing complete")

    def _resample(
        self,
        samples: tuple[int, ...],
        from_rate: int,
        to_rate: int
    ) -> list[int]:
        """Simple linear resampling."""
        if from_rate == to_rate:
            return list(samples)

        ratio = to_rate / from_rate
        new_length = int(len(samples) * ratio)
        resampled = []

        for i in range(new_length):
            src_idx = i / ratio
            idx_low = int(src_idx)
            idx_high = min(idx_low + 1, len(samples) - 1)
            frac = src_idx - idx_low

            # Linear interpolation
            value = samples[idx_low] * (1 - frac) + samples[idx_high] * frac
            resampled.append(int(value))

        return resampled

    async def _transcribe_buffer(
        self,
        samples: list[int]
    ) -> tuple[str, float]:
        """
        Transcribe an audio buffer using Whisper.

        Runs in thread pool to avoid blocking async loop.

        Returns:
            (transcript, confidence)
        """
        if not samples:
            return "", 0.0

        loop = asyncio.get_event_loop()

        def _sync_transcribe() -> tuple[str, float]:
            # Convert to float32 numpy array normalized to [-1, 1]
            audio_np = np.array(samples, dtype=np.float32) / 32768.0

            # Run transcription
            segments, info = self._model.transcribe(
                audio_np,
                language=self._language,
                beam_size=self._beam_size,
                vad_filter=self._vad_filter,
                vad_parameters={
                    "min_silence_duration_ms": 500,
                    "speech_pad_ms": 200,
                },
            )

            # Collect results
            texts = []
            total_confidence = 0.0
            segment_count = 0

            for segment in segments:
                texts.append(segment.text)
                # avg_logprob is log probability, convert to ~confidence
                total_confidence += np.exp(segment.avg_logprob) if segment.avg_logprob else 0.5
                segment_count += 1

            transcript = " ".join(texts).strip()
            avg_confidence = total_confidence / segment_count if segment_count > 0 else 0.0

            return transcript, avg_confidence

        return await loop.run_in_executor(self._executor, _sync_transcribe)

    def cancel(self) -> None:
        """Cancel current transcription (for barge-in)."""
        self._closed = True

    def reset(self) -> None:
        """Reset for new transcription."""
        self._closed = False

    async def close(self) -> None:
        """Release resources."""
        self._closed = True
        self._executor.shutdown(wait=False)
        self._model = None
        logger.debug("FasterWhisperASR: Closed")


class WhisperCppASR(BaseASRBackend):
    """
    Whisper.cpp ASR backend using whisper-cpp-python bindings.

    Alternative to faster-whisper using whisper.cpp for even faster inference.

    Installation:
        pip install whisper-cpp-python
        # Download model:
        wget https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
    """

    def __init__(
        self,
        model_path: str = "models/ggml-base.en.bin",
        sample_rate: int = 16000,
        num_channels: int = 1,
        n_threads: int = 4,
    ):
        super().__init__(sample_rate, num_channels)
        self._model_path = model_path
        self._n_threads = n_threads
        self._model = None
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1)

        logger.info(f"WhisperCppASR initialized: {model_path}")

    def _ensure_model_loaded(self):
        """Load the whisper.cpp model."""
        if self._model is not None:
            return self._model

        try:
            from whisper_cpp_python import Whisper
            self._model = Whisper(self._model_path, n_threads=self._n_threads)
            logger.info(f"Whisper.cpp model loaded: {self._model_path}")
        except ImportError:
            raise ImportError(
                "whisper-cpp-python is required for WhisperCppASR. "
                "Run: pip install whisper-cpp-python"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to load Whisper.cpp model: {e}")

        return self._model

    async def stream(
        self,
        audio_stream: AsyncIterator[AudioFrameIn],
    ) -> AsyncIterator[SpeechEvent]:
        """Process audio stream with whisper.cpp."""
        self._ensure_model_loaded()

        audio_buffer: list[int] = []
        chunk_duration_ms = 2000.0

        async for frame in audio_stream:
            if self._closed:
                break

            num_samples = len(frame.data) // 2
            samples = struct.unpack(f'<{num_samples}h', frame.data)
            audio_buffer.extend(samples)

            buffer_duration = len(audio_buffer) / self._sample_rate * 1000

            if buffer_duration >= chunk_duration_ms:
                transcript = await self._transcribe_buffer(audio_buffer)

                if transcript:
                    yield SpeechEvent(
                        text=transcript,
                        is_final=False,
                        confidence=0.9,
                        language="en",
                    )

                # Keep overlap
                overlap = len(audio_buffer) // 4
                audio_buffer = audio_buffer[-overlap:] if overlap > 0 else []

        # Final chunk
        if audio_buffer:
            transcript = await self._transcribe_buffer(audio_buffer)
            if transcript:
                yield SpeechEvent(
                    text=transcript,
                    is_final=True,
                    confidence=0.95,
                    language="en",
                )

    async def _transcribe_buffer(self, samples: list[int]) -> str:
        """Transcribe using whisper.cpp."""
        loop = asyncio.get_event_loop()

        def _sync():
            audio_np = np.array(samples, dtype=np.float32) / 32768.0
            result = self._model.transcribe(audio_np)
            return result.get("text", "").strip()

        return await loop.run_in_executor(self._executor, _sync)

    def cancel(self) -> None:
        """Cancel current transcription."""
        self._closed = True

    def reset(self) -> None:
        """Reset for new transcription."""
        self._closed = False

    async def close(self) -> None:
        """Release resources."""
        self._closed = True
        self._executor.shutdown(wait=False)
        self._model = None
