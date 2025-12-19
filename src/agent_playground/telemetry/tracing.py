"""
Distributed tracing setup for Agent Playground using OpenTelemetry.
"""

import os
import logging
import functools
from typing import Optional, Dict, Any, Callable
from contextlib import contextmanager
import asyncio

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.trace import Status, StatusCode, Span
from opentelemetry.propagate import inject, extract
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger(__name__)

# Global tracer instance
_tracer: Optional[trace.Tracer] = None


def init_telemetry(
    service_name: str = "agent-playground",
    service_version: str = "0.1.0",
    otlp_endpoint: str = None,
    enabled: bool = True,
) -> None:
    """
    Initialize OpenTelemetry tracing.
    
    Args:
        service_name: Name of the service for trace identification
        service_version: Version of the service
        otlp_endpoint: OTLP collector endpoint (default: localhost:4317)
        enabled: Whether tracing is enabled
    """
    global _tracer
    
    if not enabled:
        logger.info("Tracing disabled")
        _tracer = trace.get_tracer(__name__)
        return
    
    # Configure resource with service information
    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
        "deployment.environment": os.getenv("ENVIRONMENT", "local"),
        "agent.mode": os.getenv("AGENT_MODE", "selfhosted"),
    })
    
    # Create tracer provider
    provider = TracerProvider(resource=resource)
    
    # Configure OTLP exporter
    otlp_endpoint = otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")
    exporter = OTLPSpanExporter(
        endpoint=otlp_endpoint,
        insecure=True,  # For local development
    )
    
    # Add batch processor for better performance
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
    
    # Set global tracer provider
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(__name__, service_version)
    
    logger.info(f"Tracing initialized: {service_name}@{service_version} -> {otlp_endpoint}")


def create_tracer(name: str) -> trace.Tracer:
    """Create a named tracer for a specific component."""
    if _tracer is None:
        init_telemetry()
    return trace.get_tracer(name)


@contextmanager
def trace_span(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
):
    """
    Context manager for creating trace spans.
    
    Example:
        with trace_span("process_audio", {"frames": len(frames)}):
            process_audio_frames(frames)
    """
    tracer = _tracer or trace.get_tracer(__name__)
    
    with tracer.start_as_current_span(
        name,
        kind=kind,
        attributes=attributes or {},
    ) as span:
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise


def trace_async_span(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
):
    """
    Decorator for tracing async functions.
    
    Example:
        @trace_async_span("transcribe_audio", {"model": "whisper"})
        async def transcribe(audio):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = _tracer or trace.get_tracer(__name__)
            
            with tracer.start_as_current_span(
                name,
                kind=kind,
                attributes=attributes or {},
            ) as span:
                # Add function arguments as span attributes
                span.set_attribute("function.name", func.__name__)
                
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
        
        return wrapper
    return decorator


def add_span_attributes(attributes: Dict[str, Any]) -> None:
    """Add attributes to the current active span."""
    span = trace.get_current_span()
    if span and span.is_recording():
        for key, value in attributes.items():
            # Convert complex types to strings
            if isinstance(value, (dict, list)):
                value = str(value)
            span.set_attribute(key, value)


def record_error(error: Exception, description: Optional[str] = None) -> None:
    """Record an error in the current span."""
    span = trace.get_current_span()
    if span and span.is_recording():
        span.record_exception(error)
        if description:
            span.add_event("error", {"description": description})
        span.set_status(Status(StatusCode.ERROR, str(error)))


def get_current_trace_id() -> Optional[str]:
    """Get the current trace ID for correlation."""
    span = trace.get_current_span()
    if span and span.is_recording():
        context = span.get_span_context()
        return format(context.trace_id, '032x')
    return None


# Trace span definitions for voice agent pipeline
class AgentSpans:
    """Standard span names for voice agent tracing."""
    
    # Session lifecycle
    SESSION_START = "agent.session.start"
    SESSION_END = "agent.session.end"
    PARTICIPANT_JOIN = "agent.participant.join"
    PARTICIPANT_LEAVE = "agent.participant.leave"
    
    # Audio pipeline
    AUDIO_FRAME_IN = "agent.audio.frame_in"
    VAD_DETECTION = "agent.audio.vad"
    
    # ASR (Speech-to-Text)
    ASR_START = "agent.asr.start"
    ASR_TRANSCRIBE = "agent.asr.transcribe"
    ASR_FINAL = "agent.asr.final"
    
    # LLM
    LLM_REQUEST = "agent.llm.request"
    LLM_CONTEXT_BUILD = "agent.llm.context_build"
    LLM_INFERENCE = "agent.llm.inference"
    LLM_RESPONSE = "agent.llm.response"
    
    # TTS (Text-to-Speech)
    TTS_REQUEST = "agent.tts.request"
    TTS_SYNTHESIS = "agent.tts.synthesis"
    TTS_STREAM = "agent.tts.stream"
    
    # LiveKit events
    LIVEKIT_TRACK_SUBSCRIBE = "agent.livekit.track_subscribe"
    LIVEKIT_TRACK_PUBLISH = "agent.livekit.track_publish"
    LIVEKIT_ROOM_CONNECT = "agent.livekit.room_connect"
    
    # Turn management
    TURN_START = "agent.turn.start"
    TURN_END = "agent.turn.end"
    TURN_INTERRUPT = "agent.turn.interrupt"


# Helper function for creating child spans
def create_child_span(
    name: str,
    parent_span: Optional[Span] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> Span:
    """Create a child span with proper context propagation."""
    tracer = _tracer or trace.get_tracer(__name__)
    
    context = trace.set_span_in_context(parent_span) if parent_span else None
    
    return tracer.start_span(
        name,
        context=context,
        attributes=attributes or {},
    )