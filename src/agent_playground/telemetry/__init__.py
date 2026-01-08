"""
OpenTelemetry instrumentation for Agent Playground.
Provides decorators and context managers for distributed tracing.
"""

from .tracing import (
    init_telemetry,
    create_tracer,
    trace_span,
    trace_async_span,
    add_span_attributes,
    record_error,
    get_current_trace_id,
)
from .metrics import (
    create_meter,
    record_latency,
    increment_counter,
    record_histogram,
)

__all__ = [
    "init_telemetry",
    "create_tracer",
    "trace_span",
    "trace_async_span",
    "add_span_attributes",
    "record_error",
    "get_current_trace_id",
    "create_meter",
    "record_latency",
    "increment_counter",
    "record_histogram",
]