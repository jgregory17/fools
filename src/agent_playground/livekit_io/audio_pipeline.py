"""
Audio Pipeline - Orchestrates the ASR → LLM → TTS flow.

Handles:
- Streaming audio through ASR
- Turn detection and user input aggregation
- LLM response generation
- TTS synthesis and audio output
- Interruption handling
- Backpressure management
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import AsyncIterator, Optional

from ..core.agent_manager import AgentInstance
from ..core.event_bus import EventBus
from ..core.events import (
    AgentState,
    AgentStateChange,
    AudioFrameIn,
    AudioFrameOut,
    EndOfTurnDetected,
    InterruptionDetected,
    LLMToken,
    TranscriptFinal,
    TranscriptPartial,
    TTSChunk,
    UtteranceFinal,
)
from ..core.interfaces import ChatContext, SpeechEvent

logger = logging.getLogger(__name__)


class PipelineState(Enum):
    """Internal pipeline state."""

    IDLE = auto()
    LISTENING = auto()
    PROCESSING_ASR = auto()
    GENERATING_LLM = auto()
    SYNTHESIZING_TTS = auto()
    INTERRUPTED = auto()


@dataclass
class PipelineConfig:
    """Configuration for the audio pipeline."""

    # Turn detection
    silence_threshold_ms: float = 500.0
    min_speech_duration_ms: float = 100.0

    # Interruption handling
    allow_interruptions: bool = True
    interruption_threshold_ms: float = 200.0

    # Backpressure
    max_pending_frames: int = 100
    max_tts_queue_size: int = 50

    # Preemptive generation
    preemptive_generation: bool = False


class AudioPipeline:
    """
    Orchestrates the voice AI pipeline: ASR → LLM → TTS.

    This is the main processing engine that:
    1. Receives audio frames from RoomAdapter
    2. Streams to ASR for transcription
    3. Detects end of user turn
    4. Generates LLM response
    5. Synthesizes TTS output
    6. Publishes audio back to room

    Handles edge cases:
    - User interruptions during agent speech
    - Long silences
    - Backpressure when TTS can't keep up
    - Graceful shutdown
    """

    def __init__(
        self,
        agent: AgentInstance,
        event_bus: EventBus,
        config: Optional[PipelineConfig] = None,
    ):
        self._agent = agent
        self._event_bus = event_bus
        self._config = config or PipelineConfig()

        # State
        self._state = PipelineState.IDLE
        self._running = False

        # Audio input queue (from room adapter)
        self._audio_in_queue: asyncio.Queue[AudioFrameIn] = asyncio.Queue(
            maxsize=self._config.max_pending_frames
        )

        # Audio output queue (to room adapter)
        self._audio_out_queue: asyncio.Queue[AudioFrameOut] = asyncio.Queue(
            maxsize=self._config.max_tts_queue_size
        )

        # Current turn state
        self._current_transcript = ""
        self._speech_start_time: Optional[float] = None
        self._last_speech_time: Optional[float] = None

        # Cancellation
        self._cancel_generation = asyncio.Event()
        self._generation_task: Optional[asyncio.Task] = None

        # Tasks
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start the audio pipeline."""
        if self._running:
            return

        logger.info(f"AudioPipeline starting for agent {self._agent.agent_id}")
        self._running = True
        self._state = PipelineState.LISTENING

        # Register event handlers
        self._event_bus.subscribe(AudioFrameIn, self._handle_audio_in)

        # Start processing tasks
        self._tasks.append(asyncio.create_task(self._asr_loop()))
        self._tasks.append(asyncio.create_task(self._turn_detection_loop()))

        logger.info(f"AudioPipeline started for agent {self._agent.agent_id}")

    async def stop(self) -> None:
        """Stop the audio pipeline."""
        if not self._running:
            return

        logger.info(f"AudioPipeline stopping for agent {self._agent.agent_id}")
        self._running = False

        # Cancel any in-flight generation
        self._cancel_generation.set()
        if self._generation_task:
            self._generation_task.cancel()

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.clear()
        logger.info(f"AudioPipeline stopped for agent {self._agent.agent_id}")

    async def _handle_audio_in(self, event: AudioFrameIn) -> None:
        """Handle incoming audio frame."""
        # Filter for this agent
        if event.agent_id and event.agent_id != self._agent.agent_id:
            return

        # Check for interruption during agent speech
        if self._state == PipelineState.SYNTHESIZING_TTS:
            if self._config.allow_interruptions:
                # Detect if this is actual speech (would use VAD in real impl)
                if self._is_speech(event):
                    await self._handle_interruption()

        # Queue audio for ASR
        try:
            self._audio_in_queue.put_nowait(event)
        except asyncio.QueueFull:
            # Backpressure: drop oldest frame
            try:
                self._audio_in_queue.get_nowait()
                self._audio_in_queue.put_nowait(event)
            except asyncio.QueueEmpty:
                pass

    def _is_speech(self, frame: AudioFrameIn) -> bool:
        """Simple speech detection (placeholder for VAD)."""
        # In real implementation, would use Silero VAD or similar
        # For now, check if audio has significant energy
        if not frame.data:
            return False

        import struct
        try:
            samples = struct.unpack(f'<{len(frame.data)//2}h', frame.data)
            energy = sum(abs(s) for s in samples) / len(samples)
            return energy > 500  # Threshold
        except Exception:
            return False

    async def _handle_interruption(self) -> None:
        """Handle user interrupting agent speech."""
        logger.info("User interruption detected")

        self._state = PipelineState.INTERRUPTED
        self._cancel_generation.set()

        # Emit interruption event
        await self._event_bus.emit(InterruptionDetected(
            agent_id=self._agent.agent_id,
        ))

        # Clear TTS queue
        while not self._audio_out_queue.empty():
            try:
                self._audio_out_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Reset for new turn
        await asyncio.sleep(0.1)  # Brief pause
        self._state = PipelineState.LISTENING
        self._cancel_generation.clear()

    async def _asr_loop(self) -> None:
        """Main ASR processing loop."""
        logger.debug("ASR loop started")

        async def audio_generator() -> AsyncIterator[AudioFrameIn]:
            """Generate audio frames from queue."""
            while self._running:
                try:
                    frame = await asyncio.wait_for(
                        self._audio_in_queue.get(),
                        timeout=0.1,
                    )
                    yield frame
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

        try:
            # Stream to ASR
            async for speech_event in self._agent.asr.stream(audio_generator()):
                if not self._running:
                    break

                # Track speech timing
                now = time.time()
                if speech_event.text and not speech_event.is_final:
                    if self._speech_start_time is None:
                        self._speech_start_time = now
                    self._last_speech_time = now

                # Emit transcript events
                if speech_event.is_final:
                    self._current_transcript = speech_event.text
                    await self._event_bus.emit(TranscriptFinal(
                        agent_id=self._agent.agent_id,
                        text=speech_event.text,
                        confidence=speech_event.confidence,
                        language=speech_event.language,
                    ))
                else:
                    await self._event_bus.emit(TranscriptPartial(
                        agent_id=self._agent.agent_id,
                        text=speech_event.text,
                        confidence=speech_event.confidence,
                        language=speech_event.language,
                    ))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception(f"ASR loop error: {e}")

    async def _turn_detection_loop(self) -> None:
        """Monitor for end of user turn and trigger response."""
        logger.debug("Turn detection loop started")

        while self._running:
            await asyncio.sleep(0.1)  # Check every 100ms

            if self._state != PipelineState.LISTENING:
                continue

            # Check for end of turn
            if self._current_transcript and self._last_speech_time:
                silence_duration = (time.time() - self._last_speech_time) * 1000

                if silence_duration > self._config.silence_threshold_ms:
                    # End of turn detected
                    logger.info(f"End of turn detected: {self._current_transcript}")

                    await self._event_bus.emit(EndOfTurnDetected(
                        agent_id=self._agent.agent_id,
                        confidence=0.9,
                    ))

                    # Start response generation
                    self._generation_task = asyncio.create_task(
                        self._generate_response(self._current_transcript)
                    )

                    # Reset for next turn
                    self._current_transcript = ""
                    self._speech_start_time = None
                    self._last_speech_time = None

    async def _generate_response(self, user_input: str) -> None:
        """Generate LLM response and synthesize TTS."""
        logger.info(f"Generating response for: {user_input[:50]}...")

        self._state = PipelineState.GENERATING_LLM

        # Update agent state
        await self._event_bus.emit(AgentStateChange(
            agent_id=self._agent.agent_id,
            previous_state=AgentState.LISTENING,
            new_state=AgentState.THINKING,
        ))

        # Add user message to context
        self._agent.chat_context.add_user_message(user_input)

        try:
            # Generate LLM response
            accumulated_text = ""
            llm_start = time.time()
            ttft = None

            async for token in self._agent.llm.chat(
                self._agent.chat_context,
                tools=self._agent.tools,
            ):
                if self._cancel_generation.is_set():
                    logger.info("Generation cancelled")
                    return

                # Track TTFT
                if ttft is None:
                    ttft = (time.time() - llm_start) * 1000

                accumulated_text = token.accumulated_text

                # Emit token event
                await self._event_bus.emit(token)

            # Complete LLM response
            await self._event_bus.emit(UtteranceFinal(
                agent_id=self._agent.agent_id,
                text=accumulated_text,
                ttft_ms=ttft or 0,
            ))

            # Add assistant message to context
            self._agent.chat_context.add_assistant_message(accumulated_text)

            # Synthesize TTS
            if accumulated_text:
                await self._synthesize_and_play(accumulated_text)

        except asyncio.CancelledError:
            logger.info("Response generation cancelled")
        except Exception as e:
            logger.exception(f"Response generation error: {e}")
        finally:
            # Return to listening state
            self._state = PipelineState.LISTENING
            await self._event_bus.emit(AgentStateChange(
                agent_id=self._agent.agent_id,
                previous_state=AgentState.SPEAKING,
                new_state=AgentState.LISTENING,
            ))

    async def _synthesize_and_play(self, text: str) -> None:
        """Synthesize text to speech and queue for playback."""
        logger.info(f"Synthesizing: {text[:50]}...")

        self._state = PipelineState.SYNTHESIZING_TTS

        await self._event_bus.emit(AgentStateChange(
            agent_id=self._agent.agent_id,
            previous_state=AgentState.THINKING,
            new_state=AgentState.SPEAKING,
        ))

        try:
            async for chunk in self._agent.tts.synthesize_text(text):
                if self._cancel_generation.is_set():
                    return

                # Emit TTS chunk event
                await self._event_bus.emit(chunk)

                # Queue audio for output
                audio_out = AudioFrameOut(
                    agent_id=self._agent.agent_id,
                    data=chunk.audio_data,
                    sample_rate=chunk.sample_rate,
                    num_channels=chunk.num_channels,
                    samples_per_channel=len(chunk.audio_data) // 2,
                    text_segment=chunk.text_segment,
                )

                try:
                    self._audio_out_queue.put_nowait(audio_out)
                except asyncio.QueueFull:
                    # Backpressure: wait for space
                    await self._audio_out_queue.put(audio_out)

        except Exception as e:
            logger.exception(f"TTS synthesis error: {e}")

    def get_audio_output(self) -> asyncio.Queue[AudioFrameOut]:
        """Get the audio output queue for the room adapter."""
        return self._audio_out_queue

    @property
    def state(self) -> PipelineState:
        """Get current pipeline state."""
        return self._state
