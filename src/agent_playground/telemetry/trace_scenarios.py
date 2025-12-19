"""
Distributed tracing scenarios for different agent playground workflows.
Each scenario represents a distinct user interaction pattern with proper trace segmentation.
"""

import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum
from contextlib import contextmanager
import asyncio

from opentelemetry import trace, baggage, context
from opentelemetry.trace import Status, StatusCode, Link
from opentelemetry.propagate import inject, extract

logger = logging.getLogger(__name__)


class TraceScenario(Enum):
    """Different tracing scenarios for the agent playground."""
    
    # User interaction scenarios
    USER_CONVERSATION = "user.conversation"  # Full conversation from start to end
    USER_COMMAND = "user.command"  # Single command execution
    USER_INTERRUPT = "user.interrupt"  # User interrupting agent
    
    # Agent processing scenarios  
    AGENT_TURN = "agent.turn"  # Single agent speaking turn
    AGENT_LISTENING = "agent.listening"  # Agent listening period
    AGENT_THINKING = "agent.thinking"  # LLM processing time
    
    # System scenarios
    ROOM_LIFECYCLE = "room.lifecycle"  # Room creation to destruction
    PARTICIPANT_SESSION = "participant.session"  # Single participant session
    HEALTH_CHECK = "system.health_check"  # Health check execution
    
    # Error scenarios
    ERROR_RECOVERY = "error.recovery"  # Error handling and recovery
    TIMEOUT_HANDLING = "timeout.handling"  # Timeout and retry logic


@dataclass
class TraceContext:
    """Context for a trace scenario."""
    scenario: TraceScenario
    room_id: Optional[str] = None
    participant_id: Optional[str] = None
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None
    turn_id: Optional[str] = None
    parent_trace_id: Optional[str] = None
    attributes: Dict[str, Any] = None
    
    def to_attributes(self) -> Dict[str, Any]:
        """Convert context to span attributes."""
        attrs = {
            "scenario": self.scenario.value,
        }
        
        if self.room_id:
            attrs["room.id"] = self.room_id
        if self.participant_id:
            attrs["participant.id"] = self.participant_id
        if self.session_id:
            attrs["session.id"] = self.session_id
        if self.conversation_id:
            attrs["conversation.id"] = self.conversation_id
        if self.turn_id:
            attrs["turn.id"] = self.turn_id
        if self.parent_trace_id:
            attrs["parent.trace_id"] = self.parent_trace_id
            
        if self.attributes:
            attrs.update(self.attributes)
            
        return attrs


class ScenarioTracer:
    """Manages scenario-based distributed tracing."""
    
    def __init__(self, service_name: str = "agent-playground"):
        self.tracer = trace.get_tracer(f"{service_name}.scenarios")
        self.active_scenarios: Dict[str, trace.Span] = {}
        
    @contextmanager
    def start_scenario(self, ctx: TraceContext):
        """
        Start a new trace scenario.
        
        This creates a new trace root for the scenario, allowing proper
        segmentation of different interaction patterns.
        """
        span_name = f"scenario.{ctx.scenario.value}"
        
        # Create new trace context for scenario isolation
        with self.tracer.start_as_current_span(
            span_name,
            kind=trace.SpanKind.SERVER,
            attributes=ctx.to_attributes(),
        ) as span:
            # Store scenario span for cross-scenario linking
            scenario_key = f"{ctx.scenario.value}:{ctx.session_id or 'global'}"
            self.active_scenarios[scenario_key] = span
            
            # Set baggage for downstream propagation
            if ctx.room_id:
                baggage.set_baggage("room.id", ctx.room_id)
            if ctx.session_id:
                baggage.set_baggage("session.id", ctx.session_id)
                
            try:
                yield span
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise
            finally:
                # Clean up active scenario
                self.active_scenarios.pop(scenario_key, None)
    
    def link_scenarios(
        self,
        current_span: trace.Span,
        linked_scenario: TraceScenario,
        session_id: Optional[str] = None
    ):
        """
        Link current span to another active scenario.
        
        This creates trace links between related scenarios for correlation.
        """
        scenario_key = f"{linked_scenario.value}:{session_id or 'global'}"
        linked_span = self.active_scenarios.get(scenario_key)
        
        if linked_span:
            link = Link(linked_span.get_span_context())
            current_span.add_link(link)
            current_span.set_attribute("linked.scenario", linked_scenario.value)


