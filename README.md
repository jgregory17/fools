# Agent Playground (fools)

A modular, production-quality framework for building voice agents on top of LiveKit with **local-first inference**. All models run locally - no hosted APIs required.

> *"Foolish agents that might do something smart occasionally"*

## Features

- **Pluggable Modules**: Composable agents from ASR, LLM, TTS, Tools, Memory, and Policies
- **Local Inference**: Designed for faster-whisper, Ollama, Piper, and other local model runners
- **Multi-Agent Support**: Run multiple agents simultaneously with the AgentManager
- **Event-Driven Architecture**: Pub/sub event bus for clean decoupling
- **Config-Driven Composition**: YAML configs with environment variable interpolation
- **LiveKit Integration**: Proper room connection, audio track handling, streaming I/O

## Installation

```bash
# Basic installation
pip install -e .

# With all local inference dependencies
pip install -e ".[all]"

# Or specific components
pip install -e ".[asr]"      # faster-whisper
pip install -e ".[llm]"      # ollama
pip install -e ".[tts]"      # piper-tts
pip install -e ".[livekit]"  # livekit SDK
pip install -e ".[dev]"      # pytest, etc.
```

## Quick Start

### 1. Run with Fake Modules (No Setup Required)

```bash
# Run the demo agent with mock components
python examples/run_local_agent.py
```

### 2. Create a Config-Driven Agent

```yaml
# configs/my_agent.yaml
name: my_agent
description: My custom voice agent
instructions: You are a helpful assistant.

modules:
  asr:
    backend: fake  # or: faster_whisper, whisper_cpp
  llm:
    backend: fake  # or: ollama
    model: llama3.1
  tts:
    backend: fake  # or: piper

behavior:
  greeting: "Hello! How can I help you today?"
  allow_interruptions: true
```

```bash
# Run your agent
python -m agent_playground --agent my_agent
```

### 3. Run with Local Models

```yaml
# configs/local_inference.yaml
name: local_agent
instructions: You are a helpful voice assistant.

modules:
  asr:
    backend: faster_whisper
    model: base.en
    device: cuda
    compute_type: float16

  llm:
    backend: ollama
    model: llama3.1:8b
    base_url: http://localhost:11434

  tts:
    backend: piper
    model: en_US-lessac-medium
    speaker_id: 0

behavior:
  greeting: "Hello! I'm running entirely on local hardware."
  allow_interruptions: true
```

## Local Stack (Docker Compose)

Run the complete voice agent stack locally with Docker Compose. Includes LiveKit server, agent service, Ollama LLM, Prometheus metrics, and Grafana dashboards.

### Quick Start

```bash
# Start all services (first run will pull models - may take a few minutes)
docker compose up --build

# Pull an Ollama model (in another terminal)
docker compose exec ollama ollama pull llama3.2

# Scale to multiple agents
docker compose up --scale agent=3

# Stop and clean up
docker compose down -v
```

### Service URLs

| Service     | URL                                  | Credentials     |
|-------------|--------------------------------------|-----------------|
| **Web UI**  | http://localhost:3001                | -               |
| **LiveKit** | http://localhost:7880                | devkey / secret |
| **Agent**   | http://localhost:8080/healthz        | -               |
| **Metrics** | http://localhost:8080/metrics        | -               |
| **Token**   | http://localhost:8080/api/token      | -               |
| **Ollama**  | http://localhost:11434               | -               |
| **Prometheus** | http://localhost:9090             | -               |
| **Grafana** | http://localhost:3000                | admin / admin   |

### Choosing an LLM Model

```bash
# List available models
docker compose exec ollama ollama list

# Pull a specific model
docker compose exec ollama ollama pull mistral
docker compose exec ollama ollama pull llama3.2:1b  # Smaller, faster
docker compose exec ollama ollama pull codellama    # For coding tasks

# Set model via environment variable
LLM_MODEL=mistral docker compose up
```

To change the default model, edit `docker-compose.yml`:

```yaml
agent:
  environment:
    - LLM_MODEL=mistral  # Change from llama3.2
```

### Agent Configuration

The agent reads configuration from environment variables:

