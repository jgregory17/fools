"""
Canonical Audio Format and Normalization.

This module defines the single source of truth for audio format throughout
the pipeline, ensuring consistent sample rate, bit depth, and channel count.

Design Rationale:
- 24kHz chosen as canonical rate: high enough for good TTS quality, low enough
  for efficient ASR processing. Most ASR models downsample to 16kHz internally,
  and 24kHz is divisible by common frame sizes.
- PCM16 (signed 16-bit little-endian): Universal format, matches LiveKit's
  native format, no precision loss for speech.
- Mono: Voice agents don't need stereo, reduces processing and bandwidth.
- 20ms frames: Good balance between latency and efficiency. Matches WebRTC's
  default frame size.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class AudioFormat(Enum):
    """Supported audio formats."""
    PCM_S16LE = "pcm_s16le"  # Signed 16-bit little-endian (canonical)
    PCM_F32LE = "pcm_f32le"  # 32-bit float little-endian
    PCM_S24LE = "pcm_s24le"  # Signed 24-bit little-endian


@dataclass(frozen=True)
class CanonicalAudioFormat:
    """
    The single canonical audio format for the entire pipeline.

    All audio entering or leaving the system is normalized to this format.
    This prevents sample rate mismatch bugs and simplifies module interfaces.
    """

    # 24kHz: Good for TTS quality, divisible by common frame sizes,
    # ASR models typically downsample internally anyway
    sample_rate: int = 24000

    # Mono for voice - no need for stereo
    num_channels: int = 1

    # PCM16 signed little-endian - universal, no precision loss for speech
    format: AudioFormat = AudioFormat.PCM_S16LE

    # 20ms frames = 480 samples at 24kHz
    # Good latency/efficiency tradeoff, matches WebRTC default
    frame_duration_ms: int = 20

    @property
    def samples_per_frame(self) -> int:
        """Number of samples per frame at canonical rate."""
        return int(self.sample_rate * self.frame_duration_ms / 1000)

    @property
    def bytes_per_sample(self) -> int:
        """Bytes per sample based on format."""
        if self.format == AudioFormat.PCM_S16LE:
            return 2
        elif self.format == AudioFormat.PCM_F32LE:
            return 4
        elif self.format == AudioFormat.PCM_S24LE:
            return 3
        return 2

    @property
    def bytes_per_frame(self) -> int:
        """Total bytes per frame."""
        return self.samples_per_frame * self.bytes_per_sample * self.num_channels

    def frame_duration_samples(self, duration_ms: int) -> int:
        """Convert duration in ms to samples."""
        return int(self.sample_rate * duration_ms / 1000)


# Global canonical format instance - the single source of truth
CANONICAL_FORMAT = CanonicalAudioFormat()


@dataclass
class AudioNormalizerConfig:
    """Configuration for AudioNormalizer."""

    # Target format (default: canonical)
    target_sample_rate: int = CANONICAL_FORMAT.sample_rate
    target_channels: int = CANONICAL_FORMAT.num_channels
    target_format: AudioFormat = CANONICAL_FORMAT.format

    # Frame chunking
    target_frame_duration_ms: int = CANONICAL_FORMAT.frame_duration_ms

    # Resampling quality (for SoX resampler if available)
    # Options: "quick", "low", "medium", "high", "veryhigh"
    resample_quality: str = "medium"

    # Whether to log conversion details
    log_conversions: bool = False


class AudioNormalizer:
    """
    Normalizes audio to the canonical pipeline format.

    Handles:
    - Sample rate conversion (resampling)
    - Channel downmixing (stereo -> mono)
    - Format conversion (float -> PCM16)
    - Frame size enforcement (chunking to target duration)

    Usage:
        normalizer = AudioNormalizer()

        # Normalize incoming audio
        for frame in normalizer.normalize(raw_audio, input_rate=48000, input_channels=2):
            process(frame)

        # Get any remaining buffered audio
        for frame in normalizer.flush():
            process(frame)
    """

    def __init__(self, config: Optional[AudioNormalizerConfig] = None):
        self.config = config or AudioNormalizerConfig()
        self._buffer: bytes = b""
        self._resampler: Optional[object] = None  # Lazy-init resampler

        # Metrics
        self._frames_processed = 0
        self._bytes_converted = 0
        self._resample_operations = 0

    @property
    def target_samples_per_frame(self) -> int:
        """Target samples per output frame."""
        return int(
            self.config.target_sample_rate *
            self.config.target_frame_duration_ms / 1000
        )

    @property
    def target_bytes_per_frame(self) -> int:
        """Target bytes per output frame."""
        bytes_per_sample = 2 if self.config.target_format == AudioFormat.PCM_S16LE else 4
        return self.target_samples_per_frame * bytes_per_sample * self.config.target_channels

    def normalize(
        self,
        audio_data: bytes,
        input_sample_rate: int,
        input_channels: int = 1,
        input_format: AudioFormat = AudioFormat.PCM_S16LE,
    ) -> list[bytes]:
        """
        Normalize audio data to canonical format.

        Args:
            audio_data: Raw audio bytes
            input_sample_rate: Source sample rate
            input_channels: Source channel count
            input_format: Source audio format

        Returns:
            List of normalized audio frames (may be empty if buffering)
        """
        # Step 1: Format conversion to PCM16 if needed
        if input_format != AudioFormat.PCM_S16LE:
            audio_data = self._convert_format(audio_data, input_format)

        # Step 2: Channel downmix if needed
        if input_channels > self.config.target_channels:
            audio_data = self._downmix_channels(audio_data, input_channels)

        # Step 3: Resample if needed
        if input_sample_rate != self.config.target_sample_rate:
            audio_data = self._resample(
                audio_data,
                input_sample_rate,
                self.config.target_sample_rate
            )
            self._resample_operations += 1

        # Step 4: Chunk into frames
        self._buffer += audio_data
        self._bytes_converted += len(audio_data)

        return self._extract_frames()

    def flush(self) -> list[bytes]:
        """
        Flush any remaining buffered audio.

        Call this at end of stream to get final (possibly partial) frame.
        """
        frames = []
        if self._buffer:
            # Pad to frame size if needed
            if len(self._buffer) < self.target_bytes_per_frame:
                padding = self.target_bytes_per_frame - len(self._buffer)
                self._buffer += b'\x00' * padding
            frames.append(self._buffer[:self.target_bytes_per_frame])
            self._buffer = b""
        return frames

    def reset(self) -> None:
        """Reset internal state."""
        self._buffer = b""
        self._frames_processed = 0
        self._bytes_converted = 0
        self._resample_operations = 0

    def _extract_frames(self) -> list[bytes]:
        """Extract complete frames from buffer."""
        frames = []
        while len(self._buffer) >= self.target_bytes_per_frame:
            frame = self._buffer[:self.target_bytes_per_frame]
            self._buffer = self._buffer[self.target_bytes_per_frame:]
            frames.append(frame)
            self._frames_processed += 1
        return frames

    def _convert_format(self, data: bytes, source_format: AudioFormat) -> bytes:
        """Convert from source format to PCM16."""
        if source_format == AudioFormat.PCM_F32LE:
            # Float32 to PCM16
            float_samples = struct.unpack(f'<{len(data)//4}f', data)
            int_samples = [
                max(-32768, min(32767, int(s * 32767)))
                for s in float_samples
            ]
            return struct.pack(f'<{len(int_samples)}h', *int_samples)

        elif source_format == AudioFormat.PCM_S24LE:
            # 24-bit to 16-bit (drop lower 8 bits)
            samples = []
            for i in range(0, len(data), 3):
                # Read 3 bytes as signed 24-bit, shift to 16-bit
                b = data[i:i+3]
                if len(b) == 3:
                    val = int.from_bytes(b, 'little', signed=True)
                    samples.append(val >> 8)
            return struct.pack(f'<{len(samples)}h', *samples)

        return data  # Already PCM16

    def _downmix_channels(self, data: bytes, num_channels: int) -> bytes:
        """Downmix multi-channel audio to mono."""
        if num_channels == 1:
            return data

        # Unpack all samples
        samples = struct.unpack(f'<{len(data)//2}h', data)

        # Average channels to mono
        mono_samples = []
        for i in range(0, len(samples), num_channels):
            channel_samples = samples[i:i+num_channels]
            avg = sum(channel_samples) // num_channels
            mono_samples.append(max(-32768, min(32767, avg)))

        return struct.pack(f'<{len(mono_samples)}h', *mono_samples)

    def _resample(self, data: bytes, from_rate: int, to_rate: int) -> bytes:
        """
        Resample audio using linear interpolation.

        Note: For production, consider using a proper resampler like
        soxr or scipy.signal.resample_poly for better quality.
        """
        if from_rate == to_rate:
            return data

        # Try to use LiveKit's SoxResampler if available
        try:
            return self._resample_sox(data, from_rate, to_rate)
        except (ImportError, Exception):
            pass

        # Fallback to linear interpolation
        return self._resample_linear(data, from_rate, to_rate)

    def _resample_sox(self, data: bytes, from_rate: int, to_rate: int) -> bytes:
        """Resample using LiveKit's SoxResampler (if available)."""
        try:
            from livekit import rtc

            if self._resampler is None:
                self._resampler = rtc.AudioResampler(
                    input_rate=from_rate,
                    output_rate=to_rate,
                    num_channels=1,
                )

            # Create AudioFrame from data
            samples_per_channel = len(data) // 2  # PCM16 = 2 bytes per sample
            frame = rtc.AudioFrame(
                data=data,
                sample_rate=from_rate,
                num_channels=1,
                samples_per_channel=samples_per_channel,
            )

            resampled = self._resampler.remix_and_resample(frame, to_rate, 1)
            return bytes(resampled.data)

        except ImportError:
            raise ImportError("livekit-rtc not available")

    def _resample_linear(self, data: bytes, from_rate: int, to_rate: int) -> bytes:
        """Simple linear interpolation resampling."""
        samples = struct.unpack(f'<{len(data)//2}h', data)

        ratio = to_rate / from_rate
        new_length = int(len(samples) * ratio)

        resampled = []
        for i in range(new_length):
            src_idx = i / ratio
            idx_floor = int(src_idx)
            idx_ceil = min(idx_floor + 1, len(samples) - 1)
            frac = src_idx - idx_floor

            sample = int(samples[idx_floor] * (1 - frac) + samples[idx_ceil] * frac)
            resampled.append(max(-32768, min(32767, sample)))

        return struct.pack(f'<{len(resampled)}h', *resampled)

    @property
    def metrics(self) -> dict:
        """Return processing metrics."""
        return {
            "frames_processed": self._frames_processed,
            "bytes_converted": self._bytes_converted,
            "resample_operations": self._resample_operations,
            "buffer_size": len(self._buffer),
        }