class ConversationTracer:
    """Specialized tracer for conversation flows."""
    
    def __init__(self, tracer: ScenarioTracer):
        self.scenario_tracer = tracer
        self.tracer = trace.get_tracer("agent.conversation")
        
    @contextmanager
    def conversation_turn(
        self,
        conversation_id: str,
        turn_id: str,
        speaker: str,
        room_id: Optional[str] = None
    ):
        """Trace a single conversation turn with proper parent-child relationships."""
        
        ctx = TraceContext(
            scenario=TraceScenario.AGENT_TURN if speaker == "agent" else TraceScenario.USER_COMMAND,
            conversation_id=conversation_id,
            turn_id=turn_id,
            room_id=room_id,
            attributes={"speaker": speaker}
        )
        
        with self.scenario_tracer.start_scenario(ctx) as scenario_span:
            # Create nested spans for turn phases
            with self.tracer.start_as_current_span(
                f"turn.{speaker}",
                kind=trace.SpanKind.INTERNAL,
            ) as turn_span:
                turn_span.set_attribute("conversation.id", conversation_id)
                turn_span.set_attribute("turn.id", turn_id)
                turn_span.set_attribute("turn.speaker", speaker)
                
                yield turn_span
    
    @contextmanager
    def processing_phase(self, phase: str, attributes: Optional[Dict[str, Any]] = None):
        """Trace a processing phase within a conversation turn."""
        
        with self.tracer.start_as_current_span(
            f"processing.{phase}",
            kind=trace.SpanKind.INTERNAL,
            attributes=attributes or {}
        ) as span:
            span.set_attribute("phase", phase)
            yield span


class PipelineTracer:
    """Tracer for agent processing pipeline stages."""
    
    def __init__(self):
        self.tracer = trace.get_tracer("agent.pipeline")
        
    @contextmanager
    def audio_pipeline(self, frames_count: int, sample_rate: int):
        """Trace audio processing pipeline."""
        with self.tracer.start_as_current_span(
            "pipeline.audio",
            kind=trace.SpanKind.INTERNAL,
            attributes={
                "audio.frames": frames_count,
                "audio.sample_rate": sample_rate,
            }
        ) as span:
            yield span
    
    @contextmanager
    def asr_pipeline(self, model: str, language: str = "en"):
        """Trace ASR (speech-to-text) pipeline."""
        with self.tracer.start_as_current_span(
            "pipeline.asr",
            kind=trace.SpanKind.INTERNAL,
            attributes={
                "asr.model": model,
                "asr.language": language,
            }
        ) as span:
            yield span
    
    @contextmanager
    def llm_pipeline(self, model: str, tokens_in: int = 0, tokens_out: int = 0):
        """Trace LLM inference pipeline."""
        with self.tracer.start_as_current_span(
            "pipeline.llm",
            kind=trace.SpanKind.CLIENT,  # External service call
            attributes={
                "llm.model": model,
                "llm.tokens.input": tokens_in,
                "llm.tokens.output": tokens_out,
            }
        ) as span:
            yield span
    
    @contextmanager
    def tts_pipeline(self, text_length: int, voice: str):
        """Trace TTS (text-to-speech) pipeline."""
        with self.tracer.start_as_current_span(
            "pipeline.tts",
            kind=trace.SpanKind.INTERNAL,
            attributes={
                "tts.text_length": text_length,
                "tts.voice": voice,
            }
        ) as span:
            yield span


