"""
Pipeline Scheduler with Backpressure and Metrics.

Provides centralized coordination of the ASR → LLM → TTS pipeline stages,
with bounded queues, configurable backpressure policies, and latency tracking.

Design Goals:
- Prevent memory growth from unbounded queues
- Ensure low-latency streaming by dropping stale frames when overloaded
- Track per-stage latency for debugging and optimization
- Support graceful shutdown and resource cleanup
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, TypeVar, Generic
from collections import deque

logger = logging.getLogger(__name__)

T = TypeVar('T')


class BackpressureMode(Enum):
    """How to handle queue overflow."""
    DROP_OLDEST = "drop_oldest"      # Drop oldest items (prefer freshness)
    DROP_NEWEST = "drop_newest"      # Drop incoming items (preserve history)
    BLOCK_WITH_TIMEOUT = "block"     # Block put() with timeout


@dataclass
class BackpressurePolicy:
    """
    Configuration for backpressure handling.

    These defaults are tuned for real-time voice:
    - Small queues to minimize latency
    - Drop oldest to prefer fresh audio
    - Log when dropping to surface issues
    """

    mode: BackpressureMode = BackpressureMode.DROP_OLDEST

    # Maximum items in queue before backpressure kicks in
    max_queue_size: int = 50

    # For BLOCK_WITH_TIMEOUT mode
    block_timeout_ms: float = 100.0

    # Logging configuration
    log_drops: bool = True
    log_every_n_drops: int = 10  # Log every Nth drop to avoid spam

    # Metrics callback
    on_drop: Optional[Callable[[str, int], None]] = None


@dataclass
class StageMetrics:
    """Metrics for a single pipeline stage."""

    stage_name: str
    items_processed: int = 0
    items_dropped: int = 0
    total_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    min_latency_ms: float = float('inf')
    last_process_time: float = 0.0

    # Rolling window for recent latency
    _recent_latencies: deque = field(default_factory=lambda: deque(maxlen=100))

    @property
    def avg_latency_ms(self) -> float:
        """Average latency over processed items."""
        if self.items_processed == 0:
            return 0.0
        return self.total_latency_ms / self.items_processed

    @property
    def recent_avg_latency_ms(self) -> float:
        """Average latency over recent items."""
        if not self._recent_latencies:
            return 0.0
        return sum(self._recent_latencies) / len(self._recent_latencies)

    def record_latency(self, latency_ms: float) -> None:
        """Record a latency measurement."""
        self.items_processed += 1
        self.total_latency_ms += latency_ms
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)
        self.min_latency_ms = min(self.min_latency_ms, latency_ms)
        self.last_process_time = time.time()
        self._recent_latencies.append(latency_ms)

    def record_drop(self) -> None:
        """Record a dropped item."""
        self.items_dropped += 1

    def to_dict(self) -> dict[str, Any]:
        """Export metrics as dictionary."""
        return {
            "stage": self.stage_name,
            "processed": self.items_processed,
            "dropped": self.items_dropped,
            "drop_rate": (
                self.items_dropped / (self.items_processed + self.items_dropped)
                if (self.items_processed + self.items_dropped) > 0 else 0.0
            ),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "recent_avg_latency_ms": round(self.recent_avg_latency_ms, 2),
            "max_latency_ms": round(self.max_latency_ms, 2),
            "min_latency_ms": round(self.min_latency_ms, 2) if self.min_latency_ms != float('inf') else 0.0,
        }


class BoundedQueue(Generic[T]):
    """
    Async queue with configurable backpressure policy.

    Unlike asyncio.Queue, this provides explicit backpressure handling
    with metrics and logging.
    """

    def __init__(
        self,
        name: str,
        policy: Optional[BackpressurePolicy] = None,
    ):
        self.name = name
        self.policy = policy or BackpressurePolicy()
        self._queue: deque[tuple[T, float]] = deque()  # (item, timestamp)
        self._event = asyncio.Event()
        self._closed = False
        self._metrics = StageMetrics(stage_name=name)
        self._consecutive_drops = 0

    @property
    def size(self) -> int:
        """Current queue size."""
        return len(self._queue)

    @property
    def is_full(self) -> bool:
        """Whether queue is at capacity."""
        return len(self._queue) >= self.policy.max_queue_size

    @property
    def metrics(self) -> StageMetrics:
        """Get queue metrics."""
        return self._metrics

    async def put(self, item: T) -> bool:
        """
        Add item to queue with backpressure handling.

        Returns:
            True if item was added, False if dropped
        """
        if self._closed:
            return False

        timestamp = time.time()

        if self.is_full:
            if self.policy.mode == BackpressureMode.DROP_OLDEST:
                # Remove oldest item
                dropped = self._queue.popleft()
                self._record_drop()

                # Add new item
                self._queue.append((item, timestamp))
                self._event.set()
                return True

            elif self.policy.mode == BackpressureMode.DROP_NEWEST:
                # Don't add new item
                self._record_drop()
                return False

            elif self.policy.mode == BackpressureMode.BLOCK_WITH_TIMEOUT:
                # Wait for space with timeout
                try:
                    await asyncio.wait_for(
                        self._wait_for_space(),
                        timeout=self.policy.block_timeout_ms / 1000
                    )
                except asyncio.TimeoutError:
                    self._record_drop()
                    return False

        self._queue.append((item, timestamp))
        self._event.set()
        self._consecutive_drops = 0
        return True

    def put_nowait(self, item: T) -> bool:
        """Non-blocking put."""
        if self._closed:
            return False

        timestamp = time.time()

        if self.is_full:
            if self.policy.mode == BackpressureMode.DROP_OLDEST:
                self._queue.popleft()
                self._record_drop()
            else:
                self._record_drop()
                return False

        self._queue.append((item, timestamp))
        self._event.set()
        self._consecutive_drops = 0
        return True

    async def get(self) -> Optional[T]:
        """
        Get item from queue, waiting if empty.

        Returns None if queue is closed.
        """
        while True:
            if self._closed and not self._queue:
                return None

            if self._queue:
                item, timestamp = self._queue.popleft()
                latency_ms = (time.time() - timestamp) * 1000
                self._metrics.record_latency(latency_ms)

                if not self._queue:
                    self._event.clear()

                return item

            if self._closed:
                return None

            await self._event.wait()

    def get_nowait(self) -> Optional[T]:
        """Non-blocking get."""
        if self._queue:
            item, timestamp = self._queue.popleft()
            latency_ms = (time.time() - timestamp) * 1000
            self._metrics.record_latency(latency_ms)

            if not self._queue:
                self._event.clear()

            return item
        return None

    async def _wait_for_space(self) -> None:
        """Wait until there's space in the queue."""
        while self.is_full and not self._closed:
            await asyncio.sleep(0.001)  # 1ms polling

    def _record_drop(self) -> None:
        """Record a dropped item with logging."""
        self._metrics.record_drop()
        self._consecutive_drops += 1

        if self.policy.log_drops:
            if self._consecutive_drops % self.policy.log_every_n_drops == 1:
                logger.warning(
                    f"Queue '{self.name}' backpressure: dropped {self._consecutive_drops} items "
                    f"(mode: {self.policy.mode.value}, size: {self.size}/{self.policy.max_queue_size})"
                )

        if self.policy.on_drop:
            self.policy.on_drop(self.name, self._consecutive_drops)

    def clear(self) -> int:
        """Clear queue, return number of items cleared."""
        count = len(self._queue)
        self._queue.clear()
        self._event.clear()
        return count

    def close(self) -> None:
        """Close queue, wake up any waiters."""
        self._closed = True
        self._event.set()

    def __len__(self) -> int:
        return len(self._queue)


