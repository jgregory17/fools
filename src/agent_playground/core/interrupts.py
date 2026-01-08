"""
Interrupt Controller for Barge-in Handling.

Coordinates VAD, behavior policies, and TTS cancellation to provide
responsive interruption handling when users speak during agent output.

Design Goals:
- Fast response to user interruptions (<100ms)
- Avoid false positives from background noise
- Graceful TTS cancellation without audio artifacts
- State tracking for analytics and debugging
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional, Any, Awaitable

from .policies import BehaviorPolicy, InterruptionConfig
from .events import InterruptionDetected, VADEvent, AgentStateChange, AgentState

logger = logging.getLogger(__name__)


class InterruptState(Enum):
    """Current state of the interrupt controller."""
    IDLE = auto()               # Agent not speaking, no interrupt possible
    MONITORING = auto()         # Agent speaking, monitoring for interrupts
    INTERRUPT_DETECTED = auto() # Interrupt detected, cancelling TTS
    COOLDOWN = auto()           # Brief cooldown after interrupt


@dataclass
class InterruptControllerConfig:
    """Configuration for the interrupt controller."""

    # Enable/disable interruption handling
    enabled: bool = True

    # VAD threshold for triggering interrupt detection
    vad_threshold: float = 0.5

    # Minimum speech duration before triggering interrupt (ms)
    min_speech_duration_ms: float = 200.0

    # Minimum consecutive VAD frames to confirm speech
    min_consecutive_frames: int = 3

    # Grace period after agent starts speaking (ms)
    # Prevents immediate re-interruption
    grace_period_ms: float = 500.0

    # Cooldown after interrupt before accepting new interrupts (ms)
    cooldown_ms: float = 300.0

    # Whether to track partial audio played before interrupt
    track_played_audio: bool = True


@dataclass
class InterruptEvent:
    """Details about an interruption event."""
    timestamp: float = field(default_factory=time.time)
    played_duration_ms: float = 0.0
    vad_confidence: float = 0.0
    speech_duration_ms: float = 0.0
    agent_state: str = ""


class InterruptController:
    """
    Coordinates interruption (barge-in) handling.

    Monitors VAD output during agent speech and triggers TTS
    cancellation when user speech is detected.

    Usage:
        controller = InterruptController(
            vad=my_vad,
            on_interrupt=handle_interrupt,
        )

        # When agent starts speaking
        controller.on_speech_start()

        # Process VAD for each audio frame
        vad_result = vad.process(audio_frame)
        if controller.process_vad(vad_result):
            # Interrupt detected!
            await cancel_tts()

        # When agent stops speaking
        controller.on_speech_end()
    """

    def __init__(
        self,
        config: Optional[InterruptControllerConfig] = None,
        policy: Optional[BehaviorPolicy] = None,
        on_interrupt: Optional[Callable[[InterruptEvent], Awaitable[None]]] = None,
    ):
        self.config = config or InterruptControllerConfig()
        self._policy = policy
        self._on_interrupt = on_interrupt

        # State
        self._state = InterruptState.IDLE
        self._speech_start_time: Optional[float] = None
        self._user_speech_start_time: Optional[float] = None
        self._consecutive_speech_frames = 0
        self._cooldown_until: float = 0.0

        # Audio tracking
        self._audio_played_ms: float = 0.0

        # Metrics
        self._interrupts_triggered = 0
        self._false_positives_avoided = 0
        self._history: list[InterruptEvent] = []

    @property
    def state(self) -> InterruptState:
        """Current controller state."""
        return self._state

    @property
    def is_monitoring(self) -> bool:
        """Whether actively monitoring for interrupts."""
        return self._state == InterruptState.MONITORING

    def on_speech_start(self) -> None:
        """Called when agent starts speaking."""
        self._state = InterruptState.MONITORING
        self._speech_start_time = time.time()
        self._user_speech_start_time = None
        self._consecutive_speech_frames = 0
        self._audio_played_ms = 0.0

        logger.debug("InterruptController: monitoring started")

    def on_speech_end(self) -> None:
        """Called when agent stops speaking (naturally or via interrupt)."""
        self._state = InterruptState.IDLE
        self._speech_start_time = None

        logger.debug("InterruptController: monitoring stopped")

    def on_audio_played(self, duration_ms: float) -> None:
        """Track audio played for interrupt analytics."""
        if self.config.track_played_audio:
            self._audio_played_ms += duration_ms

    def process_vad(self, vad_probability: float) -> bool:
        """
        Process VAD result and check for interrupt.

        Args:
            vad_probability: Speech probability from VAD (0.0-1.0)

        Returns:
            True if interrupt should be triggered
        """
        if not self.config.enabled:
            return False

        if self._state != InterruptState.MONITORING:
            return False

        # Check cooldown
        if time.time() < self._cooldown_until:
            return False

        # Check grace period
        if self._speech_start_time:
            elapsed_ms = (time.time() - self._speech_start_time) * 1000
            if elapsed_ms < self.config.grace_period_ms:
                return False

        # Check VAD threshold
        if vad_probability < self.config.vad_threshold:
            # Reset consecutive count on silence
            if self._consecutive_speech_frames > 0:
                self._consecutive_speech_frames = 0
                self._user_speech_start_time = None
            return False

        # Track consecutive speech frames
        self._consecutive_speech_frames += 1

        if self._user_speech_start_time is None:
            self._user_speech_start_time = time.time()

        # Check minimum consecutive frames
        if self._consecutive_speech_frames < self.config.min_consecutive_frames:
            return False

        # Check minimum speech duration
        speech_duration_ms = (time.time() - self._user_speech_start_time) * 1000
        if speech_duration_ms < self.config.min_speech_duration_ms:
            return False

        # Interrupt confirmed!
        self._trigger_interrupt(vad_probability, speech_duration_ms)
        return True

    def _trigger_interrupt(self, vad_confidence: float, speech_duration_ms: float) -> None:
        """Handle confirmed interrupt."""
        self._state = InterruptState.INTERRUPT_DETECTED
        self._interrupts_triggered += 1
        self._cooldown_until = time.time() + (self.config.cooldown_ms / 1000)

        # Create event
        event = InterruptEvent(
            played_duration_ms=self._audio_played_ms,
            vad_confidence=vad_confidence,
            speech_duration_ms=speech_duration_ms,
            agent_state="speaking",
        )
        self._history.append(event)

        logger.info(
            f"Interrupt triggered: played {self._audio_played_ms:.0f}ms, "
            f"VAD confidence {vad_confidence:.2f}"
        )

        # Notify policy
        if self._policy:
            self._policy.on_speech_interrupted()

        # Trigger callback
        if self._on_interrupt:
            asyncio.create_task(self._on_interrupt(event))

    def reset(self) -> None:
        """Reset controller state."""
        self._state = InterruptState.IDLE
        self._speech_start_time = None
        self._user_speech_start_time = None
        self._consecutive_speech_frames = 0
        self._cooldown_until = 0.0
        self._audio_played_ms = 0.0

    @property
    def metrics(self) -> dict[str, Any]:
        """Get controller metrics."""
        return {
            "state": self._state.name,
            "interrupts_triggered": self._interrupts_triggered,
            "false_positives_avoided": self._false_positives_avoided,
            "recent_interrupts": len(self._history),
            "avg_played_before_interrupt_ms": (
                sum(e.played_duration_ms for e in self._history) / len(self._history)
                if self._history else 0.0
            ),
        }


class InterruptCoordinator:
    """
    High-level coordinator that integrates VAD, policies, and TTS control.

    Provides a simple interface for the pipeline to handle interruptions.

    Usage:
        coordinator = InterruptCoordinator(vad=my_vad)

        # In the pipeline:
        async with coordinator.speaking_session() as session:
            async for chunk in tts.synthesize(text):
                if session.should_stop:
                    break
                session.on_audio_chunk(chunk)
                yield chunk

        # VAD processing runs in background via event bus
    """

    def __init__(
        self,
        vad: Any,  # VADBackend
        config: Optional[InterruptControllerConfig] = None,
        policy: Optional[BehaviorPolicy] = None,
    ):
        self._vad = vad
        self._controller = InterruptController(
            config=config,
            policy=policy,
            on_interrupt=self._handle_interrupt,
        )
        self._cancel_event = asyncio.Event()
        self._active_session: Optional[SpeakingSession] = None

    async def _handle_interrupt(self, event: InterruptEvent) -> None:
        """Handle interrupt by signaling cancellation."""
        self._cancel_event.set()
        if self._active_session:
            self._active_session._interrupted = True

    def speaking_session(self) -> SpeakingSession:
        """Create a new speaking session."""
        self._cancel_event.clear()
        session = SpeakingSession(
            controller=self._controller,
            cancel_event=self._cancel_event,
        )
        self._active_session = session
        return session

    def process_audio(self, audio_data: bytes) -> bool:
        """
        Process audio through VAD and check for interrupt.

        Call this for each incoming audio frame during agent speech.

        Returns:
            True if interrupt was triggered
        """
        if self._vad is None:
            return False

        result = self._vad.process(audio_data)
        return self._controller.process_vad(result.probability)


class SpeakingSession:
    """
    Context manager for a speaking session with interrupt support.

    Usage:
        async with coordinator.speaking_session() as session:
            for chunk in audio_chunks:
                if session.should_stop:
                    break
                session.on_audio_chunk(chunk)
                yield chunk
    """

    def __init__(
        self,
        controller: InterruptController,
        cancel_event: asyncio.Event,
    ):
        self._controller = controller
        self._cancel_event = cancel_event
        self._interrupted = False
        self._chunks_sent = 0
        self._audio_duration_ms = 0.0

    @property
    def should_stop(self) -> bool:
        """Whether to stop sending audio (interrupted or cancelled)."""
        return self._cancel_event.is_set() or self._interrupted

    @property
    def was_interrupted(self) -> bool:
        """Whether session was interrupted by user."""
        return self._interrupted

    def on_audio_chunk(self, chunk_duration_ms: float) -> None:
        """Track audio chunk sent."""
        self._chunks_sent += 1
        self._audio_duration_ms += chunk_duration_ms
        self._controller.on_audio_played(chunk_duration_ms)

    async def __aenter__(self) -> SpeakingSession:
        self._controller.on_speech_start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        self._controller.on_speech_end()
        return False
