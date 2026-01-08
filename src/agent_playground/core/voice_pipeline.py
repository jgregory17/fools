"""
Voice Pipeline Coordinator.

End-to-end voice agent pipeline with:
- Streaming ASR -> LLM -> TTS flow
- Interruption (barge-in) handling with cancellation
- Backpressure-aware bounded queues
- Clean shutdown and resource management

This is the main coordinator that connects all components into
a working voice agent.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

from .audio import CANONICAL_FORMAT, AudioNormalizer, AudioNormalizerConfig
from .events import (
    AudioFrameIn,
    AudioFrameOut,
    LLMToken,
    SpeechEvent,
    TTSChunk,
    InterruptionDetected,
)
from .interfaces import (
    BaseASRBackend,
    BaseLLMBackend,
    BaseTTSBackend,
    ChatContext,
    ChatMessage,
)
from .interrupts import InterruptController, InterruptControllerConfig
from .policies import BehaviorPolicy, EchoSuppressionConfig
from .scheduler import BoundedQueue, BackpressurePolicy, BackpressureMode

logger = logging.getLogger(__name__)


@dataclass
class VoicePipelineConfig:
    """Configuration for the voice pipeline."""

    # Audio settings
    sample_rate: int = CANONICAL_FORMAT.sample_rate
    num_channels: int = CANONICAL_FORMAT.num_channels
    frame_duration_ms: int = CANONICAL_FORMAT.frame_duration_ms

    # Queue sizes
    audio_in_queue_size: int = 100  # ~2s at 20ms frames
    transcript_queue_size: int = 10
    tts_queue_size: int = 50
    audio_out_queue_size: int = 100

    # Interruption settings
    allow_interruptions: bool = True
    interrupt_vad_threshold: float = 0.5
    interrupt_min_speech_ms: int = 100
    interrupt_grace_period_ms: int = 200

    # Echo suppression
    suppress_asr_during_speech: bool = True
    post_speech_suppression_ms: int = 200

    # LLM settings
    system_prompt: str = "You are a helpful voice assistant. Keep responses brief and conversational."
    temperature: float = 0.7
    max_tokens: int = 256


class VoicePipeline:
    """
    Complete voice agent pipeline with interruption support.

    Connects:
    - VAD -> ASR -> LLM -> TTS -> Audio Output

    With cancellation support at every stage for responsive barge-in.

    Usage:
        pipeline = VoicePipeline(asr, llm, tts, vad)
        await pipeline.start()

        # Process audio frames
        async for frame in audio_source:
            await pipeline.process_audio(frame)

        await pipeline.stop()
    """

    def __init__(
        self,
        asr: BaseASRBackend,
        llm: BaseLLMBackend,
        tts: BaseTTSBackend,
        vad: Optional[Any] = None,  # VADBackend
        config: Optional[VoicePipelineConfig] = None,
    ):
        self.config = config or VoicePipelineConfig()

        # Backends
        self._asr = asr
        self._llm = llm
        self._tts = tts
        self._vad = vad

        # State
        self._running = False
        self._speaking = False
        self._current_llm_task: Optional[asyncio.Task] = None
        self._current_tts_task: Optional[asyncio.Task] = None

        # Conversation context
        self._chat_context = ChatContext(messages=[
            ChatMessage(role="system", content=self.config.system_prompt)
        ])

        # Behavior policy
        self._policy = BehaviorPolicy(
            echo_config=EchoSuppressionConfig(
                enabled=self.config.suppress_asr_during_speech,
                post_speech_suppression_ms=self.config.post_speech_suppression_ms,
            ),
        )

        # Interrupt controller
        self._interrupt_controller = InterruptController(
            config=InterruptControllerConfig(
                enabled=self.config.allow_interruptions,
                vad_threshold=self.config.interrupt_vad_threshold,
                min_speech_duration_ms=self.config.interrupt_min_speech_ms,
                grace_period_ms=self.config.interrupt_grace_period_ms,
            ),
            on_interrupt=self._handle_interrupt,
        )

        # Audio normalizer
        self._normalizer = AudioNormalizer(AudioNormalizerConfig(
            target_sample_rate=self.config.sample_rate,
            target_channels=self.config.num_channels,
            target_frame_duration_ms=self.config.frame_duration_ms,
        ))

        # Bounded queues
        self.audio_in_queue: BoundedQueue[AudioFrameIn] = BoundedQueue(
            "audio_in",
            BackpressurePolicy(
                mode=BackpressureMode.DROP_OLDEST,
                max_queue_size=self.config.audio_in_queue_size,
            )
        )

        self.transcript_queue: BoundedQueue[SpeechEvent] = BoundedQueue(
            "transcripts",
            BackpressurePolicy(
                mode=BackpressureMode.DROP_NEWEST,
                max_queue_size=self.config.transcript_queue_size,
            )
        )

        self.audio_out_queue: BoundedQueue[bytes] = BoundedQueue(
            "audio_out",
            BackpressurePolicy(
                mode=BackpressureMode.DROP_OLDEST,
                max_queue_size=self.config.audio_out_queue_size,
            )
        )

        # Tasks
        self._tasks: set[asyncio.Task] = set()

        # Callbacks
        self._on_transcript: Optional[Callable[[str, bool], None]] = None
        self._on_response: Optional[Callable[[str], None]] = None
        self._on_audio_out: Optional[Callable[[bytes], None]] = None
        self._on_interrupt: Optional[Callable[[], None]] = None

        # Metrics
        self._metrics = {
            "utterances_processed": 0,
            "responses_generated": 0,
            "interruptions": 0,
            "asr_errors": 0,
            "llm_errors": 0,
            "tts_errors": 0,
        }

    async def start(self) -> None:
        """Start the pipeline processing loops."""
        if self._running:
            return

        self._running = True
        logger.info("VoicePipeline starting...")

        # Start ASR processing loop
        asr_task = asyncio.create_task(self._asr_loop())
        self._tasks.add(asr_task)
        asr_task.add_done_callback(self._tasks.discard)

        # Start transcript processing loop
        transcript_task = asyncio.create_task(self._transcript_loop())
        self._tasks.add(transcript_task)
        transcript_task.add_done_callback(self._tasks.discard)

        logger.info("VoicePipeline started")

    async def stop(self) -> None:
        """Stop the pipeline and clean up resources."""
        if not self._running:
            return

        logger.info("VoicePipeline stopping...")
        self._running = False

        # Cancel any ongoing generation
        await self._cancel_generation()

        # Cancel all tasks
        for task in list(self._tasks):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.clear()

        # Close backends
        await self._asr.close()
        await self._llm.close()
        await self._tts.close()

        logger.info("VoicePipeline stopped")

    async def process_audio(
        self,
        audio_data: bytes,
        sample_rate: int = CANONICAL_FORMAT.sample_rate,
        num_channels: int = 1,
    ) -> None:
        """
        Process incoming audio data.

        Audio is normalized and queued for ASR processing.
        """
        if not self._running:
            return

        # Check VAD for interruption during agent speech
        if self._speaking and self._vad:
            vad_result = self._vad.process(audio_data)
            triggered = self._interrupt_controller.process_vad(vad_result.probability)
            if triggered:
                # Interrupt callback handles cancellation
                pass

        # Check echo suppression
        if not self._policy.should_process_audio():
            return

        # Normalize audio
        frames = self._normalizer.normalize(
            audio_data,
            input_sample_rate=sample_rate,
            input_channels=num_channels,
        )

        # Queue normalized frames
        for frame_data in frames:
            frame = AudioFrameIn(
                agent_id="pipeline",
                data=frame_data,
                sample_rate=self.config.sample_rate,
                num_channels=self.config.num_channels,
                samples_per_channel=len(frame_data) // 2,
            )
            await self.audio_in_queue.put(frame)

    async def _asr_loop(self) -> None:
        """Process audio through ASR."""
        logger.debug("ASR loop starting")

        async def audio_generator() -> AsyncIterator[AudioFrameIn]:
            while self._running:
                frame = await self.audio_in_queue.get()
                if frame:
                    yield frame

        try:
            async for speech_event in self._asr.stream(audio_generator()):
                if not self._running:
                    break

                # Queue transcript
                await self.transcript_queue.put(speech_event)

                # Callback
                if self._on_transcript:
                    self._on_transcript(speech_event.text, speech_event.is_final)

                if speech_event.is_final:
                    self._metrics["utterances_processed"] += 1

        except Exception as e:
            logger.error(f"ASR loop error: {e}")
            self._metrics["asr_errors"] += 1

    async def _transcript_loop(self) -> None:
        """Process transcripts through LLM and TTS."""
        logger.debug("Transcript loop starting")

        accumulated_text = ""

        while self._running:
            try:
                event = await self.transcript_queue.get()
                if event is None:
                    await asyncio.sleep(0.01)
                    continue

                if event.is_final:
                    # Final transcript - generate response
                    user_text = event.text.strip()
                    if user_text:
                        await self._generate_response(user_text)
                        accumulated_text = ""
                else:
                    # Partial - accumulate
                    accumulated_text = event.text

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Transcript loop error: {e}")

    async def _generate_response(self, user_text: str) -> None:
        """Generate LLM response and synthesize TTS."""
        logger.debug(f"Generating response for: {user_text[:50]}...")

        # Add user message to context
        self._chat_context.add_message("user", user_text)

        # Start speaking state
        self._speaking = True
        self._policy.on_speech_start()
        self._interrupt_controller.on_speech_start()

        try:
            # Stream LLM response
            accumulated_response = ""

            async def token_generator() -> AsyncIterator[str]:
                nonlocal accumulated_response
                async for token in self._llm.chat(
                    self._chat_context,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                ):
                    if not self._running or not self._speaking:
                        break
                    accumulated_response = token.accumulated_text
                    yield token.token

            # Stream to TTS
            async for tts_chunk in self._tts.synthesize(token_generator()):
                if not self._running or not self._speaking:
                    break

                # Queue audio for output
                await self.audio_out_queue.put(tts_chunk.audio_data)

                # Callback
                if self._on_audio_out:
                    self._on_audio_out(tts_chunk.audio_data)

                # Track playback for interruption
                duration_ms = len(tts_chunk.audio_data) / 2 / tts_chunk.sample_rate * 1000
                self._interrupt_controller.on_audio_played(duration_ms)

            # Add assistant response to context
            if accumulated_response:
                self._chat_context.add_message("assistant", accumulated_response)
                self._metrics["responses_generated"] += 1

                if self._on_response:
                    self._on_response(accumulated_response)

        except Exception as e:
            logger.error(f"Response generation error: {e}")
            self._metrics["llm_errors"] += 1

        finally:
            # End speaking state
            self._speaking = False
            self._policy.on_speech_end()
            self._interrupt_controller.on_speech_end()

    async def _handle_interrupt(self, event: InterruptionDetected) -> None:
        """Handle user interruption (barge-in)."""
        logger.info(f"Interruption detected after {event.played_duration_ms:.0f}ms")

        self._metrics["interruptions"] += 1

        # Cancel ongoing generation
        await self._cancel_generation()

        # Callback
        if self._on_interrupt:
            self._on_interrupt()

    async def _cancel_generation(self) -> None:
        """Cancel any ongoing LLM/TTS generation."""
        self._speaking = False

        # Cancel LLM
        if hasattr(self._llm, 'cancel'):
            self._llm.cancel()

        # Cancel TTS
        if hasattr(self._tts, 'cancel'):
            self._tts.cancel()

        # Clear output queue
        while True:
            item = self.audio_out_queue.get_nowait()
            if item is None:
                break

        # Reset backends for next generation
        if hasattr(self._llm, 'reset'):
            self._llm.reset()
        if hasattr(self._tts, 'reset'):
            self._tts.reset()

    def set_callbacks(
        self,
        on_transcript: Optional[Callable[[str, bool], None]] = None,
        on_response: Optional[Callable[[str], None]] = None,
        on_audio_out: Optional[Callable[[bytes], None]] = None,
        on_interrupt: Optional[Callable[[], None]] = None,
    ) -> None:
        """Set callbacks for pipeline events."""
        self._on_transcript = on_transcript
        self._on_response = on_response
        self._on_audio_out = on_audio_out
        self._on_interrupt = on_interrupt

    @property
    def is_speaking(self) -> bool:
        """Whether the agent is currently speaking."""
        return self._speaking

    @property
    def metrics(self) -> dict:
        """Get pipeline metrics."""
        return {
            **self._metrics,
            "audio_in_queue": self.audio_in_queue.metrics.to_dict(),
            "transcript_queue": self.transcript_queue.metrics.to_dict(),
            "audio_out_queue": self.audio_out_queue.metrics.to_dict(),
        }

    @property
    def chat_history(self) -> list[dict]:
        """Get current chat history."""
        return [
            {"role": msg.role, "content": msg.content}
            for msg in self._chat_context.messages
        ]
