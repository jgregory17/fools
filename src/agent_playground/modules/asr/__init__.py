"""ASR (Automatic Speech Recognition) backends."""

from .faster_whisper_asr import FasterWhisperASR, WhisperCppASR

__all__ = ["FasterWhisperASR", "WhisperCppASR"]
