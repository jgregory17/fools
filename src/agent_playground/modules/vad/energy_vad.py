"""
Energy-based Voice Activity Detection.

Simple VAD implementation based on audio energy/volume.
Fast and requires no external dependencies.

Good for:
- Development and testing
- Low-latency scenarios where ML VAD is too slow
- Environments with clean audio (no background noise)

Limitations:
- Sensitive to background noise
- No semantic understanding of speech vs. other sounds
"""

from __future__ import annotations

import struct
import math
from dataclasses import dataclass
from typing import Optional

from .interface import BaseVAD, VADResult


@dataclass
class EnergyVADConfig:
    """Configuration for energy-based VAD."""

    # Sample rate (must match input audio)
    sample_rate: int = 24000

    # Frame duration in milliseconds
    frame_duration_ms: int = 20

    # RMS energy threshold for speech detection (0.0-1.0)
    # Adjust based on expected microphone levels
    energy_threshold: float = 0.02

    # Hysteresis thresholds
    speech_threshold: float = 0.6
    silence_threshold: float = 0.3

    # Minimum frames for state transitions
    min_speech_frames: int = 3
    min_silence_frames: int = 5

    # Adaptive threshold parameters
    adaptive: bool = True
    adaptation_rate: float = 0.01  # How fast to adapt
    min_threshold: float = 0.005
    max_threshold: float = 0.1


class EnergyVAD(BaseVAD):
    """
    Energy-based Voice Activity Detection.

    Detects speech by measuring audio energy (RMS) and comparing
    to a threshold. Simple but effective for clean audio.

    Usage:
        vad = EnergyVAD()

        for frame in audio_stream:
            result = vad.process(frame)
            if result.speech_start:
                print("User started speaking")
            if result.speech_end:
                print("User stopped speaking")
    """

    def __init__(self, config: Optional[EnergyVADConfig] = None):
        self.config = config or EnergyVADConfig()

        super().__init__(
            sample_rate=self.config.sample_rate,
            frame_duration_ms=self.config.frame_duration_ms,
            speech_threshold=self.config.speech_threshold,
            silence_threshold=self.config.silence_threshold,
            min_speech_frames=self.config.min_speech_frames,
            min_silence_frames=self.config.min_silence_frames,
        )

        # Adaptive threshold state
        self._current_threshold = self.config.energy_threshold
        self._noise_floor = 0.0
        self._peak_level = 0.0

    def process(self, audio_data: bytes) -> VADResult:
        """
        Process audio frame and detect voice activity.

        Args:
            audio_data: PCM16 mono audio at expected sample rate

        Returns:
            VADResult with speech probability
        """
        if not audio_data:
            return VADResult(probability=0.0, is_speech=False)

        # Calculate RMS energy
        energy = self._compute_rms(audio_data)

        # Update adaptive threshold
        if self.config.adaptive:
            self._update_adaptive_threshold(energy)

        # Convert energy to probability (0-1 scale)
        # Using a sigmoid-like mapping
        if self._current_threshold > 0:
            normalized = energy / self._current_threshold
            probability = min(1.0, normalized)
        else:
            probability = 1.0 if energy > 0 else 0.0

        # Update state with hysteresis
        return self._update_state(probability)

    def _compute_rms(self, audio_data: bytes) -> float:
        """Compute RMS (Root Mean Square) energy of audio."""
        if len(audio_data) < 2:
            return 0.0

        # Unpack PCM16 samples
        num_samples = len(audio_data) // 2
        try:
            samples = struct.unpack(f'<{num_samples}h', audio_data)
        except struct.error:
            return 0.0

        if not samples:
            return 0.0

        # Calculate RMS, normalized to 0-1 range
        sum_squares = sum(s * s for s in samples)
        rms = math.sqrt(sum_squares / len(samples))
        normalized = rms / 32768.0  # Max value for 16-bit audio

        return normalized

    def _update_adaptive_threshold(self, energy: float) -> None:
        """
        Update adaptive threshold based on observed energy.

        Tracks noise floor and adjusts threshold accordingly.
        """
        rate = self.config.adaptation_rate

        # Update noise floor (slow adaptation to minimum observed energy)
        if not self._is_speech:
            if self._noise_floor == 0:
                self._noise_floor = energy
            else:
                self._noise_floor = (1 - rate) * self._noise_floor + rate * min(energy, self._noise_floor * 2)

        # Update peak level
        if energy > self._peak_level:
            self._peak_level = energy
        else:
            self._peak_level = (1 - rate * 0.1) * self._peak_level

        # Calculate adaptive threshold
        # Set threshold between noise floor and peak, closer to noise floor
        if self._peak_level > self._noise_floor * 2:
            self._current_threshold = self._noise_floor * 3
        else:
            self._current_threshold = self.config.energy_threshold

        # Clamp to configured range
        self._current_threshold = max(
            self.config.min_threshold,
            min(self.config.max_threshold, self._current_threshold)
        )

    def reset(self) -> None:
        """Reset VAD state."""
        super().reset()
        self._current_threshold = self.config.energy_threshold
        self._noise_floor = 0.0
        self._peak_level = 0.0

    @property
    def metrics(self) -> dict:
        """Get VAD metrics including adaptive threshold info."""
        base = super().metrics
        base.update({
            "current_threshold": self._current_threshold,
            "noise_floor": self._noise_floor,
            "peak_level": self._peak_level,
        })
        return base
