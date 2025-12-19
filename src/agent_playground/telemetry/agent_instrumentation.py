"""
Agent-specific instrumentation for distributed tracing.
Integrates with LiveKit agents framework for comprehensive observability.
"""

import logging
import functools
from typing import Optional, Dict, Any, Callable
from datetime import datetime
import json

from opentelemetry import trace, context as otel_context
from opentelemetry.instrumentation.utils import unwrap
from opentelemetry.trace import Status, StatusCode
from opentelemetry.propagate import inject, extract

from .trace_scenarios import (
    TraceScenario, 
    TraceContext,
    get_scenario_tracer,
    get_conversation_tracer,
    get_pipeline_tracer,
    get_livekit_tracer
)

logger = logging.getLogger(__name__)


class AgentInstrumentor:
    """
    Automatic instrumentation for LiveKit agents.
    Wraps key agent methods with appropriate tracing.
    """
    
    def __init__(self):
        self.scenario_tracer = get_scenario_tracer()
        self.conversation_tracer = get_conversation_tracer()
        self.pipeline_tracer = get_pipeline_tracer()
        self.livekit_tracer = get_livekit_tracer()
        self._instrumented = False
        
    def instrument(self, agent_instance):
        """
        Instrument a LiveKit agent instance with tracing.
        
        Args:
            agent_instance: The agent instance to instrument
        """
        if self._instrumented:
            logger.warning("Agent already instrumented")
            return
            
        # Instrument key agent methods
        self._instrument_lifecycle_methods(agent_instance)
        self._instrument_audio_methods(agent_instance)
        self._instrument_llm_methods(agent_instance)
        self._instrument_tts_methods(agent_instance)
        
        self._instrumented = True
        logger.info("Agent instrumentation complete")
    
    def _instrument_lifecycle_methods(self, agent):
        """Instrument agent lifecycle methods."""
        
        # Instrument on_room_connected
        if hasattr(agent, 'on_room_connected'):
            original = agent.on_room_connected
            
            @functools.wraps(original)
            async def traced_on_room_connected(room):
                ctx = TraceContext(
                    scenario=TraceScenario.ROOM_LIFECYCLE,
                    room_id=room.name,
                    attributes={"event": "room_connected"}
                )
                
                with self.scenario_tracer.start_scenario(ctx):
                    with self.livekit_tracer.room_operation("connect", room.name):
                        return await original(room)
            
            agent.on_room_connected = traced_on_room_connected
        
        # Instrument on_participant_connected
        if hasattr(agent, 'on_participant_connected'):
            original = agent.on_participant_connected
            
            @functools.wraps(original)
            async def traced_on_participant_connected(participant):
                ctx = TraceContext(
                    scenario=TraceScenario.PARTICIPANT_SESSION,
                    participant_id=participant.sid,
                    attributes={"event": "participant_connected"}
                )
                
                with self.scenario_tracer.start_scenario(ctx):
                    return await original(participant)
            
            agent.on_participant_connected = traced_on_participant_connected
    
    def _instrument_audio_methods(self, agent):
        """Instrument audio processing methods."""
        
        if hasattr(agent, 'process_audio_frame'):
            original = agent.process_audio_frame
            
            @functools.wraps(original)
            async def traced_process_audio(frame):
                with self.pipeline_tracer.audio_pipeline(
                    frames_count=len(frame.data) if hasattr(frame, 'data') else 0,
                    sample_rate=frame.sample_rate if hasattr(frame, 'sample_rate') else 16000
                ):
                    return await original(frame)
            
            agent.process_audio_frame = traced_process_audio
    
    def _instrument_llm_methods(self, agent):
        """Instrument LLM interaction methods."""
        
        if hasattr(agent, 'generate_response'):
            original = agent.generate_response
            
            @functools.wraps(original)
            async def traced_generate_response(prompt, **kwargs):
                model = kwargs.get('model', 'unknown')
                
                with self.pipeline_tracer.llm_pipeline(
                    model=model,
                    tokens_in=len(prompt.split()) * 2,  # Rough estimate
                ):
                    response = await original(prompt, **kwargs)
                    
                    # Update span with actual token counts if available
                    span = trace.get_current_span()
                    if hasattr(response, 'usage'):
                        span.set_attribute("llm.tokens.actual_input", response.usage.prompt_tokens)
                        span.set_attribute("llm.tokens.actual_output", response.usage.completion_tokens)
                    
                    return response
            
            agent.generate_response = traced_generate_response
    
    def _instrument_tts_methods(self, agent):
        """Instrument TTS synthesis methods."""
        
        if hasattr(agent, 'synthesize_speech'):
            original = agent.synthesize_speech
            
            @functools.wraps(original)
            async def traced_synthesize_speech(text, voice=None):
                with self.pipeline_tracer.tts_pipeline(
                    text_length=len(text),
                    voice=voice or "default"
                ):
                    return await original(text, voice)
            
            agent.synthesize_speech = traced_synthesize_speech


