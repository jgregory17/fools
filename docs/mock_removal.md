# Mock Removal Report

This document tracks the replacement of mock/stub/placeholder components with real working implementations in the agent_playground voice pipeline.

## Summary

| Component | Before | After | Status |
|-----------|--------|-------|--------|
| ASR Backend | `FakeASR` (random text) | `FasterWhisperASR` (real transcription) | Complete |
| LLM Backend | `FakeLLM` (echoes input) | `OllamaLLM` (real inference) | Complete |
| TTS Backend | `FakeTTS` (silence) | `PiperTTS` (real synthesis) | Complete |
| VAD Backend | `SileroVAD` | `SileroVAD` (already real) | N/A |
| LiveKit Room | `MockRoomAdapter` | `RoomAdapter` (real LiveKit) | Complete |
| Config Defaults | `backend: fake` | `backend: <real>` | Complete |

## Detailed Changes

### 1. ASR - Automatic Speech Recognition

**File:** `src/agent_playground/modules/asr/faster_whisper_asr.py`

| Aspect | Before | After |
|--------|--------|-------|
| Model Loading | Placeholder pass | `WhisperModel(model_size, device, compute_type)` |
| Transcription | Placeholder pass | Chunked 2s processing with VAD filter |
| Audio Format | Not handled | 24kHz → 16kHz resampling via linear interpolation |
| Streaming | Not implemented | Async generator yielding `ASRResult` objects |
| Cancellation | Not implemented | `cancel()` method sets `_cancelled` flag |
| Health Check | Not implemented | `health_check()` validates model loaded |

**Alternative:** `WhisperCppASR` using whisper-cpp-python for CPU-optimized inference.

**Exports Changed:**
```python
# Before (modules/asr/__init__.py)
from .fake_asr import FakeASR
__all__ = ["FasterWhisperASR", "FakeASR"]

# After
from .faster_whisper_asr import FasterWhisperASR, WhisperCppASR
__all__ = ["FasterWhisperASR", "WhisperCppASR"]
```

### 2. LLM - Large Language Model

**File:** `src/agent_playground/modules/llm/ollama_llm.py`

| Aspect | Before | After |
|--------|--------|-------|
| HTTP Client | Placeholder | `httpx.AsyncClient` with streaming |
| Streaming | Not implemented | Line-by-line JSON parsing from `/api/chat` |
| Tool Calling | Placeholder | Full Ollama tool format support |
| Cancellation | Not implemented | Request cancellation via `cancel()` |
| Health Check | Not implemented | GET `/api/tags` endpoint check |
| Timeout | Not handled | Configurable via `httpx.Timeout` |

**Alternative:** `VLLMAdapter` using OpenAI-compatible API with SSE format.

**Exports Changed:**
```python
# Before (modules/llm/__init__.py)
from .fake_llm import FakeLLM
__all__ = ["OllamaLLM", "FakeLLM"]

# After
from .ollama_llm import OllamaLLM, VLLMAdapter
__all__ = ["OllamaLLM", "VLLMAdapter"]
```

### 3. TTS - Text-to-Speech

**File:** `src/agent_playground/modules/tts/piper_tts.py`

| Aspect | Before | After |
|--------|--------|-------|
| Voice Loading | Placeholder | `PiperVoice.load()` with auto-download |
| Synthesis | Placeholder | `synthesize_stream_raw()` with sentence chunking |
| Audio Format | Not handled | 22050Hz → 24kHz resampling |
| Streaming | Not implemented | Sentence-by-sentence async generator |
| Cancellation | Not implemented | `cancel()` with immediate flag check |
| Voice Download | Not implemented | Auto-download from Hugging Face via `ensure_voice_exists()` |

**Alternative:** `CoquiTTS` using TTS library for more voice variety.

