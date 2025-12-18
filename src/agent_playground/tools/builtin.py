"""
Built-in Tools.

Common utility tools that agents can use.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Optional

from .registry import tool, ToolContext

logger = logging.getLogger(__name__)


@tool(description="End the current conversation gracefully")
async def end_conversation(
    context: ToolContext,
    reason: Optional[str] = None,
    goodbye_message: Optional[str] = None,
) -> str:
    """
    End the conversation and close the session.

    Args:
        reason: Optional reason for ending (for logging)
        goodbye_message: Optional custom goodbye message

    Returns:
        Confirmation that conversation is ending
    """
    logger.info(f"Ending conversation. Reason: {reason}")

    # Signal to session that it should close
    context.userdata["_end_conversation"] = True
    context.userdata["_end_reason"] = reason

    return goodbye_message or "Goodbye! The conversation is now ending."


@tool(description="Transfer the conversation to another agent")
async def transfer_to_agent(
    context: ToolContext,
    agent_name: str,
    transfer_message: Optional[str] = None,
) -> str:
    """
    Transfer the user to a different agent.

    Args:
        agent_name: Name of the agent to transfer to
        transfer_message: Message to tell the user about the transfer

    Returns:
        Confirmation of the transfer
    """
    logger.info(f"Transferring to agent: {agent_name}")

    # Signal transfer
    context.userdata["_transfer_to"] = agent_name

    return transfer_message or f"I'm transferring you to {agent_name}. One moment please."


@tool(description="Get the current date and time")
async def get_current_time(
    timezone: str = "UTC",
) -> dict[str, Any]:
    """
    Get the current date and time.

    Args:
        timezone: Timezone name (e.g., "UTC", "America/New_York")

    Returns:
        Dictionary with current time information
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    return {
        "datetime": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "day_of_week": now.strftime("%A"),
        "timezone": timezone,
    }


@tool(description="Search conversation memory for relevant information")
async def search_memory(
    context: ToolContext,
    query: str,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """
    Search the conversation memory for relevant information.

    Args:
        query: Search query
        max_results: Maximum number of results to return

    Returns:
        List of relevant memory entries
    """
    logger.info(f"Searching memory: {query}")

    # Placeholder - in real implementation, would search vector store
    # or conversation history

    return []


@tool(description="Wait for a specified number of seconds")
async def wait(
    seconds: float,
) -> str:
    """
    Pause execution for a specified duration.

    Args:
        seconds: Number of seconds to wait

    Returns:
        Confirmation of wait completion
    """
    import asyncio
    await asyncio.sleep(seconds)
    return f"Waited for {seconds} seconds"


@tool(description="Send a message to the room chat")
async def send_chat_message(
    context: ToolContext,
    message: str,
) -> str:
    """
    Send a text message to the room chat (not spoken).

    Args:
        message: The message to send

    Returns:
        Confirmation that message was sent
    """
    logger.info(f"Sending chat message: {message[:50]}...")

    # Signal to send via data channel
    if "_pending_chat_messages" not in context.userdata:
        context.userdata["_pending_chat_messages"] = []
    context.userdata["_pending_chat_messages"].append(message)

    return "Message sent to chat"


@tool(description="Get information about the current session")
async def get_session_info(
    context: ToolContext,
) -> dict[str, Any]:
    """
    Get information about the current session.

    Returns:
        Dictionary with session metadata
    """
    return {
        "session_id": context.session_id,
        "agent_id": context.agent_id,
        "room_name": context.room_name,
        "participant_identity": context.participant_identity,
    }
