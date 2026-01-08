# Distributed Tracing Architecture

## Overview

This agent playground implements comprehensive distributed tracing using OpenTelemetry, providing end-to-end observability across the entire voice agent pipeline. The tracing architecture segments different interaction scenarios while maintaining proper parent-child relationships across service boundaries.

## Architecture Components

### 1. Trace Scenarios

Different interaction patterns are segmented into distinct trace scenarios:

- **User Conversations** - Full conversation lifecycle from start to end
- **Agent Turns** - Individual speaking turns by the agent
- **User Commands** - Single command executions
- **Room Lifecycle** - LiveKit room creation to destruction
- **Participant Sessions** - Individual participant connections
- **Error Recovery** - Error handling and recovery flows

### 2. Pipeline Stages

Each stage of the voice agent pipeline is instrumented:

```
User Audio → VAD → ASR → LLM → TTS → Agent Audio
     ↓        ↓      ↓     ↓     ↓        ↓
[Audio Pipeline] [ASR Pipeline] [LLM Pipeline] [TTS Pipeline]
```

### 3. Cross-Service Correlation

Traces are correlated across:
- Frontend (Browser) ↔ Backend (Agent Worker)
- Agent Worker ↔ LiveKit Server
- Agent Worker ↔ LLM Service (Ollama)
- All services → OpenTelemetry Collector → Jaeger/Grafana

## Usage Examples

### Basic Worker Setup

```python
from agent_playground.telemetry import init_telemetry
from agent_playground.telemetry.auto_instrumentation import instrument_application

# Initialize telemetry at startup
init_telemetry(
    service_name="agent-worker",
    service_version="0.1.0",
    otlp_endpoint="otel-collector:4317"
)

# Enable automatic instrumentation
instrument_application()
```

### Tracing a Conversation Turn

```python
from agent_playground.telemetry.trace_scenarios import get_conversation_tracer, get_pipeline_tracer

conversation_tracer = get_conversation_tracer()
pipeline_tracer = get_pipeline_tracer()

# Start a conversation turn
with conversation_tracer.conversation_turn(
    conversation_id="conv_123",
    turn_id="turn_001", 
    speaker="user",
    room_id="room_abc"
):
    # Audio processing phase
    with conversation_tracer.processing_phase("audio_capture"):
        with pipeline_tracer.audio_pipeline(frames_count=1024, sample_rate=16000):
            # Process audio frames
            pass
    
    # ASR phase
    with conversation_tracer.processing_phase("speech_recognition"):
        with pipeline_tracer.asr_pipeline(model="vosk", language="en"):
            # Transcribe speech
            transcript = await transcribe_audio(audio)
    
    # LLM phase  
    with conversation_tracer.processing_phase("llm_inference"):
        with pipeline_tracer.llm_pipeline(model="llama3.2", tokens_in=50):
            # Generate response
            response = await generate_response(transcript)
    
    # TTS phase
    with conversation_tracer.processing_phase("speech_synthesis"):
        with pipeline_tracer.tts_pipeline(text_length=len(response), voice="en_US-lessac"):
            # Synthesize speech
            audio = await synthesize_speech(response)
```

### Frontend Correlation

```typescript
import { getTracer, tracedFetch, TracedWebSocket } from '@/lib/tracing';

const tracer = getTracer();

// Track user interactions
tracer.trackInteraction('click', 'start-conversation', {
  roomId: 'room_123'
});

// Traced API calls propagate context automatically
const response = await tracedFetch('/api/token', {
  method: 'POST',
  body: JSON.stringify({ roomName: 'test-room' })
});

// WebSocket with automatic trace propagation
const ws = new TracedWebSocket('ws://localhost:7880');
ws.send(JSON.stringify({ 
  type: 'join',
  roomId: 'room_123'
}));
```

### Using Method Decorators

```python
from agent_playground.telemetry.agent_instrumentation import trace_agent_method
from agent_playground.telemetry.trace_scenarios import TraceScenario

class VoiceAgent:
    @trace_agent_method("agent.initialize", TraceScenario.ROOM_LIFECYCLE)
    async def initialize(self):
        # Method is automatically traced
        pass
    
    @trace_agent_method("agent.process_audio")
    async def process_audio(self, frame):
        # Creates span with method metadata
        pass
```

## Trace Visualization

### Jaeger UI

Access traces at http://localhost:16686

Example trace hierarchy:
```
scenario.user.conversation [10.5s]
├─ turn.user [2.1s]
│  ├─ processing.audio_capture [0.3s]
│  │  └─ pipeline.audio [0.2s]
│  ├─ processing.speech_recognition [0.8s]
│  │  └─ pipeline.asr [0.7s]
│  ├─ processing.llm_inference [0.6s]
│  │  └─ pipeline.llm [0.5s]
│  └─ processing.speech_synthesis [0.4s]
│     └─ pipeline.tts [0.3s]
├─ turn.agent [1.2s]
│  └─ livekit.track.publish [0.1s]
└─ turn.user [2.3s]
   └─ ...
```

