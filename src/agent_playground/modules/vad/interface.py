"""
VAD (Voice Activity Detection) Interface.

Defines the protocol for VAD backends, enabling pluggable detection
of speech vs. silence in audio streams.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable, Optional
import time


@dataclass(frozen=True)
class VADResult:
    """
    Result from VAD processing.

    Provides speech probability and derived state.
    """

    # Speech probability (0.0-1.0)
    probability: float

    # Whether this is considered speech (after threshold)
    is_speech: bool

    # Timestamp of this result
    timestamp: float = field(default_factory=time.time)

    # Duration of the analyzed audio (ms)
    duration_ms: float = 0.0

    # Whether speech just started (transition from silence)
    speech_start: bool = False

    # Whether speech just ended (transition to silence)
    speech_end: bool = False


@runtime_checkable
class VADBackend(Protocol):
    """
    Protocol for Voice Activity Detection backends.

    VAD is used for:
    - Detecting user interruptions (barge-in)
    - End-of-utterance detection
    - Filtering silence from ASR input
    """

    @property
    def sample_rate(self) -> int:
        """Expected input sample rate."""
        ...

    @property
    def frame_duration_ms(self) -> int:
        """Expected frame duration in milliseconds."""
        ...

    def process(self, audio_data: bytes) -> VADResult:
        """
        Process an audio frame and return VAD result.

        Args:
            audio_data: PCM16 audio data at expected sample rate

        Returns:
            VADResult with speech probability and state
        """
        ...

    def reset(self) -> None:
        """Reset internal state (e.g., between conversations)."""
        ...


class BaseVAD:
    """
    Base class for VAD implementations.

    Provides common functionality:
    - Speech/silence state tracking
    - Hysteresis to prevent rapid toggling
    - Metrics collection
    """

    def __init__(
        self,
        sample_rate: int = 24000,
        frame_duration_ms: int = 20,
        speech_threshold: float = 0.5,
        silence_threshold: float = 0.3,
        min_speech_frames: int = 3,
        min_silence_frames: int = 5,
    ):
        self._sample_rate = sample_rate
        self._frame_duration_ms = frame_duration_ms
        self._speech_threshold = speech_threshold
        self._silence_threshold = silence_threshold
        self._min_speech_frames = min_speech_frames
        self._min_silence_frames = min_silence_frames

        # State
        self._is_speech = False
        self._consecutive_speech = 0
        self._consecutive_silence = 0
        self._frames_processed = 0

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def frame_duration_ms(self) -> int:
        return self._frame_duration_ms

    def _update_state(self, probability: float) -> VADResult:
        """
        Update speech/silence state with hysteresis.

        Returns VADResult with state transitions.
        """
        self._frames_processed += 1
        speech_start = False
        speech_end = False

        if probability >= self._speech_threshold:
            self._consecutive_speech += 1
            self._consecutive_silence = 0

            if not self._is_speech and self._consecutive_speech >= self._min_speech_frames:
                self._is_speech = True
                speech_start = True

        elif probability <= self._silence_threshold:
            self._consecutive_silence += 1
            self._consecutive_speech = 0

            if self._is_speech and self._consecutive_silence >= self._min_silence_frames:
                self._is_speech = False
                speech_end = True

        return VADResult(
            probability=probability,
            is_speech=self._is_speech,
            duration_ms=self._frame_duration_ms,
            speech_start=speech_start,
            speech_end=speech_end,
        )

    def reset(self) -> None:
        """Reset internal state."""
        self._is_speech = False
        self._consecutive_speech = 0
        self._consecutive_silence = 0

    @property
    def metrics(self) -> dict:
        """Get VAD metrics."""
        return {
            "frames_processed": self._frames_processed,
            "is_speech": self._is_speech,
            "consecutive_speech": self._consecutive_speech,
            "consecutive_silence": self._consecutive_silence,
        }