class LiveKitTracer:
    """Tracer for LiveKit-specific operations."""
    
    def __init__(self):
        self.tracer = trace.get_tracer("agent.livekit")
        
    @contextmanager  
    def room_operation(self, operation: str, room_name: str):
        """Trace LiveKit room operations."""
        with self.tracer.start_as_current_span(
            f"livekit.room.{operation}",
            kind=trace.SpanKind.CLIENT,
            attributes={
                "livekit.room.name": room_name,
                "livekit.operation": operation,
            }
        ) as span:
            yield span
    
    @contextmanager
    def track_operation(self, operation: str, track_kind: str, track_sid: Optional[str] = None):
        """Trace LiveKit track operations."""
        attrs = {
            "livekit.track.kind": track_kind,
            "livekit.operation": operation,
        }
        if track_sid:
            attrs["livekit.track.sid"] = track_sid
            
        with self.tracer.start_as_current_span(
            f"livekit.track.{operation}",
            kind=trace.SpanKind.INTERNAL,
            attributes=attrs
        ) as span:
            yield span


# Singleton instances
_scenario_tracer: Optional[ScenarioTracer] = None
_conversation_tracer: Optional[ConversationTracer] = None
_pipeline_tracer: Optional[PipelineTracer] = None
_livekit_tracer: Optional[LiveKitTracer] = None


def get_scenario_tracer() -> ScenarioTracer:
    """Get or create the scenario tracer singleton."""
    global _scenario_tracer
    if _scenario_tracer is None:
        _scenario_tracer = ScenarioTracer()
    return _scenario_tracer


def get_conversation_tracer() -> ConversationTracer:
    """Get or create the conversation tracer singleton."""
    global _conversation_tracer
    if _conversation_tracer is None:
        _conversation_tracer = ConversationTracer(get_scenario_tracer())
    return _conversation_tracer


def get_pipeline_tracer() -> PipelineTracer:
    """Get or create the pipeline tracer singleton."""
    global _pipeline_tracer
    if _pipeline_tracer is None:
        _pipeline_tracer = PipelineTracer()
    return _pipeline_tracer


def get_livekit_tracer() -> LiveKitTracer:
    """Get or create the LiveKit tracer singleton."""
    global _livekit_tracer
    if _livekit_tracer is None:
        _livekit_tracer = LiveKitTracer()
    return _livekit_tracer


# Example usage patterns
async def example_conversation_flow():
    """Example of how to use scenario tracing in a conversation flow."""
    
    conversation_tracer = get_conversation_tracer()
    pipeline_tracer = get_pipeline_tracer()
    
    conversation_id = "conv_123"
    turn_id = "turn_456"
    
    # Start a conversation turn
    with conversation_tracer.conversation_turn(
        conversation_id=conversation_id,
        turn_id=turn_id,
        speaker="user",
        room_id="room_789"
    ):
        # Audio processing phase
        with conversation_tracer.processing_phase("audio_capture"):
            with pipeline_tracer.audio_pipeline(frames_count=1024, sample_rate=16000):
                # Simulate audio capture
                await asyncio.sleep(0.1)
        
        # ASR phase
        with conversation_tracer.processing_phase("speech_recognition"):
            with pipeline_tracer.asr_pipeline(model="vosk", language="en"):
                # Simulate ASR
                await asyncio.sleep(0.2)
        
        # LLM phase
        with conversation_tracer.processing_phase("llm_inference"):
            with pipeline_tracer.llm_pipeline(model="llama3.2", tokens_in=50, tokens_out=100):
                # Simulate LLM
                await asyncio.sleep(0.5)
        
        # TTS phase
        with conversation_tracer.processing_phase("speech_synthesis"):
            with pipeline_tracer.tts_pipeline(text_length=100, voice="en_US-lessac"):
                # Simulate TTS
                await asyncio.sleep(0.3)