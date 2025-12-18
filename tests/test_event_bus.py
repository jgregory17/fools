"""Tests for the event bus."""

import asyncio
import pytest
from agent_playground.core.event_bus import EventBus
from agent_playground.core.events import TranscriptFinal, LLMToken, AgentEvent


@pytest.mark.asyncio
async def test_event_subscription():
    """Test basic event subscription and emission."""
    bus = EventBus()
    received = []

    @bus.on(TranscriptFinal)
    async def handler(event: TranscriptFinal):
        received.append(event)

    event = TranscriptFinal(text="Hello world")
    await bus.emit(event)

    assert len(received) == 1
    assert received[0].text == "Hello world"


@pytest.mark.asyncio
async def test_global_subscription():
    """Test subscribing to all events."""
    bus = EventBus()
    received = []

    @bus.on_all
    async def handler(event: AgentEvent):
        received.append(event)

    await bus.emit(TranscriptFinal(text="Hello"))
    await bus.emit(LLMToken(token="world"))

    assert len(received) == 2


@pytest.mark.asyncio
async def test_unsubscribe():
    """Test unsubscribing from events."""
    bus = EventBus()
    received = []

    async def handler(event: TranscriptFinal):
        received.append(event)

    bus.subscribe(TranscriptFinal, handler)
    await bus.emit(TranscriptFinal(text="1"))

    bus.unsubscribe(TranscriptFinal, handler)
    await bus.emit(TranscriptFinal(text="2"))

    assert len(received) == 1
    assert received[0].text == "1"


@pytest.mark.asyncio
async def test_concurrent_handlers():
    """Test that multiple handlers run concurrently."""
    bus = EventBus()
    order = []

    @bus.on(TranscriptFinal)
    async def slow_handler(event: TranscriptFinal):
        await asyncio.sleep(0.1)
        order.append("slow")

    @bus.on(TranscriptFinal)
    async def fast_handler(event: TranscriptFinal):
        order.append("fast")

    await bus.emit(TranscriptFinal(text="test"))

    # Fast should complete first due to concurrent execution
    assert order[0] == "fast"
    assert order[1] == "slow"


@pytest.mark.asyncio
async def test_handler_exception_handling():
    """Test that exceptions in handlers don't stop other handlers."""
    bus = EventBus()
    received = []

    @bus.on(TranscriptFinal)
    async def bad_handler(event: TranscriptFinal):
        raise ValueError("Oops")

    @bus.on(TranscriptFinal)
    async def good_handler(event: TranscriptFinal):
        received.append(event)

    # Should not raise
    await bus.emit(TranscriptFinal(text="test"))

    # Good handler should still run
    assert len(received) == 1


@pytest.mark.asyncio
async def test_stop_and_start():
    """Test stopping and starting the event bus."""
    bus = EventBus()
    received = []

    @bus.on(TranscriptFinal)
    async def handler(event: TranscriptFinal):
        received.append(event)

    await bus.emit(TranscriptFinal(text="1"))
    bus.stop()
    await bus.emit(TranscriptFinal(text="2"))
    bus.start()
    await bus.emit(TranscriptFinal(text="3"))

    assert len(received) == 2
    assert received[0].text == "1"
    assert received[1].text == "3"