class AudioFrameBuffer:
    """
    Thread-safe buffer for audio frames with backpressure support.

    Used to manage audio flow between async producers and consumers
    while preventing memory growth.
    """

    def __init__(
        self,
        max_frames: int = 100,
        frame_duration_ms: int = CANONICAL_FORMAT.frame_duration_ms,
    ):
        self.max_frames = max_frames
        self.frame_duration_ms = frame_duration_ms
        self._frames: list[bytes] = []
        self._dropped_count = 0
        self._total_added = 0

    @property
    def max_buffer_duration_ms(self) -> int:
        """Maximum buffered duration in milliseconds."""
        return self.max_frames * self.frame_duration_ms

    @property
    def current_duration_ms(self) -> int:
        """Current buffered duration in milliseconds."""
        return len(self._frames) * self.frame_duration_ms

    def add(self, frame: bytes, drop_policy: str = "drop_oldest") -> bool:
        """
        Add a frame to the buffer.

        Args:
            frame: Audio frame data
            drop_policy: "drop_oldest", "drop_newest", or "block"

        Returns:
            True if frame was added, False if dropped
        """
        self._total_added += 1

        if len(self._frames) >= self.max_frames:
            if drop_policy == "drop_oldest":
                self._frames.pop(0)
                self._dropped_count += 1
                if self._dropped_count % 100 == 1:
                    logger.warning(
                        f"Audio buffer overflow: dropped {self._dropped_count} frames "
                        f"(policy: {drop_policy})"
                    )
            elif drop_policy == "drop_newest":
                self._dropped_count += 1
                return False
            # "block" would be handled by caller with async queue

        self._frames.append(frame)
        return True

    def get(self) -> Optional[bytes]:
        """Get and remove the oldest frame."""
        if self._frames:
            return self._frames.pop(0)
        return None

    def peek(self) -> Optional[bytes]:
        """Peek at the oldest frame without removing."""
        if self._frames:
            return self._frames[0]
        return None

    def clear(self) -> int:
        """Clear all frames, return count of cleared frames."""
        count = len(self._frames)
        self._frames.clear()
        return count

    @property
    def metrics(self) -> dict:
        """Return buffer metrics."""
        return {
            "current_frames": len(self._frames),
            "max_frames": self.max_frames,
            "dropped_count": self._dropped_count,
            "total_added": self._total_added,
            "drop_rate": (
                self._dropped_count / self._total_added
                if self._total_added > 0 else 0.0
            ),
        }


def create_silence(duration_ms: int, format: CanonicalAudioFormat = CANONICAL_FORMAT) -> bytes:
    """Create silent audio of specified duration."""
    samples = format.frame_duration_samples(duration_ms)
    return b'\x00' * (samples * format.bytes_per_sample * format.num_channels)


def compute_audio_energy(data: bytes, format: AudioFormat = AudioFormat.PCM_S16LE) -> float:
    """
    Compute RMS energy of audio data.

    Returns a value between 0.0 and 1.0.
    """
    if not data:
        return 0.0

    if format == AudioFormat.PCM_S16LE:
        samples = struct.unpack(f'<{len(data)//2}h', data)
        if not samples:
            return 0.0

        # RMS normalized to 0-1 range
        sum_sq = sum(s * s for s in samples)
        rms = (sum_sq / len(samples)) ** 0.5
        return min(1.0, rms / 32768.0)

    return 0.0
