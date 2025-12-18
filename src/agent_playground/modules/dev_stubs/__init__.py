"""
Development Stubs for Testing.

These fake backends are for testing and development only.
They should NOT be used in production or default configurations.

Usage:
    # Only for testing
    from agent_playground.modules.dev_stubs import FakeASR, FakeLLM, FakeTTS
"""

from .fake_asr import FakeASR
from .fake_llm import FakeLLM
from .fake_tts import FakeTTS

__all__ = ["FakeASR", "FakeLLM", "FakeTTS"]