| Variable        | Default                    | Description                    |
|-----------------|----------------------------|--------------------------------|
| `AGENT_ID`      | (hostname)                 | Unique agent identifier        |
| `LLM_BACKEND`   | ollama                     | LLM backend (ollama, vllm)     |
| `LLM_BASE_URL`  | http://ollama:11434        | LLM service URL                |
| `LLM_MODEL`     | llama3.2                   | Model to use                   |
| `LIVEKIT_URL`   | ws://livekit:7880          | LiveKit server URL             |
| `SERVER_PORT`   | 8080                       | Metrics/health port            |

### Grafana Dashboard

The pre-built dashboard (`observability/grafana/dashboards/agent_playground.json`) shows:

- **E2E Latency**: p50/p95 latency from speech detection to first audio output
- **Stage Latency**: Per-stage breakdown (VAD, ASR, LLM, TTS)
- **LLM Metrics**: Request rate, token throughput, failure rate
- **Audio Pipeline**: Frame rates, dropped frames, interruptions
- **Queue Depth**: Backpressure monitoring
- **Agent State**: Speaking/listening state per agent

### Scaling Agents

When scaling agents, each container gets a unique ID derived from its hostname:

```bash
# Start 3 agents
docker compose up --scale agent=3

# Each agent exposes metrics on ports 8080-8089
# Prometheus auto-discovers all agents via DNS
```

Metrics are labeled with `agent_id`, so multiple agents don't collide:

```promql
# Query specific agent
stage_latency_ms{agent_id="fools-agent-1"}

# Aggregate across all agents
sum(rate(llm_requests_total[5m])) by (model)
```

### Endpoints

The agent service exposes:

- **`GET /healthz`** - Liveness check (always 200 if running)
- **`GET /readyz`** - Readiness check (200 when LLM reachable)
- **`GET /metrics`** - Prometheus metrics endpoint
- **`GET /api/v1/info`** - Agent info and status

### GPU Support

If you have an NVIDIA GPU, Ollama will use it automatically. The compose file includes GPU resource reservations:

```yaml
ollama:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
```

For CPU-only, remove the `deploy.resources.reservations` section.

### Troubleshooting

**Ollama not pulling models:**
```bash
# Check Ollama logs
docker compose logs ollama

# Manual pull
docker compose exec ollama ollama pull llama3.2
```

**Agent health check failing:**
```bash
# Check agent logs
docker compose logs agent

# Verify Ollama is reachable
docker compose exec agent curl http://ollama:11434/api/tags
```

**Grafana dashboard not loading:**
```bash
# Restart Grafana to reload provisioning
docker compose restart grafana
```

## Web UI

A modern, futuristic web interface for interacting with voice agents via WebRTC audio.

### Features

- **Real-time Voice Chat**: Talk to AI agents through your browser
- **Live Transcription**: See both user and agent speech transcribed in real-time
- **Audio Visualization**: Animated waveform orb that reacts to agent speech
- **Push-to-Talk or Voice Activated**: Choose your preferred input mode
- **Futuristic Design**: Glassmorphism, neon accents, dark mode by default
- **Device Selection**: Choose your microphone before connecting

### Using the Web UI

1. **Start the stack:**
   ```bash
   docker compose up --build
   ```

2. **Open the Web UI:**
   Navigate to **http://localhost:3001**

3. **Select an Agent:**
   - Choose from the dropdown menu (loads available agent configs)
   - Each agent has a unique personality and capabilities
   - See agent description and tags before connecting

4. **Connect to a Room:**
   - Enter a room name (default: "playground")
   - Enter your name (auto-generated if left blank)
   - Click "Join Room"

5. **Talk to the Agent:**
   - **Voice Activated (default)**: Just speak - the agent listens continuously
   - **Push-to-Talk**: Toggle the PTT switch, then hold the button while speaking

6. **View Transcripts:**
   - User speech appears in cyan on the right
   - Agent responses appear in purple on the left
   - Partial (interim) transcripts show with "..." animation

### Available Agents