### Grafana Dashboards

Access dashboards at http://localhost:3000 (admin/admin)

Available dashboards:
- **Agent Performance** - Latency metrics per pipeline stage
- **Conversation Analytics** - Turn counts, durations, speaker patterns
- **Error Analysis** - Error rates and recovery times
- **System Overview** - Resource usage and throughput

## Trace Attributes

### Standard Attributes

All spans include:
- `service.name` - Service identifier (e.g., "agent-worker")
- `service.version` - Service version
- `deployment.environment` - Environment (local/staging/production)
- `trace.id` - Unique trace identifier
- `span.id` - Unique span identifier

### Conversation Attributes

- `conversation.id` - Unique conversation identifier
- `conversation.duration_seconds` - Total conversation time
- `conversation.total_turns` - Number of turns
- `turn.id` - Turn identifier
- `turn.speaker` - Speaker (user/agent)

### Pipeline Attributes

- `audio.frames` - Number of audio frames
- `audio.sample_rate` - Audio sample rate
- `asr.model` - ASR model used
- `asr.transcript` - Transcript preview
- `llm.model` - LLM model used  
- `llm.tokens.input` - Input token count
- `llm.tokens.output` - Output token count
- `tts.voice` - TTS voice used
- `tts.text_length` - Text length

### LiveKit Attributes

- `livekit.room.name` - Room name
- `livekit.room.sid` - Room SID
- `livekit.track.kind` - Track type (audio/video)
- `livekit.track.sid` - Track SID
- `participant.identity` - Participant identity
- `participant.sid` - Participant SID

## Performance Impact

The telemetry system has minimal performance impact:

- **CPU Overhead**: < 1-2% in typical usage
- **Memory**: ~10-20 MB for trace buffering
- **Latency**: < 1ms per span creation
- **Network**: Traces are batched and sent asynchronously

### Optimization Tips

1. **Sampling**: For production, configure sampling to reduce data volume:
   ```python
   from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
   
   sampler = TraceIdRatioBased(0.1)  # Sample 10% of traces
   ```

2. **Batching**: Traces are automatically batched (default: 512 spans or 5 seconds)

3. **Selective Instrumentation**: Disable unnecessary instrumentation:
   ```python
   instrument_application(
       enable_logging=False,  # Disable if too verbose
       enable_grpc=False,     # Disable if not using gRPC
   )
   ```

## Debugging with Traces

### Finding Slow Operations

In Jaeger, use the search filters:
- Service: `agent-worker`
- Operation: `pipeline.llm`
- Min Duration: `1s`

### Tracing Errors

Search for error traces:
- Tags: `error=true`
- Service: `agent-worker`

### Analyzing Conversation Flow

1. Search by conversation ID tag: `conversation.id=conv_123`
2. View trace timeline to see turn transitions
3. Check span logs for detailed events

### Correlating Frontend and Backend

1. Frontend adds trace ID to WebSocket messages
2. Backend extracts and continues trace
3. Search in Jaeger by trace ID to see full flow

## Troubleshooting

### No Traces Appearing

1. Check OTLP collector is running: `docker ps | grep otel`
2. Verify endpoint configuration: `OTEL_EXPORTER_OTLP_ENDPOINT`
3. Check network connectivity: `telnet otel-collector 4317`

### Missing Spans

1. Ensure telemetry is initialized before creating spans
2. Check that instrumentation is enabled for the library
3. Verify span is properly closed (context manager exits)

### High Memory Usage

1. Reduce batch size in span processor
2. Enable sampling to reduce trace volume
3. Check for span leaks (unclosed spans)

### Trace Context Not Propagating

1. Verify headers are being passed through proxies/load balancers
2. Check WebSocket message format includes `__trace` field
3. Ensure `TraceContextPropagator` is used at boundaries

## Best Practices

1. **Use Scenarios for Logical Grouping** - Group related operations under scenario spans
2. **Add Meaningful Attributes** - Include relevant metadata for debugging
3. **Handle Errors Properly** - Record exceptions and set error status
4. **Avoid Sensitive Data** - Don't include PII in span attributes
5. **Use Consistent Naming** - Follow the naming conventions in `AgentSpans`
6. **Leverage Auto-Instrumentation** - Use provided decorators and context managers
7. **Correlate Across Services** - Always propagate trace context
8. **Monitor Performance** - Track pipeline latencies for optimization

## Environment Variables

- `OTEL_EXPORTER_OTLP_ENDPOINT` - OTLP collector endpoint (default: localhost:4317)
- `OTEL_SERVICE_NAME` - Override service name
- `OTEL_SERVICE_VERSION` - Override service version  
- `OTEL_LOG_LEVEL` - Telemetry logging level (default: INFO)
- `OTEL_TRACES_SAMPLER` - Sampling strategy
- `OTEL_TRACES_SAMPLER_ARG` - Sampling ratio (0.0-1.0)