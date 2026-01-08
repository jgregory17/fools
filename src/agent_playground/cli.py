#!/usr/bin/env python3
"""
Agent Playground CLI.

Run voice agents with LiveKit integration.

Usage:
    # Run a single agent
    python -m agent_playground --agent simple_voice --room my-room

    # Run multiple agents
    python -m agent_playground --agents simple_voice,support --room my-room

    # Run in dev mode (fake backends)
    python -m agent_playground --agent simple_voice --dev

    # List available agents
    python -m agent_playground --list

    # Generate sample config
    python -m agent_playground --generate-config simple_voice
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("agent_playground")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Agent Playground - Modular Voice AI Agents on LiveKit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Agent selection
    parser.add_argument(
        "--agent", "-a",
        type=str,
        help="Name of agent config to run",
    )
    parser.add_argument(
        "--agents",
        type=str,
        help="Comma-separated list of agents to run",
    )

    # LiveKit connection
    parser.add_argument(
        "--url",
        type=str,
        default=os.environ.get("LIVEKIT_URL", "ws://localhost:7880"),
        help="LiveKit server URL (default: $LIVEKIT_URL or ws://localhost:7880)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("LIVEKIT_API_KEY", "devkey"),
        help="LiveKit API key (default: $LIVEKIT_API_KEY)",
    )
    parser.add_argument(
        "--api-secret",
        type=str,
        default=os.environ.get("LIVEKIT_API_SECRET", "secret"),
        help="LiveKit API secret (default: $LIVEKIT_API_SECRET)",
    )
    parser.add_argument(
        "--room", "-r",
        type=str,
        help="Room name to join (auto-generated if not provided)",
    )

    # Development mode
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Development mode: use fake backends, no LiveKit connection",
    )
    parser.add_argument(
        "--mock-room",
        action="store_true",
        help="Use mock room adapter (no real LiveKit connection)",
    )

    # Configuration
    parser.add_argument(
        "--config-dir", "-c",
        type=str,
        default="configs",
        help="Directory containing agent configs (default: configs/)",
    )

    # Utility commands
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available agent configurations",
    )
    parser.add_argument(
        "--generate-config",
        type=str,
        metavar="NAME",
        help="Generate a sample config file with given name",
    )
    parser.add_argument(
        "--validate",
        type=str,
        metavar="CONFIG",
        help="Validate an agent config file",
    )

    # Logging
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    return parser.parse_args()


async def run_agent_server(args: argparse.Namespace) -> None:
    """Run the agent server."""
    from .core.agent_manager import AgentManager
    from .core.config import AgentConfig
    from .core.event_bus import EventBus
    from .livekit_io.room_adapter import MockRoomAdapter, RoomAdapter, RoomOptions
    from .livekit_io.audio_pipeline import AudioPipeline
    from .agents.base_agent import BasePlaygroundAgent

    logger.info("Starting Agent Playground server...")

    # Initialize agent manager
    manager = AgentManager(config_dir=args.config_dir)
    await manager.initialize()

    # Determine which agents to run
    agent_names = []
    if args.agent:
        agent_names.append(args.agent)
    if args.agents:
        agent_names.extend(args.agents.split(","))

    if not agent_names:
        logger.error("No agents specified. Use --agent or --agents")
        logger.info("Available agents: " + ", ".join(manager.get_available_agents()))
        return

    # Validate agent configs exist
    for name in agent_names:
        if name not in manager.get_available_agents():
            logger.error(f"Unknown agent: {name}")
            logger.info("Available agents: " + ", ".join(manager.get_available_agents()))
            return

    # Generate room name if not provided
    room_name = args.room
    if not room_name:
        import uuid
        room_name = f"playground-{str(uuid.uuid4())[:8]}"

    logger.info(f"Room: {room_name}")
    logger.info(f"Agents: {', '.join(agent_names)}")

    # Create event bus
    event_bus = manager.event_bus

    # Add logging trace handler
    if args.verbose or args.debug:
        def log_event(event):
            logger.debug(f"Event: {type(event).__name__} - {event.event_id}")
        event_bus.add_trace_handler(log_event)

    # Create agents
    agents = []
    for name in agent_names:
        agent = await manager.create_agent(name, room_name=room_name)
        agents.append(agent)
        logger.info(f"Created agent: {agent.agent_id}")

    # Set up room adapter
    if args.dev or args.mock_room:
        # Mock mode - no LiveKit connection
        logger.info("Running in mock room mode (no LiveKit connection)")
        room_adapter = MockRoomAdapter(
            event_bus=event_bus,
            options=RoomOptions(),
            agent_id=agents[0].agent_id if agents else None,
        )
    else:
        # Real LiveKit connection
        logger.info(f"Connecting to LiveKit: {args.url}")

        # TODO: Implement actual LiveKit connection
        # from livekit import rtc
        # room = rtc.Room()
        # await room.connect(args.url, token)
        # room_adapter = RoomAdapter(room, event_bus, RoomOptions())

        # For now, use mock
        logger.warning("LiveKit connection not implemented - using mock adapter")
        room_adapter = MockRoomAdapter(
            event_bus=event_bus,
            options=RoomOptions(),
            agent_id=agents[0].agent_id if agents else None,
        )

    # Start room adapter
    await room_adapter.start()

    # Start agents
    for agent in agents:
        await manager.start_agent(agent.agent_id)
        logger.info(f"Started agent: {agent.agent_id}")

    # Set up signal handlers
    shutdown_event = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    logger.info("Agent server running. Press Ctrl+C to stop.")

    # Wait for shutdown
    await shutdown_event.wait()

    # Cleanup
    logger.info("Shutting down...")
    await room_adapter.stop()
    await manager.shutdown()
    logger.info("Shutdown complete")


def list_agents(config_dir: str) -> None:
    """List available agent configurations."""
    from .core.config import load_all_configs

    configs = load_all_configs(config_dir)

    if not configs:
        print(f"No agent configurations found in {config_dir}/")
        print("\nTo create a sample config:")
        print("  python -m agent_playground --generate-config my_agent")
        return

    print("\nAvailable agents:")
    print("-" * 60)

    for name, config in sorted(configs.items()):
        print(f"\n  {name}")
        if config.description:
            print(f"    {config.description}")
        print(f"    ASR: {config.asr.backend}", end="")
        if config.asr.model:
            print(f" ({config.asr.model})", end="")
        print()
        print(f"    LLM: {config.llm.backend}", end="")
        if config.llm.model:
            print(f" ({config.llm.model})", end="")
        print()
        print(f"    TTS: {config.tts.backend}", end="")
        if config.tts.model:
            print(f" ({config.tts.model})", end="")
        print()

    print()


def generate_config(name: str, config_dir: str) -> None:
    """Generate a sample configuration file."""
    import yaml

    config = {
        "name": name,
        "description": f"A sample voice agent",
        "instructions": "You are a helpful voice AI assistant. Be concise and friendly.",
        "modules": {
            "asr": {
                "backend": "fake",
                "# Use faster_whisper for real ASR": None,
                "# model": "base.en",
                "# device": "cuda",
            },
            "llm": {
                "backend": "fake",
                "# Use ollama for real LLM": None,
                "# model": "llama3.1",
            },
            "tts": {
                "backend": "fake",
                "# Use piper for real TTS": None,
                "# voice": "en_US-lessac-medium",
            },
        },
        "behavior": {
            "allow_interruptions": True,
            "greeting": "Hello! I'm ready to help. What can I do for you?",
            "max_turns": 100,
        },
        "version": "1.0",
        "tags": ["sample", "starter"],
    }

    # Create config directory if needed
    Path(config_dir).mkdir(parents=True, exist_ok=True)

    config_path = Path(config_dir) / f"{name}.yaml"

    if config_path.exists():
        print(f"Config already exists: {config_path}")
        return

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"Generated config: {config_path}")
    print("\nEdit this file to customize your agent.")
    print(f"\nTo run the agent:")
    print(f"  python -m agent_playground --agent {name} --dev")


def validate_config(config_path: str) -> None:
    """Validate an agent configuration file."""
    from .core.config import AgentConfig

    try:
        config = AgentConfig.from_yaml(config_path)
        print(f"Config is valid: {config_path}")
        print(f"  Name: {config.name}")
        print(f"  ASR: {config.asr.backend}")
        print(f"  LLM: {config.llm.backend}")
        print(f"  TTS: {config.tts.backend}")
    except Exception as e:
        print(f"Config validation failed: {e}")
        sys.exit(1)


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Set log level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose:
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("agent_playground").setLevel(logging.DEBUG)

    # Handle utility commands
    if args.list:
        list_agents(args.config_dir)
        return

    if args.generate_config:
        generate_config(args.generate_config, args.config_dir)
        return

    if args.validate:
        validate_config(args.validate)
        return

    # Run agent server
    try:
        asyncio.run(run_agent_server(args))
    except KeyboardInterrupt:
        logger.info("Interrupted")
    except Exception as e:
        logger.exception(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
