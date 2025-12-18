"""
LiveKit Agent Worker - The Voice AI Engine

This is the core agent that joins LiveKit rooms and handles voice conversations.
It uses the official LiveKit Agents SDK with AgentSession to orchestrate:
  - Speech-to-Text (STT)
  - Large Language Model (LLM)
  - Text-to-Speech (TTS)
  - Voice Activity Detection (VAD)
  - Turn Detection

Configuration via environment variables:
  LIVEKIT_URL         - LiveKit server URL (ws://localhost:7880)
  LIVEKIT_API_KEY     - API key for authentication
  LIVEKIT_API_SECRET  - API secret for authentication

  # Model configuration (choose one mode)
  AGENT_MODE          - "cloud", "local", "ollama", or "selfhosted"
    - cloud:      LiveKit Cloud Inference (requires LiveKit Cloud)
    - local:      Plugins with API keys (Deepgram STT, OpenAI TTS, Ollama LLM)
    - ollama:     Ollama LLM with cloud STT/TTS
    - selfhosted: Fully local - Vosk STT, Ollama LLM, Piper TTS (NO cloud APIs!)

  # For local/ollama modes:
  LLM_BASE_URL        - Ollama URL (http://ollama:11434/v1)
  LLM_MODEL           - Ollama model (llama3.2)
  DEEPGRAM_API_KEY    - Deepgram API key for STT
  OPENAI_API_KEY      - OpenAI API key for TTS

  # For selfhosted mode (all local, no API keys needed):
  VOSK_MODEL          - Vosk model name (vosk-model-small-en-us-0.15)
  PIPER_VOICE         - Piper voice name (en_US-lessac-medium)

Usage:
  # Development mode (connects to LiveKit, auto-reloads)
  python -m agent_playground.worker dev

  # Production mode
  python -m agent_playground.worker start

  # Fully self-hosted mode (no cloud APIs)
  AGENT_MODE=selfhosted python -m agent_playground.worker dev
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from livekit import agents, rtc
from livekit.agents import AgentServer, AgentSession, Agent, metrics, MetricsCollectedEvent
from livekit.agents.voice import room_io
from livekit.plugins import silero, noise_cancellation

# Load environment variables
load_dotenv(".env.local")
load_dotenv(".env")

logger = logging.getLogger("agent-worker")


class VoiceAssistant(Agent):
    """
    The main voice assistant agent.

    Override methods to customize behavior:
    - on_enter: Called when agent joins room
    - on_user_turn_completed: Called after user finishes speaking
    - on_agent_turn_completed: Called after agent finishes speaking
    """

    def __init__(self, config_name: str = "default") -> None:
        # Load instructions based on config
        instructions = self._get_instructions(config_name)
        super().__init__(instructions=instructions)
        self.config_name = config_name

    def _get_instructions(self, config_name: str) -> str:
        """Get agent instructions based on config name."""
        # Default instructions for voice AI
        default_instructions = """You are a helpful voice AI assistant.

You should:
- Be concise and conversational (this is a voice interface)
- Respond in 1-3 sentences unless more detail is requested
- Be friendly and professional
- Ask clarifying questions if needed

Remember: Users are speaking to you, not typing. Keep responses natural and brief."""

        # Config-specific instructions could be loaded from YAML here
        instructions_map = {
            "default": default_instructions,
            "simple_voice": default_instructions,
            "creative_writer": """You are a creative writing assistant with a flair for storytelling.

You help users with:
- Creative writing and brainstorming
- Story development and plot ideas
- Character creation
- Poetry and prose

Be imaginative but keep voice responses concise. Elaborate only when asked.""",
            "code_assistant": """You are a programming assistant who helps with code.

You help users with:
- Explaining code concepts
- Debugging strategies
- Best practices
- Code architecture