| Agent | Description | Tags |
|-------|-------------|------|
| **creative_writer** | Creative writing assistant for stories, poems, brainstorming | creative, writing, default |
| **code_assistant** | Programming help, debugging, code explanations | coding, programming |
| **language_tutor** | Conversational language practice and pronunciation | education, language |
| **support_agent** | Customer support with empathetic responses | support, customer-service |
| **docker_agent** | General voice assistant for Docker deployment | docker, voice |
| **simple_voice** | Basic voice assistant for general conversations | starter, general-purpose |

To create custom agents, add YAML config files to `configs/` directory. See existing configs for examples.

### Agent Avatar

The animated orb avatar visualizes the agent's state:

| State | Color | Animation |
|-------|-------|-----------|
| **Speaking** | Cyan glow | Pulsing, waveform reactive |
| **Thinking** | Purple glow | Gentle pulse |
| **Listening** | Green glow | Subtle animation |
| **Offline** | Gray | Static |

### Environment Variables

The frontend supports these configuration options:

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_LIVEKIT_URL` | ws://localhost:7880 | LiveKit server WebSocket URL |
| `NEXT_PUBLIC_API_BASE_URL` | http://localhost:8080 | Backend API URL (for token endpoint) |
| `NEXT_PUBLIC_ENABLE_VIDEO` | false | Enable video track support (future) |

### API Endpoints

The agent service provides endpoints for agent discovery and LiveKit authentication:

```bash
# List available agents
curl "http://localhost:8080/api/agents"

# Response:
{
  "agents": [
    {
      "name": "creative_writer",
      "description": "Creative writing assistant...",
      "tags": ["creative", "writing", "default"],
      "greeting": "Hello, creative soul!...",
      "modules": {"asr": "faster_whisper", "llm": "ollama", "tts": "piper"}
    }
  ],
  "default": "creative_writer",
  "count": 6
}

# Get a token (with optional agent selection)
curl "http://localhost:8080/api/token?room=playground&identity=user-123&agent=creative_writer"

# Response:
{
  "token": "eyJ...",
  "room": "playground",
  "identity": "user-123",
  "agent": {
    "name": "creative_writer",
    "description": "Creative writing assistant...",
    "greeting": "Hello, creative soul!..."
  }
}
```

### Extending the Avatar

The UI includes an `AvatarProvider` interface for custom avatars:

```typescript
// frontend/src/components/avatar/WaveformOrbAvatar.tsx
export interface AvatarProvider {
  name: string
  render: (props: WaveformOrbAvatarProps) => React.ReactNode
}
```

To add custom avatars (e.g., 3D models, video streams):
1. Implement the `AvatarProvider` interface
2. Register your provider in the room screen component

### Future Features (Stubbed)

- **Video Track Support**: Camera toggle UI is in place but disabled
- **Avatar Providers**: Interface ready for custom avatar implementations
- **Screen Share**: Planned for future releases

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Agent Playground                          │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │   Agent 1   │  │   Agent 2   │  │   Agent N   │              │
│  │  (Config A) │  │  (Config B) │  │  (Config X) │              │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘              │
│         │                │                │                      │
│  ┌──────┴────────────────┴────────────────┴──────┐              │
│  │              Agent Manager                     │              │
│  │  - Lifecycle management                        │              │
│  │  - Room associations                           │              │
│  │  - Module factory                              │              │
│  └──────────────────────┬────────────────────────┘              │
│                         │                                        │
│  ┌──────────────────────┴────────────────────────┐              │
│  │                Event Bus                       │              │
│  │  AudioFrameIn → TranscriptPartial → LLMToken  │              │
│  │  → TTSChunk → AudioFrameOut                   │              │
│  └──────────────────────┬────────────────────────┘              │
│                         │                                        │
├─────────────────────────┼────────────────────────────────────────┤
│  Modules                │                                        │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │
│  │   ASR   │ │   LLM   │ │   TTS   │ │  Tools  │ │ Memory  │   │
│  ├─────────┤ ├─────────┤ ├─────────┤ ├─────────┤ ├─────────┤   │
│  │ fake    │ │ fake    │ │ fake    │ │registry │ │ in-mem  │   │
│  │ whisper │ │ ollama  │ │ piper   │ │ custom  │ │ vector  │   │
│  │ cpp     │ │ vllm    │ │ coqui   │ │         │ │         │   │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘   │
├─────────────────────────────────────────────────────────────────┤
│  LiveKit I/O                                                     │
│  ┌─────────────────────┐  ┌─────────────────────┐               │
│  │    Room Adapter     │  │   Audio Pipeline    │               │
│  │  - Track handling   │  │  - ASR→LLM→TTS flow │               │
│  │  - Participant mgmt │  │  - Interruption     │               │
│  │  - Audio routing    │  │  - Backpressure     │               │
│  └─────────────────────┘  └─────────────────────┘               │
└─────────────────────────────────────────────────────────────────┘
```