class ConversationTracker:
    """
    Tracks conversation state for proper trace correlation.
    Maintains conversation and turn IDs across the agent lifecycle.
    """
    
    def __init__(self):
        self.current_conversation_id: Optional[str] = None
        self.current_turn_id: Optional[str] = None
        self.turn_counter = 0
        self.conversation_start = None
        self.conversation_tracer = get_conversation_tracer()
        
    def start_conversation(self, room_id: str) -> str:
        """Start a new conversation and return its ID."""
        self.conversation_start = datetime.utcnow()
        self.current_conversation_id = f"conv_{room_id}_{self.conversation_start.timestamp():.0f}"
        self.turn_counter = 0
        
        # Add conversation metadata to current span
        span = trace.get_current_span()
        if span:
            span.set_attribute("conversation.id", self.current_conversation_id)
            span.set_attribute("conversation.start_time", self.conversation_start.isoformat())
        
        logger.info(f"Started conversation: {self.current_conversation_id}")
        return self.current_conversation_id
    
    def start_turn(self, speaker: str, room_id: Optional[str] = None) -> str:
        """Start a new conversation turn."""
        if not self.current_conversation_id:
            self.start_conversation(room_id or "unknown")
        
        self.turn_counter += 1
        self.current_turn_id = f"turn_{self.turn_counter:04d}"
        
        logger.debug(f"Started turn {self.current_turn_id} for speaker {speaker}")
        return self.current_turn_id
    
    def end_turn(self):
        """End the current turn."""
        if self.current_turn_id:
            logger.debug(f"Ended turn {self.current_turn_id}")
            self.current_turn_id = None
    
    def end_conversation(self):
        """End the current conversation."""
        if self.current_conversation_id:
            duration = (datetime.utcnow() - self.conversation_start).total_seconds()
            
            span = trace.get_current_span()
            if span:
                span.set_attribute("conversation.duration_seconds", duration)
                span.set_attribute("conversation.total_turns", self.turn_counter)
            
            logger.info(f"Ended conversation {self.current_conversation_id}: {self.turn_counter} turns, {duration:.1f}s")
            
            self.current_conversation_id = None
            self.current_turn_id = None


class TraceContextPropagator:
    """
    Handles trace context propagation between frontend and backend.
    Ensures proper parent-child relationships across service boundaries.
    """
    
    @staticmethod
    def inject_context(carrier: Dict[str, Any]) -> Dict[str, Any]:
        """
        Inject current trace context into a carrier (e.g., WebSocket message).
        
        Args:
            carrier: Dictionary to inject context into
            
        Returns:
            Carrier with injected trace context
        """
        inject(carrier)
        
        # Add additional metadata
        span = trace.get_current_span()
        if span and span.is_recording():
            context = span.get_span_context()
            carrier['trace_id'] = format(context.trace_id, '032x')
            carrier['span_id'] = format(context.span_id, '016x')
            
        return carrier
    
    @staticmethod
    def extract_context(carrier: Dict[str, Any]) -> otel_context.Context:
        """
        Extract trace context from a carrier.
        
        Args:
            carrier: Dictionary containing trace context
            
        Returns:
            Extracted context
        """
        return extract(carrier)
    
    @staticmethod
    def continue_trace(carrier: Dict[str, Any], operation: str):
        """
        Continue a trace from propagated context.
        
        Args:
            carrier: Dictionary containing trace context
            operation: Name of the operation to trace
        """
        ctx = TraceContextPropagator.extract_context(carrier)
        
        tracer = trace.get_tracer("agent.propagated")
        with tracer.start_as_current_span(
            operation,
            context=ctx,
            kind=trace.SpanKind.SERVER
        ) as span:
            if 'trace_id' in carrier:
                span.set_attribute("parent.trace_id", carrier['trace_id'])
            
            return span