Keep explanations clear and concise for voice. Avoid reading long code blocks aloud.""",
        }

        return instructions_map.get(config_name, default_instructions)


def create_session(agent_mode: str = "cloud") -> AgentSession:
    """
    Create an AgentSession with the appropriate STT/LLM/TTS configuration.

    Args:
        agent_mode: "cloud" for LiveKit Inference, "local" for self-hosted plugins
    """
    # Load VAD (always local - downloads model on first run)
    vad = silero.VAD.load()

    if agent_mode == "cloud":
        # Use LiveKit Inference (requires LiveKit Cloud)
        # These string descriptors route to LiveKit's hosted inference
        logger.info("Using LiveKit Cloud Inference for STT/LLM/TTS")

        return AgentSession(
            stt="deepgram/nova-3",  # or "assemblyai/universal-streaming:en"
            llm="openai/gpt-4.1-mini",
            tts="cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
            vad=vad,
        )

    elif agent_mode == "local":
        # Use plugins with API keys for self-hosted setup
        logger.info("Using local/plugin mode for STT/LLM/TTS")

        from livekit.plugins import openai as openai_plugin
        from livekit.plugins import deepgram as deepgram_plugin

        # LLM: Ollama (local, free)
        llm_base_url = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
        llm_model = os.environ.get("LLM_MODEL", "llama3.2")

        llm = openai_plugin.LLM.with_ollama(
            model=llm_model,
            base_url=llm_base_url,
            temperature=0.7,
        )
        logger.info(f"LLM: Ollama ({llm_model}) at {llm_base_url}")

        # STT: Deepgram (requires DEEPGRAM_API_KEY)
        stt = deepgram_plugin.STT(
            model="nova-2",
        )
        logger.info("STT: Deepgram Nova-2")

        # TTS: OpenAI (requires OPENAI_API_KEY)
        tts = openai_plugin.TTS(
            model="tts-1",
            voice="alloy",
        )
        logger.info("TTS: OpenAI TTS-1")

        return AgentSession(
            stt=stt,
            llm=llm,
            tts=tts,
            vad=vad,
        )

    elif agent_mode == "ollama":
        # Ollama-only mode (LLM local, STT/TTS still need API keys)
        logger.info("Using Ollama for LLM with cloud STT/TTS")

        from livekit.plugins import openai as openai_plugin

        llm_base_url = os.environ.get("LLM_BASE_URL", "http://ollama:11434/v1")
        llm_model = os.environ.get("LLM_MODEL", "llama3.2")

        llm = openai_plugin.LLM.with_ollama(
            model=llm_model,
            base_url=llm_base_url,
            temperature=0.7,
        )

        return AgentSession(
            stt="deepgram/nova-3",
            llm=llm,
            tts="cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
            vad=vad,
        )

    elif agent_mode == "selfhosted":
        # Fully self-hosted mode - NO cloud APIs required!
        # Uses: Vosk (streaming STT), Ollama (LLM), Piper (TTS)
        logger.info("Using fully self-hosted mode: Vosk STT + Ollama LLM + Piper TTS")

        from .local_agent import create_selfhosted_session

        vosk_model = os.environ.get("VOSK_MODEL", "vosk-model-small-en-us-0.15")
        llm_base_url = os.environ.get("LLM_BASE_URL", "http://ollama:11434/v1")
        llm_model = os.environ.get("LLM_MODEL", "llama3.2")

        return create_selfhosted_session(
            vosk_model=vosk_model,
            ollama_model=llm_model,
            ollama_base_url=llm_base_url,
        )

    else:
        raise ValueError(f"Unknown agent_mode: {agent_mode}. Valid: cloud, local, ollama, selfhosted")


# Create the agent server
server = AgentServer()


async def on_session_end(ctx: agents.JobContext) -> None:
    """
    Callback when session ends - generates structured session report.

    The report includes:
    - Job, room, and participant identifiers
    - Complete conversation history with timestamps
    - All session events (transcription, speech detection, etc.)
    - Agent session options and configuration
    """
    import json
    from datetime import datetime

    try:
        report = ctx.make_session_report()
        report_dict = report.to_dict()

        # Save to /tmp for debugging (in production, send to your analytics service)
        current_date = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"/tmp/session_report_{ctx.room.name}_{current_date}.json"

        with open(filename, "w") as f:
            json.dump(report_dict, f, indent=2)

        logger.info(f"Session report saved to {filename}")
    except Exception as e:
        logger.error(f"Failed to save session report: {e}")


@server.rtc_session(on_session_end=on_session_end)
async def voice_agent(ctx: agents.JobContext):
    """
    Main entrypoint for the voice agent.

    This function is called when a new room needs an agent.
    The agent joins the room, subscribes to user audio, and begins conversation.

    Participant Attributes (synced to all participants):
    - Agent sets: agent.persona, agent.mood, agent.config, agent.mode
    - User sets: user.language, user.context (read by agent to customize behavior)
    - Framework sets: lk.agent.state (initializing, listening, thinking, speaking)
    """
    import json as json_module
    from livekit import rtc

    # Determine configuration
    agent_mode = os.environ.get("AGENT_MODE", "cloud")

    # Get agent config from job metadata (set via token or dispatch)
    agent_config = "default"
    user_metadata = {}
    if ctx.job and ctx.job.metadata:
        try:
            user_metadata = json_module.loads(ctx.job.metadata)
            agent_config = user_metadata.get("agent", "default")
        except (json_module.JSONDecodeError, AttributeError):
            pass

    logger.info(f"Starting agent in room={ctx.room.name}, config={agent_config}, mode={agent_mode}")

    # Create the session
    session = create_session(agent_mode)

    # Create the agent with appropriate config
    if agent_mode == "selfhosted":
        # Use LocalVoiceAssistant for fully self-hosted mode
        from .local_agent import LocalVoiceAssistant, get_local_instructions

        piper_voice = os.environ.get("PIPER_VOICE", "en_US-lessac-medium")
        instructions = get_local_instructions(agent_config)

        assistant = LocalVoiceAssistant(
            instructions=instructions,
            piper_voice=piper_voice,
        )
        logger.info(f"Using LocalVoiceAssistant with Piper voice: {piper_voice}")
    else:
        assistant = VoiceAssistant(config_name=agent_config)

    # Configure room I/O options
    # Note: BVC (noise cancellation) is cloud-only, skip for selfhosted mode
    if agent_mode == "selfhosted":
        audio_input_opts = room_io.AudioInputOptions()
    else:
        audio_input_opts = room_io.AudioInputOptions(
            # Enable Background Voice Cancellation (BVC) for improved STT accuracy
            # Removes background noise while preserving speech clarity
            noise_cancellation=noise_cancellation.BVC(),
        )

    room_options = room_io.RoomOptions(
        audio_input=audio_input_opts,
        audio_output=room_io.AudioOutputOptions(
            sample_rate=24000,
            num_channels=1,
        ),
        text_output=room_io.TextOutputOptions(
            # Sync transcriptions with audio playback
            sync_transcription=True,
        ),
    )

    # Initialize usage collector for cost tracking
    usage_collector = metrics.UsageCollector()

    # Start the session
    await session.start(
        room=ctx.room,
        agent=assistant,
        room_options=room_options,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Participant Attributes - Set agent state and listen for user preferences
    # ─────────────────────────────────────────────────────────────────────────

    # Set initial agent attributes (visible to all participants including UI)
    try:
        await ctx.room.local_participant.set_attributes({
            "agent.config": agent_config,
            "agent.mode": agent_mode,
            "agent.persona": agent_config,  # Can be updated dynamically
            "agent.mood": "friendly",  # Can be updated based on conversation
            "agent.version": "1.0.0",
        })
        logger.info(f"Set agent attributes: config={agent_config}, mode={agent_mode}")
    except Exception as e:
        logger.warning(f"Failed to set agent attributes: {e}")

    # Track user preferences from their attributes
    user_preferences = {
        "language": "en",
        "context": None,
    }

    # Listen for participant attribute changes (user can set preferences)
    @ctx.room.on("participant_attributes_changed")
    def on_participant_attributes_changed(
        changed_attributes: dict[str, str],
        participant: rtc.Participant,
    ):
        """Handle attribute changes from user participants."""
        # Only process remote participants (users), not the agent itself
        if participant == ctx.room.local_participant:
            return

        logger.info(
            f"Participant {participant.identity} attributes changed: {changed_attributes}"
        )

        # Check for language preference change
        if "user.language" in changed_attributes:
            new_lang = changed_attributes["user.language"]
            user_preferences["language"] = new_lang
            logger.info(f"User language preference changed to: {new_lang}")
            # Could update agent instructions or TTS voice here

        # Check for context updates
        if "user.context" in changed_attributes:
            user_preferences["context"] = changed_attributes["user.context"]
            logger.info(f"User context updated: {user_preferences['context']}")

    # Listen for room metadata changes (shared room state)
    @ctx.room.on("room_metadata_changed")
    def on_room_metadata_changed(old_metadata: str, new_metadata: str):
        """Handle room metadata changes."""
        logger.info(f"Room metadata changed: {new_metadata[:100]}...")
        try:
            room_state = json_module.loads(new_metadata)
            # Could update agent behavior based on room state
            logger.debug(f"Parsed room state: {room_state}")
        except json_module.JSONDecodeError:
            pass

    # Helper to update agent mood (can be called during conversation)
    async def update_agent_mood(mood: str):
        """Update agent mood attribute (visible to UI)."""
        try:
            await ctx.room.local_participant.set_attributes({
                "agent.mood": mood,
            })
            logger.debug(f"Updated agent mood to: {mood}")
        except Exception as e:
            logger.warning(f"Failed to update agent mood: {e}")

    # Store helper on context for potential use by agent
    ctx._update_agent_mood = update_agent_mood
    ctx._user_preferences = user_preferences

    # ─────────────────────────────────────────────────────────────────────────
    # Metrics and Usage Collection
    # ─────────────────────────────────────────────────────────────────────────

    # Set up metrics collection for observability
    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent) -> None:
        """Handle metrics events for logging and usage tracking."""
        # Log metrics for debugging (STT/LLM/TTS latencies, tokens, etc.)
        metrics.log_metrics(ev.metrics)

        # Aggregate usage for cost estimation
        usage_collector.collect(ev.metrics)

    # Log usage summary on shutdown
    async def log_usage_summary() -> None:
        try:
            summary = usage_collector.get_summary()
            logger.info(f"Session usage summary: {summary}")
        except Exception as e:
            logger.warning(f"Failed to get usage summary: {e}")

    ctx.add_shutdown_callback(log_usage_summary)

    # Generate initial greeting
    greeting = _get_greeting(agent_config)
    await session.generate_reply(instructions=greeting)

    logger.info(f"Agent session started for room={ctx.room.name}")


def _get_greeting(config_name: str) -> str:
    """Get the greeting based on agent config."""
    greetings = {
        "default": "Greet the user warmly and offer your assistance.",
        "simple_voice": "Say hello and ask how you can help today.",
        "creative_writer": "Greet the user enthusiastically and offer to help with creative writing.",
        "code_assistant": "Greet the user and offer to help with programming questions.",
        "language_tutor": "Greet the user in a friendly way and offer to practice conversation.",
    }
    return greetings.get(config_name, greetings["default"])


def main():
    """Run the agent worker."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    # Log configuration
    logger.info("Agent Worker Configuration:")
    logger.info(f"  LIVEKIT_URL: {os.environ.get('LIVEKIT_URL', 'not set')}")
    logger.info(f"  AGENT_MODE: {os.environ.get('AGENT_MODE', 'cloud')}")
    logger.info(f"  LLM_MODEL: {os.environ.get('LLM_MODEL', 'llama3.2')}")

    # Run the agent server
    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
