"""
Simple Voice Agent.

A basic voice agent that demonstrates the framework.
Good starting point for new agents.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base_agent import BasePlaygroundAgent
from ..core.interfaces import ChatContext

logger = logging.getLogger(__name__)


class SimpleVoiceAgent(BasePlaygroundAgent):
    """
    Simple voice agent with basic conversation capabilities.

    Features:
    - Configurable greeting
    - Conversation history management
    - Tool support

    Use as a starting point for more complex agents.
    """

    async def on_enter(self) -> None:
        """Greet the user when session starts."""
        greeting = self.agent.config.behavior.greeting
        if greeting:
            logger.info(f"Greeting user: {greeting}")
            await self.say(greeting)
        else:
            await self.say("Hello! I'm ready to help.")

    async def on_exit(self) -> None:
        """Say goodbye when session ends."""
        logger.info("Agent session ending")
        # Could save conversation summary here

    async def on_user_turn_completed(
        self,
        transcript: str,
        context: ChatContext,
    ) -> None:
        """Process user input before LLM inference."""
        logger.debug(f"User said: {transcript}")

        # Check for exit commands
        lower = transcript.lower().strip()
        if any(phrase in lower for phrase in ["goodbye", "bye", "exit", "quit"]):
            self.agent.userdata["_should_exit"] = True

        # Add any preprocessing here (RAG, filtering, etc.)


class GreetingAgent(BasePlaygroundAgent):
    """
    Agent specialized for initial greeting and routing.

    Handles:
    - Welcome message
    - Intent detection
    - Transfer to appropriate specialist agent
    """

    async def on_enter(self) -> None:
        """Provide warm welcome."""
        welcome = """
        Hello and welcome! I'm here to help you today.
        I can assist with general questions, or connect you
        with a specialist if needed. How can I help?
        """
        await self.say(welcome.strip())

    async def on_user_turn_completed(
        self,
        transcript: str,
        context: ChatContext,
    ) -> None:
        """Detect intent and potentially transfer."""
        # Simple keyword-based routing
        lower = transcript.lower()

        if "support" in lower or "help" in lower or "problem" in lower:
            context.add_system_message(
                "User needs support. Be helpful and empathetic."
            )
        elif "sales" in lower or "buy" in lower or "purchase" in lower:
            context.add_system_message(
                "User is interested in purchasing. Be informative about products."
            )


class SupportAgent(BasePlaygroundAgent):
    """
    Agent specialized for customer support.

    Features:
    - Empathetic responses
    - Issue tracking
    - Escalation capabilities
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._issue_count = 0

    async def on_enter(self) -> None:
        """Introduce support capabilities."""
        await self.say(
            "I'm here to help resolve any issues you're experiencing. "
            "Please describe what's happening."
        )

    async def on_user_turn_completed(
        self,
        transcript: str,
        context: ChatContext,
    ) -> None:
        """Track issues and add support context."""
        self._issue_count += 1

        # Add support-specific context
        context.add_system_message(
            "You are a helpful support agent. Be empathetic and solution-focused. "
            f"This is issue #{self._issue_count} in this session."
        )

        # Check for escalation keywords
        if any(word in transcript.lower() for word in ["manager", "supervisor", "escalate"]):
            self.agent.userdata["_needs_escalation"] = True
            context.add_system_message(
                "User wants to escalate. Acknowledge their request and prepare for handoff."
            )
