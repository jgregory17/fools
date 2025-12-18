#!/usr/bin/env python3
"""
Example: Create a custom agent with specialized behavior.

This demonstrates how to subclass BasePlaygroundAgent to create
agents with custom lifecycle hooks and processing logic.

Usage:
    python examples/custom_agent.py
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_playground.core.agent_manager import AgentManager
from agent_playground.core.config import AgentConfig, ModuleConfig, BehaviorConfig
from agent_playground.core.event_bus import EventBus
from agent_playground.core.interfaces import ChatContext
from agent_playground.agents.base_agent import BasePlaygroundAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger(__name__)


class WeatherAgent(BasePlaygroundAgent):
    """
    Custom agent that specializes in weather information.

    Demonstrates:
    - Custom on_enter greeting
    - Pre-processing user turns with context injection
    - Tracking conversation state
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._questions_asked = 0
        self._locations_mentioned = []

    async def on_enter(self) -> None:
        """Custom greeting focused on weather."""
        logger.info("WeatherAgent entering session")

        greeting = (
            "Hi there! I'm your weather assistant. "
            "I can help you with weather forecasts, conditions, "
            "and recommendations. What location are you interested in?"
        )

        await self.say(greeting)

    async def on_exit(self) -> None:
        """Save session summary before exiting."""
        logger.info(f"WeatherAgent exiting. Handled {self._questions_asked} questions.")
        logger.info(f"Locations mentioned: {self._locations_mentioned}")

        # Could save to database here
        self.agent.userdata["session_summary"] = {
            "questions_asked": self._questions_asked,
            "locations": self._locations_mentioned,
        }

    async def on_user_turn_completed(
        self,
        transcript: str,
        context: ChatContext,
    ) -> None:
        """
        Process user input before LLM inference.

        - Detect location mentions
        - Add weather context
        - Track statistics
        """
        self._questions_asked += 1
        logger.info(f"Processing question #{self._questions_asked}: {transcript[:50]}...")

        # Simple location detection (real impl would use NER)
        location_keywords = ["in", "for", "at", "near"]
        words = transcript.lower().split()

        for i, word in enumerate(words):
            if word in location_keywords and i + 1 < len(words):
                location = words[i + 1]
                if location not in self._locations_mentioned:
                    self._locations_mentioned.append(location)

        # Inject weather-specific context
        context.add_system_message(
            "Remember: You are a weather specialist. "
            f"Locations discussed so far: {', '.join(self._locations_mentioned) or 'none'}. "
            "Always mention temperature in both Fahrenheit and Celsius. "
            "Include practical recommendations (umbrella, sunscreen, etc.)."
        )


class ConversationRouterAgent(BasePlaygroundAgent):
    """
    Agent that routes conversations to specialized agents.

    Demonstrates multi-agent workflow with handoffs.
    """

    async def on_enter(self) -> None:
        """Generic welcome that probes for intent."""
        await self.say(
            "Welcome! I can help with weather, support issues, "
            "or general questions. What do you need help with?"
        )

    async def on_user_turn_completed(
        self,
        transcript: str,
        context: ChatContext,
    ) -> None:
        """Detect intent and signal handoff if needed."""
        lower = transcript.lower()

        # Route based on keywords
        if any(word in lower for word in ["weather", "temperature", "rain", "sunny"]):
            logger.info("Routing to weather agent")
            self.agent.userdata["_transfer_to"] = "weather_agent"
            context.add_system_message(
                "User wants weather help. Acknowledge and prepare to transfer."
            )

        elif any(word in lower for word in ["help", "problem", "issue", "broken"]):
            logger.info("Routing to support agent")
            self.agent.userdata["_transfer_to"] = "support_agent"
            context.add_system_message(
                "User needs support. Acknowledge and prepare to transfer."
            )

        # Otherwise handle normally


async def main():
    """Demonstrate custom agents."""
    logger.info("Custom Agent Demo")
    logger.info("=" * 60)

    # Create configs for our custom agents
    weather_config = AgentConfig(
        name="weather_agent",
        description="Weather specialist agent",
        instructions="You are a weather specialist. Provide accurate, helpful weather information.",
        asr=ModuleConfig(backend="fake"),
        llm=ModuleConfig(backend="fake"),
        tts=ModuleConfig(backend="fake"),
        behavior=BehaviorConfig(allow_interruptions=True),
    )

    router_config = AgentConfig(
        name="router_agent",
        description="Conversation router",
        instructions="You route conversations to appropriate specialists.",
        asr=ModuleConfig(backend="fake"),
        llm=ModuleConfig(backend="fake"),
        tts=ModuleConfig(backend="fake"),
        behavior=BehaviorConfig(allow_interruptions=True),
    )

    # Create manager and add configs
    manager = AgentManager()
    manager.add_config(weather_config)
    manager.add_config(router_config)

    # Create agent instances
    weather_agent = await manager.create_agent("weather_agent")
    router_agent = await manager.create_agent("router_agent")

    # Create our custom agent wrappers
    event_bus = manager.event_bus

    weather = WeatherAgent(weather_agent, event_bus)
    router = ConversationRouterAgent(router_agent, event_bus)

    # Demonstrate lifecycle
    logger.info("\n--- Starting Weather Agent ---")
    await weather.start()
    await asyncio.sleep(1)

    # Simulate user turn
    logger.info("\n--- Simulating user input ---")
    await weather.on_user_turn_completed(
        "What's the weather like in Seattle?",
        weather_agent.chat_context,
    )

    await asyncio.sleep(1)

    logger.info("\n--- Stopping Weather Agent ---")
    await weather.stop()

    logger.info("\n--- Starting Router Agent ---")
    await router.start()

    # Simulate routing
    await router.on_user_turn_completed(
        "I need help with my order",
        router_agent.chat_context,
    )

    # Check for transfer signal
    transfer_to = router_agent.userdata.get("_transfer_to")
    if transfer_to:
        logger.info(f"\nRouter wants to transfer to: {transfer_to}")

    await router.stop()

    # Cleanup
    await manager.shutdown()

    logger.info("\n" + "=" * 60)
    logger.info("Custom agent demo complete!")


if __name__ == "__main__":
    asyncio.run(main())
