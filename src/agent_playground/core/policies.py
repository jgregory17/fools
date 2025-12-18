"""
Behavior Policies for Agent Pipeline.

Provides configurable policies for:
- Echo/feedback prevention (suppress ASR during agent speech)
- Interruption handling (barge-in detection)
- Turn-taking behavior
- Rate limiting and throttling

These policies prevent common real-time voice issues by default.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional, Any

logger = logging.getLogger(__name__)


class SpeakingState(Enum):
    """Agent's current speaking state."""
    IDLE = auto()           # Not speaking
    PREPARING = auto()      # Generating/buffering
    SPEAKING = auto()       # Actively outputting audio
    INTERRUPTED = auto()    # Was speaking, got interrupted


@dataclass
class EchoSuppressionConfig:
    """
    Configuration for echo/feedback prevention.

    Echo occurs when the agent hears its own TTS output through
    the user's microphone. This config controls how to prevent it.
    """

    # Master enable/disable
    enabled: bool = True

    # Suppress ASR while agent is speaking (default ON)
    # This is the primary echo prevention mechanism
    suppress_asr_during_speech: bool = True

    # Additional safety margin after speech ends (ms)
    # Accounts for audio propagation delay
    post_speech_suppression_ms: float = 200.0

    # Use AEC (Acoustic Echo Cancellation) if available
    # This allows ASR to run during speech for faster interruption detection
    enable_aec: bool = False

    # Volume threshold below which ASR is suppressed
    # Helps filter out low-level echo residue
    min_speech_energy: float = 0.01


@dataclass
class InterruptionConfig:
    """
    Configuration for barge-in/interruption handling.

    Determines when and how the agent should stop speaking
    when the user starts talking.
    """

    # Allow user to interrupt agent's speech
    allow_interruptions: bool = True

    # VAD confidence threshold to trigger interruption (0.0-1.0)
    # Higher = more confident speech needed to interrupt
    vad_threshold: float = 0.5

    # Minimum duration of detected speech before interrupting (ms)
    # Prevents brief sounds from interrupting
    min_interruption_duration_ms: float = 200.0

    # How many consecutive speech frames needed
    min_consecutive_frames: int = 3

    # Grace period at start of agent speech (ms)
    # Prevents immediate re-interruption after agent starts
    grace_period_ms: float = 500.0

    # Whether to immediately cancel TTS or fade out
    immediate_cancel: bool = True

    # Callback when interruption occurs
    on_interrupt: Optional[Callable[[float], None]] = None


@dataclass
class TurnTakingConfig:
    """
    Configuration for turn-taking behavior.

    Controls when the agent considers a user turn complete
    and when to start/stop speaking.
    """

    # End-of-utterance confidence threshold (0.0-1.0)
    eou_threshold: float = 0.7

    # Silence duration to trigger end-of-turn (ms)
    silence_timeout_ms: float = 1000.0

    # Minimum speech duration to consider a valid turn (ms)
    min_turn_duration_ms: float = 100.0

    # Maximum turn duration before forcing completion (ms)
    max_turn_duration_ms: float = 30000.0

    # Delay before agent starts responding (ms)
    # Small delay can feel more natural
    response_delay_ms: float = 0.0


