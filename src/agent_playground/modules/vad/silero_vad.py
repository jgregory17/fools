"""
Silero Voice Activity Detection.

Production-quality VAD using the Silero VAD model.
Provides state-of-the-art accuracy for speech detection.

Requires: torch, onnxruntime (optional for faster inference)

The Silero VAD model is lightweight (~2MB) and runs efficiently
even on CPU, making it suitable for real-time applications.
"""

from __future__ import annotations

import struct
import logging
from dataclasses import dataclass
from typing import Optional, Any

from .interface import BaseVAD, VADResult

logger = logging.getLogger(__name__)


@dataclass
class SileroVADConfig:
    """Configuration for Silero VAD."""

    # Sample rate - Silero expects 16kHz
    sample_rate: int = 16000

    # Frame duration - Silero works best with 30ms frames
    frame_duration_ms: int = 30

    # Speech probability threshold
    speech_threshold: float = 0.5
    silence_threshold: float = 0.35

    # Minimum frames for state transitions
    min_speech_frames: int = 2
    min_silence_frames: int = 8

    # Use ONNX runtime for faster inference (if available)
    use_onnx: bool = True

    # Model repo (for downloading)
    model_repo: str = "snakers4/silero-vad"
    model_name: str = "silero_vad"


class SileroVAD(BaseVAD):
    """
    Silero VAD implementation.

    Uses the Silero VAD model for accurate speech detection.
    Falls back to energy-based VAD if Silero is not available.

    Usage:
        vad = SileroVAD()

        for frame in audio_stream:
            result = vad.process(frame)
            if result.is_speech:
                # User is speaking
                pass
    """

    def __init__(self, config: Optional[SileroVADConfig] = None):
        self.config = config or SileroVADConfig()

        super().__init__(
            sample_rate=self.config.sample_rate,
            frame_duration_ms=self.config.frame_duration_ms,
            speech_threshold=self.config.speech_threshold,
            silence_threshold=self.config.silence_threshold,
            min_speech_frames=self.config.min_speech_frames,
            min_silence_frames=self.config.min_silence_frames,
        )

        self._model: Optional[Any] = None
        self._model_loaded = False
        self._fallback_to_energy = False

        # Try to load model
        self._load_model()

    def _load_model(self) -> None:
        """Load the Silero VAD model."""
        try:
            import torch

            # Load model from torch hub
            model, utils = torch.hub.load(
                repo_or_dir=self.config.model_repo,
                model=self.config.model_name,
                force_reload=False,
                onnx=self.config.use_onnx,
                trust_repo=True,
            )

            self._model = model
            self._get_speech_timestamps = utils[0]
            self._model_loaded = True
            logger.info("Silero VAD model loaded successfully")

        except ImportError as e:
            logger.warning(f"PyTorch not available, falling back to energy VAD: {e}")
            self._fallback_to_energy = True
        except Exception as e:
            logger.warning(f"Failed to load Silero VAD, falling back to energy VAD: {e}")
            self._fallback_to_energy = True

    def process(self, audio_data: bytes) -> VADResult:
        """
        Process audio frame with Silero VAD.

        Args:
            audio_data: PCM16 mono audio at 16kHz

        Returns:
            VADResult with speech probability
        """
        if not audio_data:
            return VADResult(probability=0.0, is_speech=False)

        if self._fallback_to_energy:
            return self._process_energy(audio_data)

        if not self._model_loaded:
            return self._process_energy(audio_data)

        try:
            probability = self._process_silero(audio_data)
        except Exception as e:
            logger.warning(f"Silero inference failed, using energy fallback: {e}")
            return self._process_energy(audio_data)

        return self._update_state(probability)

    def _process_silero(self, audio_data: bytes) -> float:
        """Process with Silero model."""
        import torch

        # Convert bytes to float tensor
        num_samples = len(audio_data) // 2
        samples = struct.unpack(f'<{num_samples}h', audio_data)

        # Normalize to -1.0 to 1.0 range
        audio_tensor = torch.FloatTensor(samples) / 32768.0

        # Run inference
        with torch.no_grad():
            probability = self._model(audio_tensor, self.config.sample_rate).item()

        return probability

    def _process_energy(self, audio_data: bytes) -> VADResult:
        """Fallback to simple energy-based VAD."""
        if len(audio_data) < 2:
            return VADResult(probability=0.0, is_speech=False)

        # Calculate RMS energy
        num_samples = len(audio_data) // 2
        samples = struct.unpack(f'<{num_samples}h', audio_data)

        if not samples:
            return VADResult(probability=0.0, is_speech=False)

        sum_squares = sum(s * s for s in samples)
        rms = (sum_squares / len(samples)) ** 0.5
        normalized = rms / 32768.0

        # Simple threshold mapping
        probability = min(1.0, normalized / 0.02)

        return self._update_state(probability)

    def reset(self) -> None:
        """Reset VAD state including model state."""
        super().reset()

        if self._model_loaded and hasattr(self._model, 'reset_states'):
            try:
                self._model.reset_states()
            except Exception:
                pass

    @property
    def metrics(self) -> dict:
        """Get VAD metrics."""
        base = super().metrics
        base.update({
            "model_loaded": self._model_loaded,
            "fallback_to_energy": self._fallback_to_energy,
        })
        return base
