"""
Metrics collection for Agent Playground using OpenTelemetry.
"""

import os
import time
from typing import Dict, Any, Optional
from contextlib import contextmanager

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource, SERVICE_NAME

# Global meter instance
_meter: Optional[metrics.Meter] = None


def init_metrics(
    service_name: str = "agent-playground",
    otlp_endpoint: str = None,
    export_interval_ms: int = 10000,
) -> None:
    """Initialize OpenTelemetry metrics collection."""
    global _meter
    
    resource = Resource.create({
        SERVICE_NAME: service_name,
        "deployment.environment": os.getenv("ENVIRONMENT", "local"),
    })
    
    # Configure OTLP exporter
    otlp_endpoint = otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")
    exporter = OTLPMetricExporter(
        endpoint=otlp_endpoint,
        insecure=True,
    )
    
    # Create metric reader
    reader = PeriodicExportingMetricReader(
        exporter=exporter,
        export_interval_millis=export_interval_ms,
    )
    
    # Set up meter provider
    provider = MeterProvider(
        resource=resource,
        metric_readers=[reader],
    )
    metrics.set_meter_provider(provider)
    
    _meter = metrics.get_meter(service_name)


def create_meter(name: str) -> metrics.Meter:
    """Create a named meter for a specific component."""
    if _meter is None:
        init_metrics()
    return metrics.get_meter(name)


@contextmanager
def record_latency(histogram_name: str, attributes: Optional[Dict[str, Any]] = None):
    """
    Context manager to record operation latency.
    
    Example:
        with record_latency("asr.transcription", {"model": "whisper"}):
            result = transcribe_audio(audio)
    """
    meter = _meter or metrics.get_meter(__name__)
    histogram = meter.create_histogram(
        name=histogram_name,
        description=f"Latency of {histogram_name} operations",
        unit="ms",
    )
    
    start_time = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000
        histogram.record(duration_ms, attributes or {})


def increment_counter(
    counter_name: str,
    value: int = 1,
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    """Increment a counter metric."""
    meter = _meter or metrics.get_meter(__name__)
    counter = meter.create_counter(
        name=counter_name,
        description=f"Count of {counter_name} events",
    )
    counter.add(value, attributes or {})


def record_histogram(
    histogram_name: str,
    value: float,
    attributes: Optional[Dict[str, Any]] = None,
    unit: str = "1",
) -> None:
    """Record a value in a histogram."""
    meter = _meter or metrics.get_meter(__name__)
    histogram = meter.create_histogram(
        name=histogram_name,
        description=f"Distribution of {histogram_name}",
        unit=unit,
    )
    histogram.record(value, attributes or {})


# Standard metrics for voice agents
class AgentMetrics:
    """Standard metric names for voice agent monitoring."""
    
    # Latency metrics (histograms)
    VAD_LATENCY = "agent.vad.latency"
    ASR_LATENCY = "agent.asr.latency"
    LLM_LATENCY = "agent.llm.latency"
    TTS_LATENCY = "agent.tts.latency"
    E2E_LATENCY = "agent.e2e.latency"
    
    # Throughput metrics (counters)
    AUDIO_FRAMES_IN = "agent.audio.frames.in"
    AUDIO_FRAMES_OUT = "agent.audio.frames.out"
    TRANSCRIPTS_COMPLETED = "agent.transcripts.completed"
    LLM_TOKENS_IN = "agent.llm.tokens.in"
    LLM_TOKENS_OUT = "agent.llm.tokens.out"
    
    # Error metrics (counters)
    ASR_ERRORS = "agent.asr.errors"
    LLM_ERRORS = "agent.llm.errors"
    TTS_ERRORS = "agent.tts.errors"
    
    # Queue metrics (gauges/histograms)
    AUDIO_QUEUE_SIZE = "agent.queue.audio.size"
    LLM_QUEUE_SIZE = "agent.queue.llm.size"
    
    # Session metrics
    ACTIVE_SESSIONS = "agent.sessions.active"
    SESSION_DURATION = "agent.sessions.duration"
    TURNS_PER_SESSION = "agent.sessions.turns"