class BehaviorPolicy:
    """
    Coordinates agent behavior policies.

    This class manages the interaction between different policies:
    - Echo suppression
    - Interruption handling
    - Turn-taking

    It provides the central point for querying whether ASR should
    be active, whether to interrupt TTS, etc.

    Usage:
        policy = BehaviorPolicy()

        # In ASR loop:
        if policy.should_process_audio():
            process_audio(frame)

        # When TTS starts:
        policy.on_speech_start()

        # When TTS ends:
        policy.on_speech_end()

        # When user speech detected during agent speech:
        if policy.should_interrupt(vad_confidence):
            cancel_tts()
    """

    def __init__(
        self,
        echo_config: Optional[EchoSuppressionConfig] = None,
        interruption_config: Optional[InterruptionConfig] = None,
        turn_config: Optional[TurnTakingConfig] = None,
    ):
        self.echo_config = echo_config or EchoSuppressionConfig()
        self.interruption_config = interruption_config or InterruptionConfig()
        self.turn_config = turn_config or TurnTakingConfig()

        # State
        self._speaking_state = SpeakingState.IDLE
        self._speech_start_time: Optional[float] = None
        self._speech_end_time: Optional[float] = None
        self._consecutive_speech_frames = 0
        self._user_speech_start_time: Optional[float] = None

        # Metrics
        self._interruptions_count = 0
        self._suppressed_frames_count = 0

    @property
    def speaking_state(self) -> SpeakingState:
        """Current speaking state."""
        return self._speaking_state

    @property
    def is_speaking(self) -> bool:
        """Whether agent is currently speaking."""
        return self._speaking_state == SpeakingState.SPEAKING

    def on_speech_start(self) -> None:
        """Called when agent starts speaking."""
        self._speaking_state = SpeakingState.SPEAKING
        self._speech_start_time = time.time()
        self._speech_end_time = None
        self._consecutive_speech_frames = 0
        logger.debug("Agent speech started, ASR suppression active")

    def on_speech_end(self) -> None:
        """Called when agent stops speaking."""
        self._speaking_state = SpeakingState.IDLE
        self._speech_end_time = time.time()
        logger.debug("Agent speech ended")

    def on_speech_interrupted(self) -> None:
        """Called when agent speech is interrupted."""
        prev_state = self._speaking_state
        self._speaking_state = SpeakingState.INTERRUPTED
        self._speech_end_time = time.time()
        self._interruptions_count += 1

        played_duration_ms = 0.0
        if self._speech_start_time:
            played_duration_ms = (time.time() - self._speech_start_time) * 1000

        logger.info(f"Agent speech interrupted after {played_duration_ms:.0f}ms")

        if self.interruption_config.on_interrupt:
            self.interruption_config.on_interrupt(played_duration_ms)

    def should_process_audio(self, audio_energy: float = 1.0) -> bool:
        """
        Whether ASR should process the current audio frame.

        Returns False when audio should be suppressed to prevent echo.
        """
        if not self.echo_config.enabled:
            return True

        # Suppress if audio is too quiet (likely echo residue)
        if audio_energy < self.echo_config.min_speech_energy:
            return False

        # Suppress during agent speech
        if self.echo_config.suppress_asr_during_speech:
            if self._speaking_state == SpeakingState.SPEAKING:
                self._suppressed_frames_count += 1
                return False

            # Post-speech suppression
            if self._speech_end_time:
                elapsed_ms = (time.time() - self._speech_end_time) * 1000
                if elapsed_ms < self.echo_config.post_speech_suppression_ms:
                    self._suppressed_frames_count += 1
                    return False

        return True

    def should_interrupt(self, vad_confidence: float) -> bool:
        """
        Whether to interrupt agent speech based on user VAD.

        Args:
            vad_confidence: VAD confidence score (0.0-1.0)

        Returns:
            True if agent should stop speaking
        """
        if not self.interruption_config.allow_interruptions:
            return False

        if self._speaking_state != SpeakingState.SPEAKING:
            return False

        # Check grace period
        if self._speech_start_time:
            elapsed_ms = (time.time() - self._speech_start_time) * 1000
            if elapsed_ms < self.interruption_config.grace_period_ms:
                return False

        # Check VAD threshold
        if vad_confidence < self.interruption_config.vad_threshold:
            self._consecutive_speech_frames = 0
            self._user_speech_start_time = None
            return False

        # Track consecutive speech frames
        self._consecutive_speech_frames += 1

        if self._user_speech_start_time is None:
            self._user_speech_start_time = time.time()

        # Check minimum frames
        if self._consecutive_speech_frames < self.interruption_config.min_consecutive_frames:
            return False

        # Check minimum duration
        speech_duration_ms = (time.time() - self._user_speech_start_time) * 1000
        if speech_duration_ms < self.interruption_config.min_interruption_duration_ms:
            return False

        return True

    def should_end_turn(
        self,
        silence_duration_ms: float,
        eou_confidence: float,
        turn_duration_ms: float,
    ) -> bool:
        """
        Whether to consider the user's turn complete.

        Args:
            silence_duration_ms: How long user has been silent
            eou_confidence: End-of-utterance confidence from model
            turn_duration_ms: Total duration of user's turn

        Returns:
            True if turn should be considered complete
        """
        # Force completion if turn is too long
        if turn_duration_ms >= self.turn_config.max_turn_duration_ms:
            return True

        # Check EOU confidence
        if eou_confidence >= self.turn_config.eou_threshold:
            return True

        # Check silence timeout
        if silence_duration_ms >= self.turn_config.silence_timeout_ms:
            return True

        return False

    def reset(self) -> None:
        """Reset policy state."""
        self._speaking_state = SpeakingState.IDLE
        self._speech_start_time = None
        self._speech_end_time = None
        self._consecutive_speech_frames = 0
        self._user_speech_start_time = None

    @property
    def metrics(self) -> dict[str, Any]:
        """Get policy metrics."""
        return {
            "speaking_state": self._speaking_state.name,
            "interruptions": self._interruptions_count,
            "suppressed_frames": self._suppressed_frames_count,
        }


class SpeakingGate:
    """
    Context manager for gating ASR during agent speech.

    Usage:
        async with speaking_gate.speaking():
            await tts.synthesize(text)

        # ASR is now unsuppressed
    """

    def __init__(self, policy: BehaviorPolicy):
        self._policy = policy

    class _SpeakingContext:
        def __init__(self, policy: BehaviorPolicy):
            self._policy = policy

        async def __aenter__(self):
            self._policy.on_speech_start()
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            if exc_type is not None:
                self._policy.on_speech_interrupted()
            else:
                self._policy.on_speech_end()
            return False

    def speaking(self) -> _SpeakingContext:
        """Create a context for agent speech."""
        return self._SpeakingContext(self._policy)


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    # Maximum requests per second
    max_rps: float = 10.0

    # Burst allowance
    burst_size: int = 5

    # Timeout for acquiring permit (ms)
    timeout_ms: float = 1000.0


class RateLimiter:
    """
    Token bucket rate limiter.

    Used to prevent overwhelming backends with too many requests.
    """

    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or RateLimitConfig()
        self._tokens = float(self.config.burst_size)
        self._last_refill = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """
        Acquire a permit to proceed.

        Returns True if permit acquired, False if rate limited.
        """
        async with self._lock:
            self._refill()

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True

            # Wait for token
            wait_time = (1.0 - self._tokens) / self.config.max_rps
            if wait_time * 1000 > self.config.timeout_ms:
                return False

            await asyncio.sleep(wait_time)
            self._refill()

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True

            return False

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self._last_refill
        self._tokens = min(
            self.config.burst_size,
            self._tokens + elapsed * self.config.max_rps
        )
        self._last_refill = now
