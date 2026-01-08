# Agent Playground Architecture

## High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              LIVEKIT SERVER (SFU)                                │
│                         (self-hosted or LiveKit Cloud)                           │
└───────────────────────────────────┬─────────────────────────────────────────────┘
                                    │
                                    │ WebRTC / Room Protocol
                                    │
┌───────────────────────────────────▼─────────────────────────────────────────────┐
│                            AGENT PLAYGROUND SERVER                               │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │                           AgentManager                                       ││
│  │  • Creates/destroys agent instances                                         ││
│  │  • Routes room events → agents                                              ││
│  │  • Manages lifecycle (start/stop/reload)                                    ││
│  │  • Config-driven agent selection                                            ││
│  └─────────────────────────┬───────────────────────────────────────────────────┘│
│                            │                                                     │
│  ┌─────────────────────────▼───────────────────────────────────────────────────┐│
│  │                         Agent Instance (per room/participant)               ││
│  │  ┌────────────────────────────────────────────────────────────────────────┐ ││
│  │  │                    Composition Root (DI Wiring)                         │ ││
│  │  │                                                                         │ ││
│  │  │  config.yaml → ModuleFactory → [ASR, LLM, TTS, Tools, Memory, Policy]  │ ││
│  │  └────────────────────────────────────────────────────────────────────────┘ ││
│  │                                                                              ││
│  │  ┌─────────────────────────────────────────────────────────────────────────┐││
│  │  │                           Event Bus (pub/sub)                           │││
│  │  │  AudioFrameIn → TranscriptPartial → TranscriptFinal → LLMToken →       │││
│  │  │  UtteranceFinal → TTSChunk → AudioFrameOut → AgentState                │││
│  │  └─────────────────────────────────────────────────────────────────────────┘││
│  │                                                                              ││
│  │  ┌──────────────────── AUDIO PIPELINE ──────────────────────────────────┐  ││
│  │  │                                                                       │  ││
│  │  │  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐           │  ││
│  │  │  │ RoomIO  │───►│   ASR   │───►│   LLM   │───►│   TTS   │           │  ││
│  │  │  │ (input) │    │(stream) │    │(stream) │    │(stream) │           │  ││
│  │  │  └─────────┘    └─────────┘    └─────────┘    └────┬────┘           │  ││
│  │  │       ▲                              │              │                │  ││
│  │  │       │                              ▼              ▼                │  ││
│  │  │       │                        ┌──────────┐   ┌─────────┐           │  ││
│  │  │       │                        │  Tools   │   │ RoomIO  │           │  ││
│  │  │       │                        │(actions) │   │(output) │           │  ││
│  │  │       │                        └──────────┘   └─────────┘           │  ││
│  │  │       │                                                              │  ││
│  │  │       └──────────────── backpressure / cancellation ─────────────────┘  ││
│  │  └──────────────────────────────────────────────────────────────────────┘  ││
│  │                                                                              ││
│  │  ┌─────────────────── OPTIONAL MODULES ─────────────────────────────────┐  ││
│  │  │  Memory (conversation history)    Policy/Guardrails (content filter) │  ││
│  │  └─────────────────────────────────────────────────────────────────────┘  ││
│  └──────────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────────────────────┐
                    │         LOCAL MODEL RUNNERS             │
                    │                                         │
                    │  ┌──────────────┐  ┌──────────────┐    │
                    │  │faster-whisper│  │ whisper.cpp  │    │
                    │  │   (ASR)      │  │   (ASR)      │    │
                    │  └──────────────┘  └──────────────┘    │
                    │                                         │
                    │  ┌──────────────┐  ┌──────────────┐    │
                    │  │   Ollama     │  │   vLLM       │    │
                    │  │   (LLM)      │  │   (LLM)      │    │
                    │  └──────────────┘  └──────────────┘    │
                    │                                         │
                    │  ┌──────────────┐  ┌──────────────┐    │
                    │  │    Piper     │  │   Coqui-TTS  │    │
                    │  │   (TTS)      │  │   (TTS)      │    │
                    │  └──────────────┘  └──────────────┘    │
                    └─────────────────────────────────────────┘
```

## Data Flow

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              STREAMING DATA FLOW                                  │
└──────────────────────────────────────────────────────────────────────────────────┘

User speaks into microphone
         │
         ▼
┌─────────────────┐
│  WebRTC Track   │  Audio frames @ 24kHz mono, 16-bit PCM
│  (subscribed)   │  Frame size: 20-50ms chunks
└────────┬────────┘
         │
         ▼  AudioFrameIn event
┌─────────────────┐
│  ASR Backend    │  Streaming partial transcripts
│  (streaming)    │  → TranscriptPartial events
└────────┬────────┘  → TranscriptFinal event (end of utterance)
         │
         ▼  User turn complete
┌─────────────────┐
│  LLM Backend    │  Streaming token generation
│  (streaming)    │  → LLMToken events
└────────┬────────┘  → UtteranceFinal event (complete response)
         │
         ▼
┌─────────────────┐
│  TTS Backend    │  Streaming audio synthesis
│  (streaming)    │  → TTSChunk events (audio frames)
└────────┬────────┘
         │
         ▼  AudioFrameOut event
┌─────────────────┐
│  WebRTC Track   │  Published back to room
│  (published)    │  Same format: 24kHz mono PCM
└─────────────────┘
         │
         ▼
User hears agent response
```