class MetricsCollector:
    """
    Collects metrics from traces for monitoring.
    Integrates with the OTLP metrics pipeline.
    """
    
    def __init__(self):
        self.conversation_durations = []
        self.turn_counts = []
        self.llm_latencies = []
        self.tts_latencies = []
        self.asr_latencies = []
        
    def record_conversation_metrics(self, duration: float, turn_count: int):
        """Record conversation-level metrics."""
        self.conversation_durations.append(duration)
        self.turn_counts.append(turn_count)
        
        # Add to current span as events
        span = trace.get_current_span()
        if span:
            span.add_event("conversation.complete", {
                "duration_seconds": duration,
                "turn_count": turn_count,
            })
    
    def record_pipeline_latency(self, pipeline: str, latency_ms: float):
        """Record pipeline processing latency."""
        if pipeline == "llm":
            self.llm_latencies.append(latency_ms)
        elif pipeline == "tts":
            self.tts_latencies.append(latency_ms)
        elif pipeline == "asr":
            self.asr_latencies.append(latency_ms)
        
        span = trace.get_current_span()
        if span:
            span.set_attribute(f"{pipeline}.latency_ms", latency_ms)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary metrics for reporting."""
        
        def safe_avg(lst):
            return sum(lst) / len(lst) if lst else 0
        
        return {
            "conversations": {
                "count": len(self.conversation_durations),
                "avg_duration_seconds": safe_avg(self.conversation_durations),
                "avg_turn_count": safe_avg(self.turn_counts),
            },
            "latencies_ms": {
                "llm_avg": safe_avg(self.llm_latencies),
                "tts_avg": safe_avg(self.tts_latencies),
                "asr_avg": safe_avg(self.asr_latencies),
            }
        }


# Global instances
_agent_instrumentor: Optional[AgentInstrumentor] = None
_conversation_tracker: Optional[ConversationTracker] = None
_metrics_collector: Optional[MetricsCollector] = None


def get_agent_instrumentor() -> AgentInstrumentor:
    """Get or create the agent instrumentor singleton."""
    global _agent_instrumentor
    if _agent_instrumentor is None:
        _agent_instrumentor = AgentInstrumentor()
    return _agent_instrumentor


def get_conversation_tracker() -> ConversationTracker:
    """Get or create the conversation tracker singleton."""
    global _conversation_tracker
    if _conversation_tracker is None:
        _conversation_tracker = ConversationTracker()
    return _conversation_tracker


def get_metrics_collector() -> MetricsCollector:
    """Get or create the metrics collector singleton."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


# Convenience decorator for tracing agent methods
def trace_agent_method(operation: str, scenario: Optional[TraceScenario] = None):
    """
    Decorator for tracing agent methods with proper context.
    
    Args:
        operation: Name of the operation
        scenario: Optional trace scenario to use
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            tracer = trace.get_tracer("agent.methods")
            
            # Build attributes from method signature
            attributes = {
                "method": func.__name__,
                "class": self.__class__.__name__,
            }
            
            # Add scenario context if provided
            if scenario:
                ctx = TraceContext(
                    scenario=scenario,
                    attributes=attributes
                )
                with get_scenario_tracer().start_scenario(ctx):
                    return await func(self, *args, **kwargs)
            else:
                with tracer.start_as_current_span(
                    operation,
                    kind=trace.SpanKind.INTERNAL,
                    attributes=attributes
                ):
                    return await func(self, *args, **kwargs)
        
        return wrapper
    return decorator