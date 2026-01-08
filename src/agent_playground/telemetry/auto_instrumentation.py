"""
Automatic instrumentation for HTTP, WebSocket, and gRPC communications.
Provides seamless tracing across all network boundaries.
"""

import logging
import json
from typing import Optional, Dict, Any, Callable
from contextlib import asynccontextmanager
import functools

from opentelemetry import trace, propagate, baggage
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient, GrpcInstrumentorServer
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.trace import Status, StatusCode
from opentelemetry.semconv.trace import SpanAttributes

logger = logging.getLogger(__name__)


class WebSocketInstrumentor:
    """
    Custom instrumentor for WebSocket connections.
    Handles trace propagation across WebSocket boundaries.
    """
    
    def __init__(self):
        self.tracer = trace.get_tracer("websocket.instrumentation")
        self._active_connections: Dict[str, trace.Span] = {}
    
    def instrument_client(self, websocket_class):
        """Instrument WebSocket client for outgoing connections."""
        
        original_connect = websocket_class.connect
        original_send = websocket_class.send
        original_receive = websocket_class.receive
        
        @functools.wraps(original_connect)
        async def traced_connect(self, *args, **kwargs):
            with self.tracer.start_as_current_span(
                "websocket.connect",
                kind=trace.SpanKind.CLIENT,
                attributes={
                    SpanAttributes.NET_PEER_NAME: self.host if hasattr(self, 'host') else 'unknown',
                    SpanAttributes.NET_PEER_PORT: self.port if hasattr(self, 'port') else 0,
                    "websocket.protocol": "wss" if hasattr(self, 'secure') and self.secure else "ws",
                }
            ) as span:
                # Inject trace context into connection headers
                headers = kwargs.get('headers', {})
                propagate.inject(headers)
                kwargs['headers'] = headers
                
                try:
                    result = await original_connect(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    
                    # Store connection span for message correlation
                    connection_id = f"{self.host}:{self.port}" if hasattr(self, 'host') else str(id(self))
                    self._active_connections[connection_id] = span
                    
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
        
        @functools.wraps(original_send)
        async def traced_send(self, message, *args, **kwargs):
            with self.tracer.start_as_current_span(
                "websocket.send",
                kind=trace.SpanKind.CLIENT,
                attributes={
                    "message.type": type(message).__name__,
                    "message.size": len(str(message)),
                }
            ) as span:
                # Inject trace context into message if it's JSON
                if isinstance(message, dict):
                    propagate.inject(message)
                    message = json.dumps(message)
                elif isinstance(message, str):
                    try:
                        msg_dict = json.loads(message)
                        propagate.inject(msg_dict)
                        message = json.dumps(msg_dict)
                    except json.JSONDecodeError:
                        pass  # Not JSON, send as-is
                
                try:
                    result = await original_send(message, *args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
        
        @functools.wraps(original_receive)
        async def traced_receive(self, *args, **kwargs):
            with self.tracer.start_as_current_span(
                "websocket.receive",
                kind=trace.SpanKind.CLIENT
            ) as span:
                try:
                    message = await original_receive(*args, **kwargs)
                    
                    # Extract trace context from message if it's JSON
                    if isinstance(message, str):
                        try:
                            msg_dict = json.loads(message)
                            context = propagate.extract(msg_dict)
                            # Link to extracted context if present
                            if context:
                                span.add_link(trace.Link(context))
                        except json.JSONDecodeError:
                            pass
                    
                    span.set_attribute("message.type", type(message).__name__)
                    span.set_attribute("message.size", len(str(message)))
                    span.set_status(Status(StatusCode.OK))
                    
                    return message
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
        
        websocket_class.connect = traced_connect
        websocket_class.send = traced_send
        websocket_class.receive = traced_receive
        
        logger.info("WebSocket client instrumented")
    
    def instrument_server(self, websocket_handler):
        """Instrument WebSocket server for incoming connections."""
        
        @functools.wraps(websocket_handler)
        async def traced_handler(websocket, path=None):
            # Extract trace context from connection headers
            headers = websocket.request_headers if hasattr(websocket, 'request_headers') else {}
            context = propagate.extract(dict(headers))
            
            with self.tracer.start_as_current_span(
                "websocket.connection",
                context=context,
                kind=trace.SpanKind.SERVER,
                attributes={
                    SpanAttributes.NET_HOST_NAME: websocket.host if hasattr(websocket, 'host') else 'unknown',
                    SpanAttributes.NET_HOST_PORT: websocket.port if hasattr(websocket, 'port') else 0,
                    "websocket.path": path or "/",
                }
            ) as span:
                # Store connection span
                connection_id = f"{websocket.remote_address}" if hasattr(websocket, 'remote_address') else str(id(websocket))
                self._active_connections[connection_id] = span
                
                try:
                    # Instrument message handling
                    original_send = websocket.send
                    original_receive = websocket.receive
                    
                    async def traced_send(message):
                        with self.tracer.start_as_current_span(
                            "websocket.server.send",
                            kind=trace.SpanKind.INTERNAL
                        ):
                            # Inject context for client correlation
                            if isinstance(message, dict):
                                propagate.inject(message)
                                message = json.dumps(message)
                            await original_send(message)
                    
                    async def traced_receive():
                        with self.tracer.start_as_current_span(
                            "websocket.server.receive",
                            kind=trace.SpanKind.INTERNAL
                        ) as recv_span:
                            message = await original_receive()
                            
                            # Extract context from message
                            if isinstance(message, str):
                                try:
                                    msg_dict = json.loads(message)
                                    msg_context = propagate.extract(msg_dict)
                                    if msg_context:
                                        recv_span.add_link(trace.Link(msg_context))
                                except json.JSONDecodeError:
                                    pass
                            
                            return message
                    
                    websocket.send = traced_send
                    websocket.receive = traced_receive
                    
                    # Call original handler
                    result = await websocket_handler(websocket, path)
                    span.set_status(Status(StatusCode.OK))
                    return result
                    
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
                finally:
                    # Clean up connection
                    self._active_connections.pop(connection_id, None)
        
        return traced_handler


class LiveKitInstrumentor:
    """
    Specialized instrumentor for LiveKit SDK operations.
    Provides detailed tracing for LiveKit-specific functionality.
    """
    
    def __init__(self):
        self.tracer = trace.get_tracer("livekit.instrumentation")
    
    def instrument_room(self, room_class):
        """Instrument LiveKit Room class."""
        
        # Instrument connect method
        original_connect = room_class.connect
        
        @functools.wraps(original_connect)
        async def traced_connect(self, url: str, token: str, **kwargs):
            with self.tracer.start_as_current_span(
                "livekit.room.connect",
                kind=trace.SpanKind.CLIENT,
                attributes={
                    "livekit.url": url,
                    "livekit.room.name": self.name if hasattr(self, 'name') else 'unknown',
                }
            ) as span:
                try:
                    result = await original_connect(url, token, **kwargs)
                    span.set_attribute("livekit.room.sid", self.sid if hasattr(self, 'sid') else 'unknown')
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
        
        room_class.connect = traced_connect
        
        # Instrument publish method
        if hasattr(room_class, 'publish'):
            original_publish = room_class.publish
            
            @functools.wraps(original_publish)
            async def traced_publish(self, track, **kwargs):
                with self.tracer.start_as_current_span(
                    "livekit.track.publish",
                    kind=trace.SpanKind.CLIENT,
                    attributes={
                        "livekit.track.kind": track.kind if hasattr(track, 'kind') else 'unknown',
                        "livekit.track.source": track.source if hasattr(track, 'source') else 'unknown',
                    }
                ) as span:
                    try:
                        result = await original_publish(track, **kwargs)
                        if hasattr(track, 'sid'):
                            span.set_attribute("livekit.track.sid", track.sid)
                        span.set_status(Status(StatusCode.OK))
                        return result
                    except Exception as e:
                        span.record_exception(e)
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        raise
            
            room_class.publish = traced_publish


class AutoInstrumentor:
    """
    Main class for automatic instrumentation of all communication protocols.
    """
    
    def __init__(self):
        self.websocket_instrumentor = WebSocketInstrumentor()
        self.livekit_instrumentor = LiveKitInstrumentor()
        self._instrumented = False
    
    def instrument_all(
        self,
        enable_fastapi: bool = True,
        enable_httpx: bool = True,
        enable_grpc: bool = True,
        enable_logging: bool = True,
        enable_websocket: bool = True,
        enable_livekit: bool = True,
    ):
        """
        Instrument all supported libraries and protocols.
        
        Args:
            enable_fastapi: Instrument FastAPI for HTTP server
            enable_httpx: Instrument HTTPX for HTTP client
            enable_grpc: Instrument gRPC client and server
            enable_logging: Instrument Python logging
            enable_websocket: Instrument WebSocket connections
            enable_livekit: Instrument LiveKit SDK
        """
        if self._instrumented:
            logger.warning("Already instrumented")
            return
        
        # FastAPI instrumentation (HTTP server)
        if enable_fastapi:
            try:
                FastAPIInstrumentor.instrument(
                    tracer_provider=trace.get_tracer_provider(),
                    excluded_urls="/healthz,/metrics",  # Exclude health/metrics endpoints
                )
                logger.info("FastAPI instrumented")
            except Exception as e:
                logger.warning(f"Failed to instrument FastAPI: {e}")
        
        # HTTPX instrumentation (HTTP client)
        if enable_httpx:
            try:
                HTTPXClientInstrumentor().instrument(
                    tracer_provider=trace.get_tracer_provider(),
                )
                logger.info("HTTPX client instrumented")
            except Exception as e:
                logger.warning(f"Failed to instrument HTTPX: {e}")
        
        # gRPC instrumentation
        if enable_grpc:
            try:
                # Client instrumentation
                grpc_client_instrumentor = GrpcInstrumentorClient()
                grpc_client_instrumentor.instrument(
                    tracer_provider=trace.get_tracer_provider(),
                )
                
                # Server instrumentation
                grpc_server_instrumentor = GrpcInstrumentorServer()
                grpc_server_instrumentor.instrument(
                    tracer_provider=trace.get_tracer_provider(),
                )
                
                logger.info("gRPC client and server instrumented")
            except Exception as e:
                logger.warning(f"Failed to instrument gRPC: {e}")
        
        # Logging instrumentation (correlates logs with traces)
        if enable_logging:
            try:
                LoggingInstrumentor().instrument(
                    tracer_provider=trace.get_tracer_provider(),
                    set_logging_format=True,
                )
                logger.info("Python logging instrumented")
            except Exception as e:
                logger.warning(f"Failed to instrument logging: {e}")
        
        # WebSocket instrumentation
        if enable_websocket:
            try:
                # Import and instrument WebSocket libraries if available
                try:
                    import websockets
                    self.websocket_instrumentor.instrument_client(websockets.WebSocketClientProtocol)
                    logger.info("WebSocket client (websockets) instrumented")
                except ImportError:
                    pass
                
                try:
                    import aiohttp
                    # Instrument aiohttp WebSocket if needed
                    logger.info("WebSocket (aiohttp) instrumentation available")
                except ImportError:
                    pass
                    
            except Exception as e:
                logger.warning(f"Failed to instrument WebSocket: {e}")
        
        # LiveKit instrumentation
        if enable_livekit:
            try:
                from livekit import rtc
                self.livekit_instrumentor.instrument_room(rtc.Room)
                logger.info("LiveKit SDK instrumented")
            except Exception as e:
                logger.warning(f"Failed to instrument LiveKit: {e}")
        
        self._instrumented = True
        logger.info("Auto-instrumentation complete")
    
    def uninstrument_all(self):
        """Remove all instrumentation."""
        
        try:
            FastAPIInstrumentor.uninstrument()
            HTTPXClientInstrumentor().uninstrument()
            LoggingInstrumentor().uninstrument()
            # Note: Some instrumentors don't support uninstrument
            
            self._instrumented = False
            logger.info("Instrumentation removed")
        except Exception as e:
            logger.error(f"Failed to uninstrument: {e}")


# Global singleton
_auto_instrumentor: Optional[AutoInstrumentor] = None


def get_auto_instrumentor() -> AutoInstrumentor:
    """Get or create the auto-instrumentor singleton."""
    global _auto_instrumentor
    if _auto_instrumentor is None:
        _auto_instrumentor = AutoInstrumentor()
    return _auto_instrumentor


def instrument_application():
    """
    Convenience function to instrument the entire application.
    Call this early in your application startup.
    """
    instrumentor = get_auto_instrumentor()
    instrumentor.instrument_all()


# Middleware for trace context injection in HTTP responses
class TraceContextMiddleware:
    """
    ASGI middleware that adds trace context to HTTP responses.
    Useful for frontend correlation.
    """
    
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            span = trace.get_current_span()
            
            async def send_wrapper(message):
                if message["type"] == "http.response.start" and span and span.is_recording():
                    headers = message.setdefault("headers", [])
                    
                    # Add trace ID to response headers
                    context = span.get_span_context()
                    headers.append((b"x-trace-id", format(context.trace_id, '032x').encode()))
                    headers.append((b"x-span-id", format(context.span_id, '016x').encode()))
                    
                    # Add baggage items
                    for key, value in baggage.get_all().items():
                        headers.append((f"x-baggage-{key}".encode(), str(value).encode()))
                
                await send(message)
            
            await self.app(scope, receive, send_wrapper)
        else:
            await self.app(scope, receive, send)