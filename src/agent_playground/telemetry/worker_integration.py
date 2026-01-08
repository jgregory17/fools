"""
Integration example showing how to use the distributed tracing architecture
in the agent playground worker.
"""

import logging
import asyncio
from typing import Optional
from datetime import datetime

from livekit import agents, rtc
from livekit.agents import JobContext, WorkerOptions, cli

from .tracing import init_telemetry
from .agent_instrumentation import (
    get_agent_instrumentor,
    get_conversation_tracker,
    get_metrics_collector,
    trace_agent_method,
    TraceContextPropagator
)
from .trace_scenarios import (
    TraceScenario,
    TraceContext,
    get_scenario_tracer,
    get_conversation_tracer,
    get_pipeline_tracer,
    get_livekit_tracer
)

logger = logging.getLogger(__name__)


class TracedVoiceAgent:
    """
    Example voice agent with comprehensive distributed tracing.
    Shows how to properly instrument each phase of agent operation.
    """
    
    def __init__(self, ctx: JobContext):
        self.ctx = ctx
        self.room = ctx.room
        
        # Initialize tracing components
        self.instrumentor = get_agent_instrumentor()
        self.conversation_tracker = get_conversation_tracker()
        self.metrics_collector = get_metrics_collector()
        self.scenario_tracer = get_scenario_tracer()
        self.conversation_tracer = get_conversation_tracer()
        self.pipeline_tracer = get_pipeline_tracer()
        self.livekit_tracer = get_livekit_tracer()
        
        # Track conversation state
        self.conversation_start_time = None
        self.current_speaker = None
        
    @trace_agent_method("agent.initialize", TraceScenario.ROOM_LIFECYCLE)
    async def initialize(self):
        """Initialize the agent with tracing."""
        logger.info(f"Initializing agent for room {self.room.name}")
        
        # Start room lifecycle trace
        ctx = TraceContext(
            scenario=TraceScenario.ROOM_LIFECYCLE,
            room_id=self.room.name,
            session_id=self.ctx.job.id,
            attributes={
                "room.name": self.room.name,
                "agent.version": "0.1.0",
            }
        )
        
        with self.scenario_tracer.start_scenario(ctx) as span:
            span.set_attribute("room.participant_count", len(self.room.participants))
            
            # Subscribe to room events
            self.room.on("participant_connected", self.on_participant_connected)
            self.room.on("participant_disconnected", self.on_participant_disconnected)
            self.room.on("track_subscribed", self.on_track_subscribed)
            
            # Start conversation tracking
            self.conversation_tracker.start_conversation(self.room.name)
            self.conversation_start_time = datetime.utcnow()
    
    @trace_agent_method("participant.connected", TraceScenario.PARTICIPANT_SESSION)
    async def on_participant_connected(self, participant: rtc.RemoteParticipant):
        """Handle participant connection with tracing."""
        
        ctx = TraceContext(
            scenario=TraceScenario.PARTICIPANT_SESSION,
            room_id=self.room.name,
            participant_id=participant.sid,
            session_id=self.ctx.job.id,
            attributes={
                "participant.identity": participant.identity,
                "participant.name": participant.name,
            }
        )
        
        with self.scenario_tracer.start_scenario(ctx):
            logger.info(f"Participant connected: {participant.identity}")
            
            # Link to room lifecycle
            span = trace.get_current_span()
            self.scenario_tracer.link_scenarios(
                span, 
                TraceScenario.ROOM_LIFECYCLE,
                self.ctx.job.id
            )
    
    @trace_agent_method("track.subscribed")
    async def on_track_subscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant
    ):
        """Handle track subscription with tracing."""
        
        with self.livekit_tracer.track_operation(
            "subscribe",
            track.kind.value,
            track.sid
        ) as span:
            span.set_attribute("participant.identity", participant.identity)
            
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                # Start audio processing pipeline
                asyncio.create_task(self.process_audio_track(track, participant))
    
    async def process_audio_track(self, track: rtc.Track, participant: rtc.RemoteParticipant):
        """Process incoming audio with comprehensive tracing."""
        
        audio_stream = rtc.AudioStream(track)
        
        async for audio_frame in audio_stream:
            # Start a new turn if needed
            if self.current_speaker != participant.identity:
                if self.current_speaker:
                    self.conversation_tracker.end_turn()
                
                self.current_speaker = participant.identity
                turn_id = self.conversation_tracker.start_turn("user", self.room.name)
                
                # Create turn context
                with self.conversation_tracer.conversation_turn(
                    conversation_id=self.conversation_tracker.current_conversation_id,
                    turn_id=turn_id,
                    speaker="user",
                    room_id=self.room.name
                ):
                    await self.process_user_turn(audio_frame, participant)
            else:
                # Continue current turn
                await self.process_audio_frame(audio_frame)
    
    async def process_user_turn(self, audio_frame, participant: rtc.RemoteParticipant):
        """Process a complete user turn with all pipeline stages."""
        
        # Audio processing
        with self.conversation_tracer.processing_phase("audio_capture", {
            "participant": participant.identity
        }):
            with self.pipeline_tracer.audio_pipeline(
                frames_count=len(audio_frame.data),
                sample_rate=audio_frame.sample_rate
            ):
                # VAD and audio processing
                processed_audio = await self.apply_vad(audio_frame)
        
        # Speech recognition
        with self.conversation_tracer.processing_phase("speech_recognition"):
            with self.pipeline_tracer.asr_pipeline(
                model="vosk-small-en",
                language="en"
            ) as span:
                start_time = asyncio.get_event_loop().time()
                transcript = await self.transcribe_audio(processed_audio)
                
                # Record metrics
                latency_ms = (asyncio.get_event_loop().time() - start_time) * 1000
                self.metrics_collector.record_pipeline_latency("asr", latency_ms)
                span.set_attribute("asr.transcript", transcript[:100])  # First 100 chars
        
        # Generate response
        with self.conversation_tracer.processing_phase("llm_inference"):
            with self.pipeline_tracer.llm_pipeline(
                model="llama3.2",
                tokens_in=len(transcript.split())
            ) as span:
                start_time = asyncio.get_event_loop().time()
                response = await self.generate_response(transcript)
                
                # Record metrics
                latency_ms = (asyncio.get_event_loop().time() - start_time) * 1000
                self.metrics_collector.record_pipeline_latency("llm", latency_ms)
                span.set_attribute("llm.response_preview", response[:100])
        
        # Synthesize speech
        with self.conversation_tracer.processing_phase("speech_synthesis"):
            with self.pipeline_tracer.tts_pipeline(
                text_length=len(response),
                voice="en_US-lessac-medium"
            ) as span:
                start_time = asyncio.get_event_loop().time()
                audio_data = await self.synthesize_speech(response)
                
                # Record metrics
                latency_ms = (asyncio.get_event_loop().time() - start_time) * 1000
                self.metrics_collector.record_pipeline_latency("tts", latency_ms)
        
        # Publish audio response
        with self.livekit_tracer.track_operation("publish", "audio"):
            await self.publish_audio_response(audio_data)
        
        # End user turn, start agent turn
        self.conversation_tracker.end_turn()
        self.current_speaker = "agent"
        self.conversation_tracker.start_turn("agent", self.room.name)
    
    async def apply_vad(self, audio_frame):
        """Apply voice activity detection."""
        # Implementation would go here
        return audio_frame
    
    async def transcribe_audio(self, audio):
        """Transcribe audio to text."""
        # Implementation would go here
        return "Hello, how can I help you today?"
    
    async def generate_response(self, transcript: str) -> str:
        """Generate LLM response."""
        # Implementation would go here
        return "I'm here to assist you with your questions."
    
    async def synthesize_speech(self, text: str):
        """Synthesize speech from text."""
        # Implementation would go here
        return b"audio_data"
    
    async def publish_audio_response(self, audio_data):
        """Publish audio response to room."""
        # Implementation would go here
        pass
    
    async def process_audio_frame(self, audio_frame):
        """Process a single audio frame."""
        # Implementation would go here
        pass
    
    @trace_agent_method("participant.disconnected")
    async def on_participant_disconnected(self, participant: rtc.RemoteParticipant):
        """Handle participant disconnection."""
        logger.info(f"Participant disconnected: {participant.identity}")
        
        # End conversation if this was the last participant
        if len(self.room.participants) == 0:
            await self.end_conversation()
    
    async def end_conversation(self):
        """End the conversation and collect metrics."""
        if self.conversation_start_time:
            duration = (datetime.utcnow() - self.conversation_start_time).total_seconds()
            turn_count = self.conversation_tracker.turn_counter
            
            # Record metrics
            self.metrics_collector.record_conversation_metrics(duration, turn_count)
            
            # End conversation tracking
            self.conversation_tracker.end_conversation()
            
            # Log metrics summary
            summary = self.metrics_collector.get_summary()
            logger.info(f"Conversation metrics: {summary}")