## Module Interfaces

All modules implement Python Protocols for clean dependency injection:

```python
from agent_playground.core.interfaces import ASRBackend, LLMBackend, TTSBackend

@runtime_checkable
class ASRBackend(Protocol):
    @property
    def sample_rate(self) -> int: ...

    async def stream(
        self,
        audio_stream: AsyncIterator[AudioFrameIn]
    ) -> AsyncIterator[SpeechEvent]: ...

@runtime_checkable
class LLMBackend(Protocol):
    async def chat(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[ToolDefinition]] = None,
    ) -> AsyncIterator[LLMResponse]: ...

@runtime_checkable
class TTSBackend(Protocol):
    @property
    def sample_rate(self) -> int: ...

    async def synthesize(
        self,
        text: str
    ) -> AsyncIterator[AudioChunk]: ...
```

## Creating Custom Agents

```python
from agent_playground.agents.base_agent import BasePlaygroundAgent

class MyCustomAgent(BasePlaygroundAgent):
    async def on_enter(self) -> None:
        """Called when agent joins a session."""
        await self.say("Welcome! How can I help?")

    async def on_user_turn_completed(
        self,
        transcript: str,
        context: ChatContext
    ) -> None:
        """Process user input before LLM inference."""
        # Add custom context injection
        context.add_system_message(
            "Remember to be concise and helpful."
        )

    async def on_exit(self) -> None:
        """Called when agent leaves session."""
        logger.info("Session ended")
```

## Event System

Subscribe to pipeline events for monitoring and customization:

```python
from agent_playground.core.event_bus import EventBus
from agent_playground.core.events import TranscriptFinal, LLMToken

bus = EventBus()

@bus.on(TranscriptFinal)
async def on_transcript(event: TranscriptFinal):
    print(f"User said: {event.text}")

@bus.on(LLMToken)
async def on_token(event: LLMToken):
    print(event.token, end="", flush=True)

# Subscribe to all events
@bus.on_all
async def log_all(event: AgentEvent):
    logger.debug(f"Event: {event}")
```

## CLI Reference

```bash
# Run a single agent
python -m agent_playground --agent simple_voice

# Run multiple agents
python -m agent_playground --agents agent1,agent2,agent3

# Development mode (auto-reload)
python -m agent_playground --agent my_agent --dev

# List available agents
python -m agent_playground --list

# Generate config template
python -m agent_playground --generate-config > my_agent.yaml

# Validate configs
python -m agent_playground --validate

# Custom config directory
python -m agent_playground --agent my_agent --config-dir ./my_configs
```

## Why This Architecture Prevents Common Pitfalls

Unlike typical voice agent implementations that require careful manual handling of edge cases, this framework is **architected to handle common pitfalls by default**. Here's how:

### Built-in Protection Mechanisms

| Pitfall | Architectural Solution | Default Behavior |
|---------|----------------------|------------------|
| **Sample Rate Mismatch** | `AudioNormalizer` at all boundaries | Auto-resample to 24kHz canonical format |
| **High Latency** | `PipelineScheduler` with bounded queues | 20ms frames, streaming throughout |
| **Echo/Feedback** | `BehaviorPolicy` with speaking state | ASR suppressed during agent speech |
| **Track Lifecycle** | `RoomAdapter` with state machine | Auto-reconnect, clean teardown |
| **Memory Growth** | `BoundedQueue` with `BackpressurePolicy` | Drop oldest frames when overloaded |
| **Interruption** | `InterruptController` with VAD | Barge-in enabled by default |
| **GPU Contention** | `ResourceManager` with device assignment | Auto device selection, TTS on CPU |
| **Context Overflow** | `ConversationMemory` with summarization | 50 turn limit, auto-summarize |