**Exports Changed:**
```python
# Before (modules/tts/__init__.py)
from .fake_tts import FakeTTS
__all__ = ["PiperTTS", "FakeTTS"]

# After
from .piper_tts import PiperTTS, CoquiTTS
__all__ = ["PiperTTS", "CoquiTTS"]
```

### 4. VAD - Voice Activity Detection

**File:** `src/agent_playground/modules/vad/silero_vad.py`

**Status:** Already real implementation - no changes needed.

Loads model from `snakers4/silero-vad` via torch hub, falls back to energy-based VAD if torch unavailable.

### 5. LiveKit Room Adapter

**File:** `src/agent_playground/livekit_io/room_adapter.py`

| Aspect | Before (placeholder) | After |
|--------|---------------------|-------|
| Audio Input | TODO comments | `rtc.AudioStream(track, sample_rate, num_channels)` |
| Audio Output | TODO comments | `rtc.AudioSource()` + `rtc.LocalAudioTrack.create_audio_track()` |
| Frame Publishing | TODO comments | `audio_source.capture_frame(frame)` |
| Track Publishing | TODO comments | `room.local_participant.publish_track()` |

**MockRoomAdapter retained** for testing purposes - it's appropriate for unit tests without LiveKit server.

### 6. Configuration Defaults

**File:** `src/agent_playground/core/config.py`

**AgentConfig Dataclass (lines 303-305):**
```python
# Before
asr_config: ASRConfig = field(default_factory=lambda: ASRConfig(backend="fake"))
llm_config: LLMConfig = field(default_factory=lambda: LLMConfig(backend="fake"))
tts_config: TTSConfig = field(default_factory=lambda: TTSConfig(backend="fake"))

# After
asr_config: ASRConfig = field(default_factory=lambda: ASRConfig(backend="faster_whisper"))
llm_config: LLMConfig = field(default_factory=lambda: LLMConfig(backend="ollama"))
tts_config: TTSConfig = field(default_factory=lambda: TTSConfig(backend="piper"))
```

**from_dict() Method (lines 345-347):** Same changes applied.

**generate_default_config() YAML (lines 649-660):**
```yaml
modules:
  asr:
    backend: faster_whisper  # Was: fake
  llm:
    backend: ollama  # Was: fake
  tts:
    backend: piper  # Was: fake
```

### 7. Agent Manager Registration

**File:** `src/agent_playground/core/agent_manager.py`

**_register_defaults() method:**
```python
# Real backends registered first (required)
self.register_asr_backend("faster_whisper", FasterWhisperASR)
self.register_asr_backend("whisper_cpp", WhisperCppASR)
self.register_llm_backend("ollama", OllamaLLM)
self.register_llm_backend("vllm", VLLMAdapter)
self.register_tts_backend("piper", PiperTTS)
self.register_tts_backend("coqui", CoquiTTS)

# Dev stubs optional (try/except)
try:
    from ..modules.dev_stubs import FakeASR, FakeLLM, FakeTTS
    self.register_asr_backend("fake", FakeASR)
    # ...
except ImportError:
    pass
```

### 8. Dev Stubs Directory

**Location:** `src/agent_playground/modules/dev_stubs/`

Fake backends moved here for development/testing only:
- `fake_asr.py` - Returns random stock phrases
- `fake_llm.py` - Echoes input with delay
- `fake_tts.py` - Returns silence

**Import Fix:** Changed from relative `...core` to absolute `agent_playground.core` to work from deeper directory.

## Dependencies

Real backends require these packages:

```bash
# ASR
pip install faster-whisper  # or whisper-cpp-python

# LLM
pip install httpx  # Ollama client
# Also need Ollama running: ollama serve && ollama pull llama3.2

# TTS
pip install piper-tts  # or TTS for Coqui

# VAD (already in requirements)
pip install torch  # For Silero VAD

# LiveKit
pip install livekit
```

## Verification

Run the demo to verify all components work:

```bash
python examples/robust_pipeline_demo.py
```

All 7 demos should pass with real audio processing.
