"""
Prometheus Metrics for Agent Playground.

Provides comprehensive observability for the voice pipeline including:
- Counters for frames, transcripts, tokens, failures
- Histograms for stage latencies and e2e latency
- Gauges for active agents, queue depths, speaking state

All metrics are labeled with agent_id for multi-agent scaling support.
"""

from __future__ import annotations

import time
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Optional, Generator
from enum import Enum

logger = logging.getLogger(__name__)

# Try to import prometheus_client; provide fallback if not available
try:
    from prometheus_client import (
        Counter,
        Histogram,
        Gauge,
        CollectorRegistry,
        generate_latest,
        CONTENT_TYPE_LATEST,
        multiprocess,
        REGISTRY,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("prometheus_client not installed. Metrics disabled.")


class PipelineStage(str, Enum):
    """Stages in the voice pipeline for latency tracking."""
    LIVEKIT_RECV = "livekit_recv"
    NORMALIZE = "normalize"
    VAD = "vad"
    ASR = "asr"
    LLM = "llm"
    TTS = "tts"
    LIVEKIT_SEND = "livekit_send"


class DropReason(str, Enum):
    """Reasons for dropping frames."""
    BACKPRESSURE = "backpressure"
    INTERRUPTION = "interruption"
    CANCELLATION = "cancellation"
    ERROR = "error"


class LLMFailureReason(str, Enum):
    """Reasons for LLM failures."""
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMIT = "rate_limit"
    INVALID_RESPONSE = "invalid_response"
    CANCELLED = "cancelled"
    OTHER = "other"


@dataclass
class UtteranceTracker:
    """Tracks timing for a single utterance through the pipeline."""
    utterance_id: str
    start_time: float = field(default_factory=time.time)
    first_asr_time: Optional[float] = None
    first_llm_time: Optional[float] = None
    first_tts_time: Optional[float] = None
    first_audio_out_time: Optional[float] = None
    completed: bool = False


class MetricsCollector:
    """
    Centralized metrics collection for the voice pipeline.

    Thread-safe and supports multi-agent scenarios via agent_id labels.
    Provides context managers for easy stage timing.
    """

    def __init__(self, registry: Optional["CollectorRegistry"] = None):
        """
        Initialize metrics collector.

        Args:
            registry: Custom Prometheus registry (uses default if None)
        """
        self._enabled = PROMETHEUS_AVAILABLE
        self._registry = registry or (REGISTRY if PROMETHEUS_AVAILABLE else None)
        self._utterance_trackers: Dict[str, UtteranceTracker] = {}

        if not self._enabled:
            logger.warning("Metrics disabled - prometheus_client not available")
            return

        # ─────────────────────────────────────────────────────────────────────
        # Counters
        # ─────────────────────────────────────────────────────────────────────

        self.audio_frames_in_total = Counter(
            "audio_frames_in_total",
            "Total audio frames received from LiveKit",
            ["agent_id", "room"],
            registry=self._registry,
        )

        self.audio_frames_out_total = Counter(
            "audio_frames_out_total",
            "Total audio frames published to LiveKit",
            ["agent_id", "room"],
            registry=self._registry,
        )

        self.dropped_frames_total = Counter(
            "dropped_frames_total",
            "Total frames dropped",
            ["agent_id", "room", "reason"],
            registry=self._registry,
        )

        self.transcripts_partial_total = Counter(
            "transcripts_partial_total",
            "Total partial transcripts from ASR",
            ["agent_id", "room"],
            registry=self._registry,
        )

        self.transcripts_final_total = Counter(
            "transcripts_final_total",
            "Total final transcripts from ASR",
            ["agent_id", "room"],
            registry=self._registry,
        )

        self.llm_requests_total = Counter(
            "llm_requests_total",
            "Total LLM generation requests",
            ["agent_id", "backend", "model"],
            registry=self._registry,
        )

        self.llm_failures_total = Counter(
            "llm_failures_total",
            "Total LLM failures",
            ["agent_id", "backend", "reason"],
            registry=self._registry,
        )

        self.llm_tokens_total = Counter(
            "llm_tokens_total",
            "Total LLM tokens generated",
            ["agent_id", "backend", "model"],
            registry=self._registry,
        )

        self.tts_chunks_total = Counter(
            "tts_chunks_total",
            "Total TTS audio chunks generated",
            ["agent_id", "room"],
            registry=self._registry,
        )

        self.interruptions_total = Counter(
            "interruptions_total",
            "Total user interruptions detected",
            ["agent_id", "room"],
            registry=self._registry,
        )

        # ─────────────────────────────────────────────────────────────────────
        # Histograms
        # ─────────────────────────────────────────────────────────────────────

        # Latency buckets in milliseconds (10ms to 10s)
        latency_buckets = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)

        self.stage_latency_ms = Histogram(
            "stage_latency_ms",
            "Per-stage processing latency in milliseconds",
            ["agent_id", "stage"],
            buckets=latency_buckets,
            registry=self._registry,
        )

        # E2E latency: utterance start → first audio out published
        e2e_buckets = (100, 250, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000)

        self.e2e_latency_ms = Histogram(
            "e2e_latency_ms",
            "End-to-end latency from speech start to first audio output (ms)",
            ["agent_id"],
            buckets=e2e_buckets,
            registry=self._registry,
        )

        # ─────────────────────────────────────────────────────────────────────
        # Gauges
        # ─────────────────────────────────────────────────────────────────────

        self.active_agents = Gauge(
            "active_agents",
            "Number of currently active agents",
            registry=self._registry,
        )

        self.speaking_state = Gauge(
            "speaking_state",
            "Whether agent is currently speaking (0/1)",
            ["agent_id"],
            registry=self._registry,
        )

        self.queue_depth = Gauge(
            "queue_depth",
            "Current depth of processing queues",
            ["agent_id", "queue"],
            registry=self._registry,
        )

        logger.info("Prometheus metrics initialized")

    @property
    def enabled(self) -> bool:
        """Whether metrics collection is enabled."""
        return self._enabled

    # ─────────────────────────────────────────────────────────────────────────
    # Counter Methods
    # ─────────────────────────────────────────────────────────────────────────

    def inc_audio_frames_in(self, agent_id: str, room: str = "", count: int = 1) -> None:
        """Increment audio frames received counter."""
        if self._enabled:
            self.audio_frames_in_total.labels(agent_id=agent_id, room=room).inc(count)

    def inc_audio_frames_out(self, agent_id: str, room: str = "", count: int = 1) -> None:
        """Increment audio frames published counter."""
        if self._enabled:
            self.audio_frames_out_total.labels(agent_id=agent_id, room=room).inc(count)

    def inc_dropped_frames(self, agent_id: str, reason: DropReason, room: str = "", count: int = 1) -> None:
        """Increment dropped frames counter."""
        if self._enabled:
            self.dropped_frames_total.labels(agent_id=agent_id, room=room, reason=reason.value).inc(count)

    def inc_transcripts_partial(self, agent_id: str, room: str = "", count: int = 1) -> None:
        """Increment partial transcripts counter."""
        if self._enabled:
            self.transcripts_partial_total.labels(agent_id=agent_id, room=room).inc(count)

    def inc_transcripts_final(self, agent_id: str, room: str = "", count: int = 1) -> None:
        """Increment final transcripts counter."""
        if self._enabled:
            self.transcripts_final_total.labels(agent_id=agent_id, room=room).inc(count)

    def inc_llm_requests(self, agent_id: str, backend: str, model: str, count: int = 1) -> None:
        """Increment LLM requests counter."""
        if self._enabled:
            self.llm_requests_total.labels(
                agent_id=agent_id, backend=backend, model=model
            ).inc(count)

    def inc_llm_failures(self, agent_id: str, backend: str, reason: LLMFailureReason) -> None:
        """Increment LLM failures counter."""
        if self._enabled:
            self.llm_failures_total.labels(
                agent_id=agent_id, backend=backend, reason=reason.value
            ).inc()

    def inc_llm_tokens(self, agent_id: str, backend: str, model: str, count: int = 1) -> None:
        """Increment LLM tokens counter."""
        if self._enabled:
            self.llm_tokens_total.labels(
                agent_id=agent_id, backend=backend, model=model
            ).inc(count)

    def inc_tts_chunks(self, agent_id: str, room: str = "", count: int = 1) -> None:
        """Increment TTS chunks counter."""
        if self._enabled:
            self.tts_chunks_total.labels(agent_id=agent_id, room=room).inc(count)

    def inc_interruptions(self, agent_id: str, room: str = "") -> None:
        """Increment interruptions counter."""
        if self._enabled:
            self.interruptions_total.labels(agent_id=agent_id, room=room).inc()

    # ─────────────────────────────────────────────────────────────────────────
    # Histogram Methods
    # ─────────────────────────────────────────────────────────────────────────

    def observe_stage_latency(self, agent_id: str, stage: PipelineStage, latency_ms: float) -> None:
        """Record stage processing latency."""
        if self._enabled:
            self.stage_latency_ms.labels(agent_id=agent_id, stage=stage.value).observe(latency_ms)

    def observe_e2e_latency(self, agent_id: str, latency_ms: float) -> None:
        """Record end-to-end latency."""
        if self._enabled:
            self.e2e_latency_ms.labels(agent_id=agent_id).observe(latency_ms)

    @contextmanager
    def time_stage(self, agent_id: str, stage: PipelineStage) -> Generator[None, None, None]:
        """Context manager for timing a pipeline stage."""
        start = time.perf_counter()
        try:
            yield
        finally:
            latency_ms = (time.perf_counter() - start) * 1000
            self.observe_stage_latency(agent_id, stage, latency_ms)

    # ─────────────────────────────────────────────────────────────────────────
    # Gauge Methods
    # ─────────────────────────────────────────────────────────────────────────

    def set_active_agents(self, count: int) -> None:
        """Set number of active agents."""
        if self._enabled:
            self.active_agents.set(count)

    def inc_active_agents(self) -> None:
        """Increment active agents count."""
        if self._enabled:
            self.active_agents.inc()

    def dec_active_agents(self) -> None:
        """Decrement active agents count."""
        if self._enabled:
            self.active_agents.dec()

    def set_speaking_state(self, agent_id: str, is_speaking: bool) -> None:
        """Set speaking state for an agent."""
        if self._enabled:
            self.speaking_state.labels(agent_id=agent_id).set(1 if is_speaking else 0)

    def set_queue_depth(self, agent_id: str, queue_name: str, depth: int) -> None:
        """Set queue depth."""
        if self._enabled:
            self.queue_depth.labels(agent_id=agent_id, queue=queue_name).set(depth)

    # ─────────────────────────────────────────────────────────────────────────
    # Utterance Tracking (for E2E Latency)
    # ─────────────────────────────────────────────────────────────────────────

    def start_utterance(self, utterance_id: str) -> UtteranceTracker:
        """Start tracking a new utterance for e2e latency measurement."""
        tracker = UtteranceTracker(utterance_id=utterance_id)
        self._utterance_trackers[utterance_id] = tracker
        return tracker

    def get_utterance_tracker(self, utterance_id: str) -> Optional[UtteranceTracker]:
        """Get tracker for an utterance."""
        return self._utterance_trackers.get(utterance_id)

    def mark_first_asr(self, utterance_id: str) -> None:
        """Mark first ASR result time for utterance."""
        tracker = self._utterance_trackers.get(utterance_id)
        if tracker and tracker.first_asr_time is None:
            tracker.first_asr_time = time.time()

    def mark_first_llm(self, utterance_id: str) -> None:
        """Mark first LLM token time for utterance."""
        tracker = self._utterance_trackers.get(utterance_id)
        if tracker and tracker.first_llm_time is None:
            tracker.first_llm_time = time.time()

    def mark_first_tts(self, utterance_id: str) -> None:
        """Mark first TTS chunk time for utterance."""
        tracker = self._utterance_trackers.get(utterance_id)
        if tracker and tracker.first_tts_time is None:
            tracker.first_tts_time = time.time()

    def complete_utterance(self, agent_id: str, utterance_id: str) -> Optional[float]:
        """
        Mark utterance complete and record e2e latency.

        Returns the e2e latency in ms, or None if tracking failed.
        """
        tracker = self._utterance_trackers.pop(utterance_id, None)
        if tracker is None:
            return None

        tracker.first_audio_out_time = time.time()
        tracker.completed = True

        # E2E latency = first audio out - utterance start
        e2e_ms = (tracker.first_audio_out_time - tracker.start_time) * 1000
        self.observe_e2e_latency(agent_id, e2e_ms)

        logger.debug(
            f"Utterance {utterance_id} completed: e2e={e2e_ms:.0f}ms "
            f"(asr={_delta_ms(tracker.start_time, tracker.first_asr_time):.0f}ms, "
            f"llm={_delta_ms(tracker.first_asr_time, tracker.first_llm_time):.0f}ms, "
            f"tts={_delta_ms(tracker.first_llm_time, tracker.first_tts_time):.0f}ms)"
        )

        return e2e_ms

    def cancel_utterance(self, utterance_id: str) -> None:
        """Cancel tracking for an utterance (e.g., on interruption)."""
        self._utterance_trackers.pop(utterance_id, None)

    # ─────────────────────────────────────────────────────────────────────────
    # Export
    # ─────────────────────────────────────────────────────────────────────────

    def generate_metrics(self) -> bytes:
        """Generate Prometheus metrics output."""
        if not self._enabled:
            return b"# Metrics disabled - prometheus_client not installed\n"
        return generate_latest(self._registry)

    def get_content_type(self) -> str:
        """Get content type for metrics response."""
        if not self._enabled:
            return "text/plain"
        return CONTENT_TYPE_LATEST


def _delta_ms(start: Optional[float], end: Optional[float]) -> float:
    """Calculate delta in ms, returning 0 if either is None."""
    if start is None or end is None:
        return 0.0
    return (end - start) * 1000


# Global metrics instance (can be overridden for testing)
_metrics: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector instance."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics


def set_metrics(metrics: MetricsCollector) -> None:
    """Set the global metrics collector instance."""
    global _metrics
    _metrics = metrics
