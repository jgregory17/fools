#!/usr/bin/env python3
"""
Example: Run a local voice agent without LiveKit.

This demonstrates the agent pipeline with fake/mock components,
useful for testing and development without a LiveKit server.

Usage:
    python examples/run_local_agent.py
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_playground.core.agent_manager import AgentManager, get_factory
from agent_playground.core.config import AgentConfig, ModuleConfig, BehaviorConfig
from agent_playground.core.event_bus import EventBus
from agent_playground.core.events import (
    AgentStateChange,
    AudioFrameIn,
    TranscriptFinal,
    TranscriptPartial,
    LLMToken,
    TTSChunk,
    UtteranceFinal,
)
from agent_playground.livekit_io.room_adapter import MockRoomAdapter, RoomOptions
from agent_playground.livekit_io.audio_pipeline import AudioPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Run a demo agent with mock room."""
    logger.info("Starting local agent demo...")

    # Create in-memory config (no YAML file needed)
    config = AgentConfig(
        name="demo_agent",
        description="Demo agent for local testing",
        instructions="You are a helpful assistant. Be concise.",
        asr=ModuleConfig(backend="fake"),
        llm=ModuleConfig(backend="fake"),
        tts=ModuleConfig(backend="fake"),
        behavior=BehaviorConfig(
            greeting="Hello! This is a local demo. I'm running without LiveKit.",
            allow_interruptions=True,
        ),
    )

    # Create agent manager
    manager = AgentManager()
    manager.add_config(config)

    # Create event bus with logging
    event_bus = manager.event_bus

    # Log interesting events
    @event_bus.on(AgentStateChange)
    async def on_state_change(event: AgentStateChange):
        logger.info(f"State: {event.previous_state.name} -> {event.new_state.name}")

    @event_bus.on(TranscriptPartial)
    async def on_partial(event: TranscriptPartial):
        logger.info(f"[Partial] {event.text}")

    @event_bus.on(TranscriptFinal)
    async def on_transcript(event: TranscriptFinal):
        logger.info(f"[Final] User: {event.text}")

    @event_bus.on(LLMToken)
    async def on_token(event: LLMToken):
        print(event.token, end="", flush=True)

    @event_bus.on(UtteranceFinal)
    async def on_utterance(event: UtteranceFinal):
        print()  # Newline after tokens
        logger.info(f"[Complete] Agent: {event.text[:50]}...")

    @event_bus.on(TTSChunk)
    async def on_tts(event: TTSChunk):
        logger.debug(f"TTS chunk: {len(event.audio_data)} bytes")

    # Create agent
    agent = await manager.create_agent("demo_agent", room_name="demo-room")
    logger.info(f"Created agent: {agent.agent_id}")

    # Create mock room adapter (no LiveKit connection)
    room_adapter = MockRoomAdapter(
        event_bus=event_bus,
        options=RoomOptions(),
        agent_id=agent.agent_id,
    )

    # Create audio pipeline
    pipeline = AudioPipeline(
        agent=agent,
        event_bus=event_bus,
    )

    # Start everything
    await room_adapter.start()
    await pipeline.start()
    await agent.start()

    logger.info("\n" + "=" * 60)
    logger.info("Agent is running! Press Ctrl+C to stop.")
    logger.info("In a real scenario, audio would come from LiveKit room.")
    logger.info("=" * 60 + "\n")

    # Simulate some user speech (in real scenario, comes from room)
    async def simulate_user_speech():
        await asyncio.sleep(2)

        # Inject "audio" that will trigger ASR
        logger.info("\n[Simulating user speech...]")

        # The FakeASR will respond to audio frames with canned responses
        # Inject some non-silent audio to trigger it
        import struct
        # Create simple tone to simulate speech
        sample_rate = 24000
        duration_ms = 1000
        samples = int(sample_rate * duration_ms / 1000)
        audio_data = struct.pack(f'<{samples}h', *[1000] * samples)

        await room_adapter.inject_audio(audio_data)

        # Wait for processing
        await asyncio.sleep(3)

    # Run simulation
    try:
        await simulate_user_speech()

        # Keep running
        logger.info("\nDemo complete! The agent responded to simulated speech.")
        logger.info("In a real scenario, connect a LiveKit client to interact.")

        # Wait a bit more to show the pipeline
        await asyncio.sleep(2)

    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    finally:
        await pipeline.stop()
        await room_adapter.stop()
        await manager.shutdown()

    logger.info("Demo finished!")


if __name__ == "__main__":
    asyncio.run(main())