async def entrypoint(ctx: JobContext):
    """
    Main entry point for the traced agent worker.
    This shows how to initialize tracing and create an instrumented agent.
    """
    
    # Initialize OpenTelemetry tracing
    init_telemetry(
        service_name="agent-worker",
        service_version="0.1.0",
        otlp_endpoint="otel-collector:4317",  # Docker service name
        enabled=True
    )
    
    # Extract trace context from job metadata if present
    if ctx.job.metadata:
        # Continue trace from frontend if context was propagated
        carrier = ctx.job.metadata
        TraceContextPropagator.continue_trace(carrier, "agent.job.start")
    
    # Create and initialize traced agent
    agent = TracedVoiceAgent(ctx)
    await agent.initialize()
    
    # Keep agent running
    await asyncio.Future()


def create_traced_worker() -> WorkerOptions:
    """
    Create worker options with tracing enabled.
    This is what you'd use in your main worker script.
    """
    return WorkerOptions(
        entrypoint_fnc=entrypoint,
        # Additional worker options can go here
    )


# Example of how to use in your main worker script:
if __name__ == "__main__":
    # Initialize base telemetry for the worker process
    init_telemetry(
        service_name="agent-worker-main",
        service_version="0.1.0",
        enabled=True
    )
    
    # Run the worker with CLI
    cli.run_app(create_traced_worker())