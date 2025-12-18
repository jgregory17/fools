"""
Event Bus for pub/sub communication between pipeline modules.

Provides:
- Type-safe event subscription
- Async event handlers
- Event filtering
- Tracing/logging hooks
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine, Optional, Type, TypeVar

from .events import AgentEvent

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=AgentEvent)
EventHandler = Callable[[AgentEvent], Coroutine[Any, Any, None]]


class EventBus:
    """
    Async pub/sub event bus for pipeline communication.

    Example usage:
        bus = EventBus()

        # Subscribe to specific event types
        @bus.on(TranscriptFinal)
        async def handle_transcript(event: TranscriptFinal):
            print(f"User said: {event.text}")

        # Subscribe to all events
        @bus.on_all
        async def log_all(event: AgentEvent):
            logger.debug(f"Event: {event}")

        # Publish events
        await bus.emit(TranscriptFinal(text="Hello"))
    """

    def __init__(self, name: str = "default"):
        self.name = name
        self._handlers: dict[Type[AgentEvent], list[EventHandler]] = defaultdict(list)
        self._global_handlers: list[EventHandler] = []
        self._trace_handlers: list[Callable[[AgentEvent], None]] = []
        self._running = True

    def on(
        self,
        event_type: Type[T],
    ) -> Callable[[Callable[[T], Coroutine[Any, Any, None]]], Callable[[T], Coroutine[Any, Any, None]]]:
        """
        Decorator to subscribe to a specific event type.

        Args:
            event_type: The event class to subscribe to

        Example:
            @bus.on(TranscriptFinal)
            async def handle(event: TranscriptFinal):
                ...
        """
        def decorator(
            handler: Callable[[T], Coroutine[Any, Any, None]],
        ) -> Callable[[T], Coroutine[Any, Any, None]]:
            self.subscribe(event_type, handler)  # type: ignore
            return handler
        return decorator

    def on_all(
        self,
        handler: Callable[[AgentEvent], Coroutine[Any, Any, None]],
    ) -> Callable[[AgentEvent], Coroutine[Any, Any, None]]:
        """
        Decorator to subscribe to all events.

        Example:
            @bus.on_all
            async def handle(event: AgentEvent):
                ...
        """
        self._global_handlers.append(handler)
        return handler

    def subscribe(
        self,
        event_type: Type[AgentEvent],
        handler: EventHandler,
    ) -> None:
        """Subscribe a handler to a specific event type."""
        self._handlers[event_type].append(handler)

    def unsubscribe(
        self,
        event_type: Type[AgentEvent],
        handler: EventHandler,
    ) -> None:
        """Unsubscribe a handler from an event type."""
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe to all events."""
        self._global_handlers.append(handler)

    def unsubscribe_all(self, handler: EventHandler) -> None:
        """Unsubscribe from all events."""
        if handler in self._global_handlers:
            self._global_handlers.remove(handler)

    def add_trace_handler(
        self,
        handler: Callable[[AgentEvent], None],
    ) -> None:
        """
        Add a synchronous trace handler (for logging/metrics).

        Trace handlers are called synchronously before async handlers.
        """
        self._trace_handlers.append(handler)

    async def emit(self, event: AgentEvent) -> None:
        """
        Emit an event to all subscribers.

        Events are delivered to:
        1. Trace handlers (sync, for logging)
        2. Type-specific handlers (async)
        3. Global handlers (async)

        Handlers run concurrently for performance.
        """
        if not self._running:
            return

        # Sync trace handlers first
        for trace_handler in self._trace_handlers:
            try:
                trace_handler(event)
            except Exception as e:
                logger.exception(f"Trace handler error: {e}")

        # Collect all async handlers
        handlers: list[EventHandler] = []

        # Type-specific handlers
        event_type = type(event)
        handlers.extend(self._handlers.get(event_type, []))

        # Also match parent classes (for polymorphic handling)
        for registered_type, type_handlers in self._handlers.items():
            if registered_type != event_type and isinstance(event, registered_type):
                handlers.extend(type_handlers)

        # Global handlers
        handlers.extend(self._global_handlers)

        if not handlers:
            return

        # Run all handlers concurrently
        tasks = [self._safe_call(handler, event) for handler in handlers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_call(self, handler: EventHandler, event: AgentEvent) -> None:
        """Call a handler with exception handling."""
        try:
            await handler(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Event handler error for {type(event).__name__}: {e}")

    def stop(self) -> None:
        """Stop the event bus (no more events will be delivered)."""
        self._running = False

    def start(self) -> None:
        """Restart the event bus after stopping."""
        self._running = True

    def clear(self) -> None:
        """Remove all handlers."""
        self._handlers.clear()
        self._global_handlers.clear()
        self._trace_handlers.clear()


class FilteredEventBus:
    """
    Event bus wrapper with filtering capabilities.

    Useful for routing events to specific agents or modules
    based on agent_id or other criteria.
    """

    def __init__(
        self,
        bus: EventBus,
        filter_fn: Callable[[AgentEvent], bool],
    ):
        self._bus = bus
        self._filter_fn = filter_fn

    async def emit(self, event: AgentEvent) -> None:
        """Emit event only if it passes the filter."""
        if self._filter_fn(event):
            await self._bus.emit(event)

    def on(
        self,
        event_type: Type[T],
    ) -> Callable[[Callable[[T], Coroutine[Any, Any, None]]], Callable[[T], Coroutine[Any, Any, None]]]:
        """Subscribe with automatic filtering."""
        def decorator(
            handler: Callable[[T], Coroutine[Any, Any, None]],
        ) -> Callable[[T], Coroutine[Any, Any, None]]:
            async def filtered_handler(event: AgentEvent) -> None:
                if self._filter_fn(event):
                    await handler(event)  # type: ignore
            self._bus.subscribe(event_type, filtered_handler)
            return handler
        return decorator


def create_agent_bus(bus: EventBus, agent_id: str) -> FilteredEventBus:
    """Create a filtered bus for a specific agent."""
    return FilteredEventBus(
        bus,
        lambda event: event.agent_id == agent_id or event.agent_id is None,
    )