@dataclass
class PipelineSchedulerConfig:
    """Configuration for the pipeline scheduler."""

    # Per-stage queue configurations
    asr_queue_size: int = 100      # ~2 seconds of audio at 20ms frames
    llm_queue_size: int = 10       # Transcripts waiting for LLM
    tts_queue_size: int = 50       # Text chunks waiting for TTS
    output_queue_size: int = 100   # Audio frames waiting to publish

    # Backpressure mode (applied to all queues)
    backpressure_mode: BackpressureMode = BackpressureMode.DROP_OLDEST

    # Metrics reporting interval
    metrics_interval_seconds: float = 10.0

    # Enable detailed per-item tracing
    enable_tracing: bool = False


class PipelineScheduler:
    """
    Coordinates data flow through the voice pipeline with backpressure.

    Provides bounded queues between stages:
    - audio_in: Raw audio frames from room adapter
    - transcripts: Final transcripts to LLM
    - tts_input: Text chunks to TTS
    - audio_out: Synthesized audio to room adapter

    Each queue has metrics for latency and drop rate monitoring.
    """

    def __init__(self, config: Optional[PipelineSchedulerConfig] = None):
        self.config = config or PipelineSchedulerConfig()
        self._running = False
        self._metrics_task: Optional[asyncio.Task] = None

        # Create backpressure policy
        policy = BackpressurePolicy(
            mode=self.config.backpressure_mode,
            max_queue_size=self.config.asr_queue_size,  # Will be overridden per-queue
        )

        # Create stage queues
        self.audio_in: BoundedQueue[bytes] = BoundedQueue(
            "audio_in",
            BackpressurePolicy(
                mode=self.config.backpressure_mode,
                max_queue_size=self.config.asr_queue_size,
            )
        )

        self.transcripts: BoundedQueue[str] = BoundedQueue(
            "transcripts",
            BackpressurePolicy(
                mode=self.config.backpressure_mode,
                max_queue_size=self.config.llm_queue_size,
            )
        )

        self.tts_input: BoundedQueue[str] = BoundedQueue(
            "tts_input",
            BackpressurePolicy(
                mode=self.config.backpressure_mode,
                max_queue_size=self.config.tts_queue_size,
            )
        )

        self.audio_out: BoundedQueue[bytes] = BoundedQueue(
            "audio_out",
            BackpressurePolicy(
                mode=self.config.backpressure_mode,
                max_queue_size=self.config.output_queue_size,
            )
        )

        # All queues for iteration
        self._queues = [
            self.audio_in,
            self.transcripts,
            self.tts_input,
            self.audio_out,
        ]

    async def start(self) -> None:
        """Start the scheduler and metrics reporting."""
        if self._running:
            return

        self._running = True

        # Start periodic metrics logging
        if self.config.metrics_interval_seconds > 0:
            self._metrics_task = asyncio.create_task(self._report_metrics())

        logger.info("Pipeline scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler and close all queues."""
        self._running = False

        # Cancel metrics task
        if self._metrics_task:
            self._metrics_task.cancel()
            try:
                await self._metrics_task
            except asyncio.CancelledError:
                pass

        # Close all queues
        for queue in self._queues:
            queue.close()

        logger.info("Pipeline scheduler stopped")

    async def _report_metrics(self) -> None:
        """Periodically log pipeline metrics."""
        while self._running:
            await asyncio.sleep(self.config.metrics_interval_seconds)

            if not self._running:
                break

            metrics = self.get_metrics()
            total_drops = sum(m.get("dropped", 0) for m in metrics.values())

            if total_drops > 0:
                logger.info(f"Pipeline metrics: {metrics}")

    def get_metrics(self) -> dict[str, dict[str, Any]]:
        """Get metrics for all stages."""
        return {
            queue.name: queue.metrics.to_dict()
            for queue in self._queues
        }

    def clear_all(self) -> dict[str, int]:
        """Clear all queues, return counts of cleared items."""
        return {
            queue.name: queue.clear()
            for queue in self._queues
        }

    @property
    def total_queued_items(self) -> int:
        """Total items across all queues."""
        return sum(len(q) for q in self._queues)


class LatencyTracker:
    """
    Tracks end-to-end latency through the pipeline.

    Provides detailed breakdown of where time is spent:
    - ASR latency (audio to transcript)
    - LLM latency (transcript to first token, and total generation)
    - TTS latency (text to first audio chunk)
    - Total latency (user speech to agent audio)
    """

    def __init__(self, name: str = "default"):
        self.name = name
        self._spans: dict[str, dict[str, float]] = {}
        self._completed_spans: deque[dict] = deque(maxlen=1000)

    def start_span(self, span_id: str, stage: str) -> None:
        """Start tracking a span."""
        if span_id not in self._spans:
            self._spans[span_id] = {"start": time.time()}
        self._spans[span_id][f"{stage}_start"] = time.time()

    def end_span(self, span_id: str, stage: str) -> Optional[float]:
        """End tracking a span, return duration in ms."""
        if span_id not in self._spans:
            return None

        end_time = time.time()
        self._spans[span_id][f"{stage}_end"] = end_time

        start_key = f"{stage}_start"
        if start_key in self._spans[span_id]:
            return (end_time - self._spans[span_id][start_key]) * 1000
        return None

    def complete_span(self, span_id: str) -> Optional[dict]:
        """Mark span complete, move to completed list, return timing data."""
        if span_id not in self._spans:
            return None

        span = self._spans.pop(span_id)
        span["completed"] = time.time()
        span["total_ms"] = (span["completed"] - span["start"]) * 1000

        # Calculate per-stage durations
        stages = ["asr", "llm", "tts", "output"]
        for stage in stages:
            start_key = f"{stage}_start"
            end_key = f"{stage}_end"
            if start_key in span and end_key in span:
                span[f"{stage}_ms"] = (span[end_key] - span[start_key]) * 1000

        self._completed_spans.append(span)
        return span

    def get_summary(self) -> dict[str, Any]:
        """Get summary statistics."""
        if not self._completed_spans:
            return {"spans": 0}

        total_latencies = [s["total_ms"] for s in self._completed_spans]

        summary = {
            "spans": len(self._completed_spans),
            "avg_total_ms": sum(total_latencies) / len(total_latencies),
            "max_total_ms": max(total_latencies),
            "min_total_ms": min(total_latencies),
        }

        # Per-stage averages
        for stage in ["asr", "llm", "tts", "output"]:
            key = f"{stage}_ms"
            values = [s[key] for s in self._completed_spans if key in s]
            if values:
                summary[f"avg_{key}"] = sum(values) / len(values)

        return summary