### Canonical Audio Format

All audio in the pipeline is normalized to a single canonical format:

```
Format: PCM16 signed little-endian
Sample Rate: 24kHz
Channels: Mono
Frame Duration: 20ms (480 samples)
```

This eliminates sample rate mismatch bugs. The `AudioNormalizer` handles conversion at boundaries:

```python
from agent_playground.core.audio import AudioNormalizer, CANONICAL_FORMAT

normalizer = AudioNormalizer()

# Input: 48kHz stereo from LiveKit
# Output: 24kHz mono frames, chunked to 20ms
for frame in normalizer.normalize(raw_audio, input_sample_rate=48000, input_channels=2):
    process(frame)  # Always canonical format
```

### Backpressure by Default

Every queue in the pipeline is bounded with configurable overflow handling:

```python
from agent_playground.core.scheduler import BoundedQueue, BackpressurePolicy, BackpressureMode

# Default: Drop oldest frames to prefer freshness
queue = BoundedQueue("audio_in", BackpressurePolicy(
    mode=BackpressureMode.DROP_OLDEST,  # or DROP_NEWEST, BLOCK_WITH_TIMEOUT
    max_queue_size=100,  # ~2 seconds at 20ms frames
    log_drops=True,
))
```

### Echo Suppression Without AEC

The `BehaviorPolicy` provides simple but effective echo prevention:

```python
from agent_playground.core.policies import BehaviorPolicy

policy = BehaviorPolicy()

# During agent speech, ASR is suppressed
policy.on_speech_start()
assert policy.should_process_audio(0.5) == False  # Suppressed!

policy.on_speech_end()
# Brief post-speech suppression (200ms default) for audio propagation delay
```

### Responsive Interruption Handling

The `InterruptController` coordinates VAD with TTS cancellation:

```python
from agent_playground.core.interrupts import InterruptCoordinator

coordinator = InterruptCoordinator(vad=my_vad)

async with coordinator.speaking_session() as session:
    async for chunk in tts.synthesize(text):
        if session.should_stop:
            break  # User interrupted!
        yield chunk
```

### Context Window Management

`ConversationMemory` prevents LLM context overflow automatically:

```python
from agent_playground.modules.memory import ConversationMemory

memory = ConversationMemory(max_turns=50, max_context_tokens=8000)

# When approaching limits, old turns are summarized
if memory.should_summarize():
    await memory.summarize_old_turns()

messages = memory.get_messages()  # Always within token budget
```

### Running the Demo

See all these components in action:

```bash
python examples/robust_pipeline_demo.py
```

---

## Common Pitfalls and Solutions

The following section documents the pitfalls in detail. **Note: These are handled automatically by the architecture above.** This documentation exists for understanding and customization.

### 1. Sample Rate Mismatch

**Problem**: Audio sounds distorted, too fast, or too slow.

**Cause**: Different components expect different sample rates (ASR might want 16kHz, TTS outputs 22kHz, LiveKit uses 48kHz).

**Solution**:
```python
# Always resample at boundaries
from agent_playground.livekit_io.room_adapter import AudioInputOptions

options = AudioInputOptions(
    sample_rate=24000,  # Target rate
    channels=1,
    auto_resample=True,  # Enable automatic resampling
)
```

### 2. High Latency / Buffering Issues

**Problem**: Responses feel slow, audio cuts out, or there's noticeable delay.

**Cause**: Large buffer sizes, blocking I/O, or non-streaming processing.

**Solution**:
- Use streaming throughout (AsyncIterator everywhere)
- Keep buffer sizes small (10-50ms chunks)
- Process audio in real-time, don't wait for complete utterances
```python
# Bad: Wait for complete audio
audio = await collect_all_audio(stream)
result = await asr.transcribe(audio)

# Good: Stream processing
async for event in asr.stream(audio_stream):
    await bus.emit(event)
```

### 3. Echo / Feedback Loops

**Problem**: Agent hears itself and creates feedback loops.

**Cause**: TTS output is captured by ASR input.

