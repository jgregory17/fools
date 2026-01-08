"""
Example of a fully traced voice agent using OpenTelemetry.
Shows how to instrument the audio pipeline for distributed tracing.
"""

import asyncio
import logging
from typing import AsyncIterator

from agent_playground.telemetry import (
    init_telemetry,
    trace_span,
    trace_async_span,
    add_span_attributes,
    record_latency,
    increment_counter,
    AgentSpans,
    AgentMetrics,
)

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TracedVoiceAgent:
    """Example voice agent with full tracing instrumentation."""
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.session_id = None
        
    async def handle_session(self, room_id: str, participant_id: str):
        """Handle a complete user session with tracing."""
        
        # Create root span for the entire session
        with trace_span(
            AgentSpans.SESSION_START,
            attributes={
                "room.id": room_id,
                "participant.id": participant_id,
                "agent.id": self.agent_id,
            },
            kind=trace.SpanKind.SERVER,  # This is the entry point
        ) as session_span:
            
            self.session_id = session_span.get_span_context().trace_id
            logger.info(f"Session started: {self.session_id}")
            
            try:
                # Initialize agent
                await self._initialize_agent()
                
                # Process audio pipeline
                await self._process_audio_pipeline()
                
            finally:
                # End session
                with trace_span(AgentSpans.SESSION_END):
                    await self._cleanup_session()
    
    @trace_async_span("agent.initialization")
    async def _initialize_agent(self):
        """Initialize agent components with tracing."""
        add_span_attributes({
            "agent.mode": "selfhosted",
            "models.asr": "whisper",
            "models.llm": "llama3.2",
            "models.tts": "piper",
        })
        
        # Simulate initialization
        await asyncio.sleep(0.1)
        logger.info("Agent initialized")
    
    async def _process_audio_pipeline(self):
        """Process the complete audio pipeline with detailed tracing."""
        
        # Simulate multiple speech turns
        for turn_id in range(3):
            await self._process_speech_turn(turn_id)
    
    async def _process_speech_turn(self, turn_id: int):
        """Process a single speech turn with full tracing."""
        
        with trace_span(
            AgentSpans.TURN_START,
            attributes={"turn.id": turn_id}
        ) as turn_span:
            
            # 1. Audio ingestion and VAD
            audio_frames = await self._ingest_audio()
            
            # 2. Speech recognition (ASR)
            transcript = await self._transcribe_audio(audio_frames)
            
            # 3. LLM inference
            response = await self._generate_response(transcript)
            
            # 4. Text-to-speech (TTS)
            await self._synthesize_speech(response)
            
            # Record end-to-end latency
            with record_latency(AgentMetrics.E2E_LATENCY, {"turn.id": turn_id}):
                pass  # Latency recorded when context exits
    
    async def _ingest_audio(self) -> list:
        """Ingest audio frames with VAD detection."""
        
        with trace_span(AgentSpans.AUDIO_FRAME_IN) as span:
            # Simulate audio ingestion
            await asyncio.sleep(0.05)
            frames = ["frame1", "frame2", "frame3"]
            
            # VAD detection
            with trace_span(AgentSpans.VAD_DETECTION):
                with record_latency(AgentMetrics.VAD_LATENCY):
                    await asyncio.sleep(0.01)
                    speech_detected = True
                
                add_span_attributes({
                    "vad.speech_detected": speech_detected,
                    "audio.frames": len(frames),
                })
            
            # Increment frame counter
            increment_counter(
                AgentMetrics.AUDIO_FRAMES_IN,
                value=len(frames),
                attributes={"agent.id": self.agent_id}
            )
            
            return frames
    
    @trace_async_span(AgentSpans.ASR_TRANSCRIBE, {"asr.model": "whisper"})
    async def _transcribe_audio(self, audio_frames: list) -> str:
        """Transcribe audio using ASR."""
        
        with record_latency(
            AgentMetrics.ASR_LATENCY,
            {"model": "whisper", "frames": len(audio_frames)}
        ):
            # Simulate ASR processing
            await asyncio.sleep(0.2)
            transcript = "Hello, how can I help you today?"
        
        add_span_attributes({
            "transcript.text": transcript,
            "transcript.words": len(transcript.split()),
        })
        
        increment_counter(AgentMetrics.TRANSCRIPTS_COMPLETED)
        
        return transcript
    
    async def _generate_response(self, transcript: str) -> str:
        """Generate LLM response with detailed tracing."""
        
        with trace_span(
            AgentSpans.LLM_REQUEST,
            attributes={"llm.model": "llama3.2"}
        ):
            
            # Build context
            with trace_span(AgentSpans.LLM_CONTEXT_BUILD):
                await asyncio.sleep(0.01)
                context = f"User: {transcript}\nAssistant:"
                add_span_attributes({"context.length": len(context)})
            
            # LLM inference
            with trace_span(AgentSpans.LLM_INFERENCE):
                with record_latency(
                    AgentMetrics.LLM_LATENCY,
                    {"model": "llama3.2"}
                ):
                    await asyncio.sleep(0.3)  # Simulate LLM processing
                    response = "I'm here to help! What would you like to know?"
                
                # Record token metrics
                increment_counter(
                    AgentMetrics.LLM_TOKENS_IN,
                    value=len(transcript.split()),
                    attributes={"model": "llama3.2"}
                )
                increment_counter(
                    AgentMetrics.LLM_TOKENS_OUT,
                    value=len(response.split()),
                    attributes={"model": "llama3.2"}
                )
            
            add_span_attributes({
                "response.text": response,
                "response.words": len(response.split()),
            })
            
            return response
    
    @trace_async_span(AgentSpans.TTS_SYNTHESIS, {"tts.model": "piper"})
    async def _synthesize_speech(self, text: str):
        """Synthesize speech from text."""
        
        with record_latency(
            AgentMetrics.TTS_LATENCY,
            {"model": "piper", "text_length": len(text)}
        ):
            # Simulate TTS processing
            await asyncio.sleep(0.15)
            
            # Stream audio output
            with trace_span(AgentSpans.TTS_STREAM):
                await asyncio.sleep(0.05)
                increment_counter(
                    AgentMetrics.AUDIO_FRAMES_OUT,
                    value=10,  # Simulated frame count
                    attributes={"agent.id": self.agent_id}
                )
        
        add_span_attributes({
            "tts.duration_ms": 150,
            "tts.sample_rate": 24000,
        })
    
    async def _cleanup_session(self):
        """Clean up session resources."""
        await asyncio.sleep(0.01)
        logger.info(f"Session ended: {self.session_id}")


async def main():
    """Run the traced agent example."""
    
    # Initialize telemetry
    init_telemetry(
        service_name="agent-playground",
        otlp_endpoint="localhost:4317",
    )
    
    # Create and run agent
    agent = TracedVoiceAgent(agent_id="agent-001")
    
    # Simulate a session
    await agent.handle_session(
        room_id="room-123",
        participant_id="user-456"
    )
    
    logger.info("Example completed! Check Jaeger UI at http://localhost:16686")


if __name__ == "__main__":
    asyncio.run(main())