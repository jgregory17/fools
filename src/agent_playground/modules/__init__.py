"""
Pluggable modules for ASR, LLM, TTS, and tools.

Each module directory contains:
- Real adapters for local model runners
- dev_stubs/ contains fake backends for testing only
"""

from .asr import FasterWhisperASR, WhisperCppASR
from .llm import OllamaLLM, VLLMAdapter
from .tts import PiperTTS, CoquiTTS

__all__ = [
    "FasterWhisperASR",
    "WhisperCppASR",
    "OllamaLLM",
    "VLLMAdapter",
    "PiperTTS",
    "CoquiTTS",
]