**Solution**:
```yaml
# In config
behavior:
  enable_aec: true  # Acoustic Echo Cancellation

# Or disable ASR while speaking
behavior:
  suppress_asr_during_speech: true
```

### 4. Track Lifecycle Issues

**Problem**: Audio stops working after participant reconnects, or tracks are duplicated.

**Cause**: Not properly handling LiveKit track subscribe/unsubscribe events.

**Solution**:
```python
# The RoomAdapter handles this automatically, but if customizing:
@room.on("track_subscribed")
async def on_track_subscribed(track, publication, participant):
    if track.kind == rtc.TrackKind.KIND_AUDIO:
        # Replace existing stream, don't add another
        await adapter.set_audio_source(track, participant.identity)

@room.on("track_unsubscribed")
async def on_track_unsubscribed(track, publication, participant):
    await adapter.remove_audio_source(participant.identity)
```

### 5. Memory Growth / Backpressure

**Problem**: Memory usage grows unbounded during long conversations.

**Cause**: Unbounded queues, not dropping frames when overwhelmed.

**Solution**:
```python
# Use bounded queues
audio_queue: asyncio.Queue[AudioFrameIn] = asyncio.Queue(maxsize=100)

# Drop frames when queue is full (prefer freshness over completeness)
try:
    audio_queue.put_nowait(frame)
except asyncio.QueueFull:
    logger.warning("Dropping audio frame due to backpressure")
```

### 6. Interruption Handling

**Problem**: Agent keeps talking even when user interrupts.

**Cause**: Not detecting speech during output, or not canceling TTS properly.

**Solution**:
```yaml
behavior:
  allow_interruptions: true
  interruption_threshold: 0.5  # VAD confidence threshold
  min_interruption_duration_ms: 200  # Avoid false positives
```

```python
# In your pipeline
if vad_detected_speech and currently_speaking:
    await tts.cancel()  # Stop current output
    await pipeline.transition_to_listening()
```

### 7. GPU Resource Contention

**Problem**: Models compete for GPU memory, causing OOM errors or slowdowns.

**Cause**: Running multiple GPU models simultaneously (ASR + LLM + TTS all on GPU).

**Solution**:
```yaml
# Distribute across devices
modules:
  asr:
    backend: faster_whisper
    device: cuda:0  # First GPU

  llm:
    backend: ollama
    # Ollama manages its own GPU allocation

  tts:
    backend: piper
    device: cpu  # TTS often fast enough on CPU
```

### 8. Context Window Overflow

**Problem**: LLM starts giving incoherent responses after long conversations.

**Cause**: Chat context exceeds model's context window.

**Solution**:
```python
# Implement context summarization
from agent_playground.modules.memory import ConversationMemory

memory = ConversationMemory(
    max_turns=20,           # Keep last N turns
    summarize_after=15,     # Summarize older turns
    max_context_tokens=4096 # Hard limit
)
```

## Project Structure

```
agent_playground/
├── src/agent_playground/
│   ├── core/
│   │   ├── interfaces.py      # Protocol definitions
│   │   ├── events.py          # Event types
│   │   ├── event_bus.py       # Pub/sub system
│   │   ├── config.py          # YAML config loading
│   │   └── agent_manager.py   # Lifecycle management
│   ├── modules/
│   │   ├── asr/               # ASR implementations
│   │   ├── llm/               # LLM implementations
│   │   ├── tts/               # TTS implementations
│   │   └── memory/            # Memory backends
│   ├── livekit_io/
│   │   ├── room_adapter.py    # LiveKit integration
│   │   └── audio_pipeline.py  # ASR→LLM→TTS flow
│   ├── tools/
│   │   ├── registry.py        # Tool registration
│   │   └── builtin.py         # Built-in tools
│   ├── agents/
│   │   ├── base_agent.py      # Agent base class
│   │   └── simple_agent.py    # Example agents
│   └── cli.py                 # CLI entrypoint
├── configs/                   # Agent configurations
├── examples/                  # Usage examples
└── tests/                     # Test suite
```

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=agent_playground

# Run specific test file
pytest tests/test_event_bus.py -v
```

## License

MIT