## Module Interface Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           PROTOCOL INTERFACES (ABCs)                              │
└──────────────────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────────────┐
                    │       ASRBackend (Protocol)      │
                    ├─────────────────────────────────┤
                    │ + stream(audio) → SpeechEvent   │
                    │ + sample_rate: int              │
                    │ + num_channels: int             │
                    └───────────────┬─────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
    ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
    │   FakeASR       │  │ FasterWhisper   │  │  WhisperCpp     │
    │   (stub)        │  │ Adapter         │  │  Adapter        │
    └─────────────────┘  └─────────────────┘  └─────────────────┘


                    ┌─────────────────────────────────┐
                    │       LLMBackend (Protocol)      │
                    ├─────────────────────────────────┤
                    │ + chat(ctx, tools) → ChatChunk  │
                    │ + supports_tools: bool          │
                    └───────────────┬─────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
    ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
    │   FakeLLM       │  │ OllamaAdapter   │  │  vLLMAdapter    │
    │   (stub)        │  │                 │  │                 │
    └─────────────────┘  └─────────────────┘  └─────────────────┘


                    ┌─────────────────────────────────┐
                    │       TTSBackend (Protocol)      │
                    ├─────────────────────────────────┤
                    │ + synthesize(text) → AudioFrame │
                    │ + sample_rate: int              │
                    │ + voice: str                    │
                    └───────────────┬─────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
    ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
    │   FakeTTS       │  │  PiperAdapter   │  │  CoquiAdapter   │
    │   (stub)        │  │                 │  │                 │
    └─────────────────┘  └─────────────────┘  └─────────────────┘
```

## Common Pitfalls and Solutions

### 1. Sample Rate Mismatch
**Problem**: Audio artifacts, distortion, or silence when ASR/TTS sample rates don't match WebRTC track.

**Solution**:
- Normalize all audio to 24kHz mono PCM (LiveKit default)
- Use `AudioByteStream` utility for resampling
- Validate sample rates at pipeline boundaries

```python
# Always normalize at boundaries
if frame.sample_rate != TARGET_SAMPLE_RATE:
    frame = resample(frame, TARGET_SAMPLE_RATE)
```

### 2. Buffering / Latency Issues
**Problem**: Audio stuttering or long delays before speech starts.

**Solution**:
- Use streaming throughout (never wait for complete transcripts)
- Implement sentence-level TTS chunking
- Set appropriate frame sizes (20-50ms)
- Use preemptive generation when supported

### 3. Echo / Feedback Loops
**Problem**: Agent hears itself, creates infinite loop.

**Solution**:
- LiveKit handles echo cancellation on client side
- Use `noise_cancellation.BVC()` for background voice cancellation
- Track agent state to ignore audio while speaking

### 4. Track Lifecycle / Subscription Races
**Problem**: Missing audio frames, subscription failures, or stale tracks.

**Solution**:
- Wait for `track_subscribed` events before processing
- Handle `track_unsubscribed` gracefully
- Use `RoomIO` abstraction (handles this automatically)

### 5. Concurrency / Backpressure
**Problem**: Memory growth, dropped frames, or pipeline stalls.

**Solution**:
- Use async generators with proper cancellation
- Implement backpressure via bounded queues
- Cancel in-flight requests on interruption

### 6. Interruption Handling
**Problem**: Agent continues speaking after user interrupts.

**Solution**:
- Monitor VAD for user speech start
- Cancel TTS pipeline on interruption
- Use `SpeechHandle.interrupted` to detect
- Clear pending audio buffers

### 7. State Management Across Agents
**Problem**: Lost context when switching agents, memory leaks.

**Solution**:
- Use `session.userdata` for shared state
- Pass `ChatContext` explicitly on handoff
- Clean up resources in `on_exit` hook

### 8. GPU Resource Contention
**Problem**: OOM errors, slow inference when running multiple models.

**Solution**:
- Use model pools with max concurrency
- Implement request queuing
- Consider CPU fallback for ASR/TTS
- Monitor VRAM usage

## Configuration-Driven Agent Selection

```yaml
# agents/greeting_agent.yaml
name: greeting_agent
description: "Handles initial user greeting"

modules:
  asr:
    backend: faster_whisper
    model: base.en
    device: cuda

  llm:
    backend: ollama
    model: llama3.1:8b
    temperature: 0.7

  tts:
    backend: piper
    voice: en_US-lessac-medium

  tools:
    - name: transfer_to_support
      handler: tools.transfers.support_handoff

behavior:
  allow_interruptions: true
  greeting: "Hello! How can I help you today?"
  max_turns: 10
```

## Multi-Agent Room Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                         ROOM                                    │
│                                                                 │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │
│  │   User A     │     │   User B     │     │   Agent 1    │   │
│  │ (publisher)  │     │ (publisher)  │     │ (subscriber/ │   │
│  └──────────────┘     └──────────────┘     │  publisher)  │   │
│         │                   │              └──────────────┘   │
│         │                   │                     │            │
│         └───────────┬───────┘                     │            │
│                     │                             │            │
│              ┌──────▼───────┐             ┌──────▼───────┐    │
│              │  Audio Track │             │  Audio Track │    │
│              │  (mixed)     │             │  (agent out) │    │
│              └──────────────┘             └──────────────┘    │
│                                                                 │
│  ┌──────────────┐                                              │
│  │   Agent 2    │  ← Can run multiple agents per room          │
│  │ (different   │  ← Each with different capabilities          │
│  │  identity)   │  ← Routed by AgentManager                    │
│  └──────────────┘                                              │
└────────────────────────────────────────────────────────────────┘
```
