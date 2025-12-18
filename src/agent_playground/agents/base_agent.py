"""
Base Agent Class.

Provides the foundation for custom agent implementations
with hooks for lifecycle and pipeline customization.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional

from ..core.agent_manager import AgentInstance
from ..core.config import AgentConfig
from ..core.event_bus import EventBus
from ..core.events import TranscriptFinal
from ..core.interfaces import ChatContext
from ..livekit_io.audio_pipeline import AudioPipeline, PipelineConfig

logger = logging.getLogger(__name__)


class BasePlaygroundAgent:
    """
    Base class for custom agent implementations.

    Override lifecycle hooks to customize behavior:
    - on_enter(): Called when agent becomes active
    - on_exit(): Called before agent deactivates
    - on_user_turn_completed(): Called after user finishes speaking
    - stt_node(): Customize ASR processing
    - llm_node(): Customize LLM inference
    - tts_node(): Customize speech synthesis

    Example:
        class MyAgent(BasePlaygroundAgent):
            async def on_enter(self):
                await self.session.say("Hello! How can I help?")

            async def on_user_turn_completed(self, transcript: str):
                # Add RAG context before LLM
                context = await self.search_docs(transcript)
                self.agent.chat_context.add_system_message(
                    f"Relevant context: {context}"
                )
    """

    def __init__(
        self,
        agent_instance: AgentInstance,
        event_bus: EventBus,
    ):
        self.agent = agent_instance
        self.event_bus = event_bus
        self.pipeline: Optional[AudioPipeline] = None

        # Set up event handlers
        self.event_bus.subscribe(TranscriptFinal, self._handle_transcript)

    async def start(self) -> None:
        """Start the agent and pipeline."""
        logger.info(f"Starting agent: {self.agent.config.name}")

        # Create pipeline config from agent behavior
        pipeline_config = PipelineConfig(
            allow_interruptions=self.agent.config.behavior.allow_interruptions,
            preemptive_generation=self.agent.config.behavior.preemptive_generation,
        )

        # Create and start pipeline
        self.pipeline = AudioPipeline(
            agent=self.agent,
            event_bus=self.event_bus,
            config=pipeline_config,
        )
        await self.pipeline.start()

        # Start agent instance
        await self.agent.start()

        # Call lifecycle hook
        await self.on_enter()

    async def stop(self) -> None:
        """Stop the agent and cleanup."""
        logger.info(f"Stopping agent: {self.agent.config.name}")

        # Call lifecycle hook
        await self.on_exit()

        # Stop pipeline
        if self.pipeline:
            await self.pipeline.stop()

        # Stop agent instance
        await self.agent.stop()

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle Hooks (override in subclasses)
    # ─────────────────────────────────────────────────────────────────────────

    async def on_enter(self) -> None:
        """
        Called when the agent becomes the active agent.

        Override to customize greeting or initialization.
        """
        if self.agent.config.behavior.greeting:
            await self.say(self.agent.config.behavior.greeting)

    async def on_exit(self) -> None:
        """
        Called before the agent deactivates.

        Override to save state or say goodbye.
        """
        pass

    async def on_user_turn_completed(
        self,
        transcript: str,
        context: ChatContext,
    ) -> None:
        """
        Called when user finishes speaking, before LLM inference.

        Override to:
        - Add RAG context
        - Modify the transcript
        - Cancel response generation

        Args:
            transcript: The user's complete utterance
            context: Current chat context (can be modified)
        """
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # Pipeline Node Hooks (override for custom processing)
    # ─────────────────────────────────────────────────────────────────────────

    async def stt_node(
        self,
        audio: AsyncIterator[bytes],
    ) -> AsyncIterator[str]:
        """
        Override to customize ASR processing.

        Default: use agent's ASR backend directly.
        """
        # Default implementation - subclasses can override
        raise NotImplementedError("Use default pipeline")

    async def llm_node(
        self,
        context: ChatContext,
    ) -> AsyncIterator[str]:
        """
        Override to customize LLM inference.

        Default: use agent's LLM backend directly.
        """
        raise NotImplementedError("Use default pipeline")

    async def tts_node(
        self,
        text: AsyncIterator[str],
    ) -> AsyncIterator[bytes]:
        """
        Override to customize TTS synthesis.

        Default: use agent's TTS backend directly.
        """
        raise NotImplementedError("Use default pipeline")

    # ─────────────────────────────────────────────────────────────────────────
    # Convenience Methods
    # ─────────────────────────────────────────────────────────────────────────

    async def say(
        self,
        text: str,
        allow_interruptions: bool = True,
    ) -> None:
        """
        Have the agent speak a message.

        Args:
            text: Text to speak
            allow_interruptions: Whether user can interrupt
        """
        logger.info(f"Agent saying: {text[:50]}...")

        # Synthesize and queue audio
        async for chunk in self.agent.tts.synthesize_text(text):
            # Audio will be published via pipeline
            pass

    async def generate_reply(
        self,
        instructions: Optional[str] = None,
        user_input: Optional[str] = None,
    ) -> None:
        """
        Generate an LLM response.

        Args:
            instructions: Optional instructions for the response
            user_input: Optional user input to respond to
        """
        if instructions:
            self.agent.chat_context.add_system_message(instructions)

        if user_input:
            self.agent.chat_context.add_user_message(user_input)

        # Trigger LLM generation via pipeline
        # (Implementation depends on pipeline state)

    def update_instructions(self, instructions: str) -> None:
        """Update the agent's system instructions."""
        # Remove old system messages and add new
        self.agent.chat_context.messages = [
            m for m in self.agent.chat_context.messages
            if m.role != "system"
        ]
        self.agent.chat_context.add_system_message(instructions)

    async def _handle_transcript(self, event: TranscriptFinal) -> None:
        """Handle final transcript events."""
        # Filter for this agent
        if event.agent_id and event.agent_id != self.agent.agent_id:
            return

        # Call hook before pipeline processes
        await self.on_user_turn_completed(
            event.text,
            self.agent.chat_context,
        )
