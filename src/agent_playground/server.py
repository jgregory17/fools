"""
HTTP API Server for Agent Playground.

Provides endpoints for:
- /healthz - Health check for container orchestration
- /readyz - Readiness check (components initialized)
- /metrics - Prometheus metrics endpoint
- /api/token - LiveKit access token generation with permissions & attributes
- /api/agents - List available agent configurations
- /api/room/metadata - Update room metadata (server-side only)
- /api/participant/attributes - Update participant attributes

Token endpoint supports:
- Fine-grained permissions (canPublish, canSubscribe, canPublishData, etc.)
- Initial participant metadata (JSON, 64KB max)
- Initial participant attributes (key-value pairs)
- Hidden participants for observers/moderators

Usage:
    from agent_playground.server import create_app, run_server

    app = create_app(agent_manager)
    await run_server(app, host="0.0.0.0", port=8080)
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Any, Dict, Optional, TYPE_CHECKING

logger = logging.getLogger(__name__)

# Try to import aiohttp; provide fallback info if not available
try:
    from aiohttp import web
    import aiohttp_cors
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None
    aiohttp_cors = None
    logger.warning("aiohttp not installed. HTTP server disabled. Install: pip install aiohttp aiohttp-cors")

# Try to import livekit-api for token generation
try:
    from livekit import api as livekit_api
    LIVEKIT_API_AVAILABLE = True
except ImportError:
    LIVEKIT_API_AVAILABLE = False
    livekit_api = None
    logger.warning("livekit-api not installed. Token endpoint disabled. Install: pip install livekit-api")

from .core.metrics import get_metrics, MetricsCollector

if TYPE_CHECKING:
    from .core.agent_manager import AgentManager


class HealthStatus:
    """Track health status of various components."""

    def __init__(self):
        self.components: Dict[str, bool] = {}
        self.details: Dict[str, str] = {}

    def set_healthy(self, component: str, detail: str = "OK") -> None:
        self.components[component] = True
        self.details[component] = detail

    def set_unhealthy(self, component: str, detail: str) -> None:
        self.components[component] = False
        self.details[component] = detail

    @property
    def is_healthy(self) -> bool:
        return all(self.components.values()) if self.components else True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "healthy": self.is_healthy,
            "components": {
                name: {"healthy": healthy, "detail": self.details.get(name, "")}
                for name, healthy in self.components.items()
            },
        }


class AgentServer:
    """
    HTTP server for agent management and observability.

    Provides Prometheus metrics, health checks, and API endpoints.
    """

    def __init__(
        self,
        agent_manager: Optional["AgentManager"] = None,
        metrics: Optional[MetricsCollector] = None,
        agent_id: Optional[str] = None,
    ):
        """
        Initialize the server.

        Args:
            agent_manager: Optional agent manager instance for management endpoints
            metrics: Optional custom metrics collector
            agent_id: Agent ID (defaults to hostname-derived ID)
        """
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp not installed. Install: pip install aiohttp")

        self.agent_manager = agent_manager
        self.metrics = metrics or get_metrics()
        self.agent_id = agent_id or self._derive_agent_id()
        self.health = HealthStatus()
        self._ready = False
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        logger.info(f"AgentServer initialized with agent_id={self.agent_id}")

    def _derive_agent_id(self) -> str:
        """Derive agent ID from environment or hostname."""
        # Check environment variables (for Docker/K8s)
        agent_id = os.environ.get("AGENT_ID")
        if agent_id:
            return agent_id

        # Use hostname (works for docker-compose --scale)
        hostname = os.environ.get("HOSTNAME", socket.gethostname())
        return f"agent-{hostname}"

    def set_ready(self, ready: bool = True) -> None:
        """Set readiness state."""
        self._ready = ready

    async def _handle_health(self, request: web.Request) -> web.Response:
        """
        Health check endpoint (/healthz).

        Returns 200 if server is running, regardless of component health.
        Used by container orchestration for liveness probes.
        """
        return web.json_response({
            "status": "ok",
            "agent_id": self.agent_id,
        })

    async def _handle_ready(self, request: web.Request) -> web.Response:
        """
        Readiness check endpoint (/readyz).

        Returns 200 only if all components are healthy and ready.
        Used by load balancers and orchestration for readiness probes.
        """
        # Check agent manager if available
        if self.agent_manager:
            try:
                # Update health based on active agents
                active = len(self.agent_manager._agents) if hasattr(self.agent_manager, '_agents') else 0
                self.health.set_healthy("agent_manager", f"{active} agents")
            except Exception as e:
                self.health.set_unhealthy("agent_manager", str(e))

        # Check LLM backend health if available
        await self._check_llm_health()

        health_data = self.health.to_dict()
        health_data["ready"] = self._ready
        health_data["agent_id"] = self.agent_id

        status = 200 if (self._ready and self.health.is_healthy) else 503
        return web.json_response(health_data, status=status)

    async def _check_llm_health(self) -> None:
        """Check LLM backend health."""
        try:
            import httpx
            ollama_url = os.environ.get("LLM_BASE_URL", "http://localhost:11434")
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{ollama_url}/api/tags")
                if resp.status_code == 200:
                    self.health.set_healthy("llm", "ollama connected")
                else:
                    self.health.set_unhealthy("llm", f"ollama status {resp.status_code}")
        except Exception as e:
            self.health.set_unhealthy("llm", f"ollama: {e}")

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """
        Prometheus metrics endpoint (/metrics).

        Returns metrics in Prometheus exposition format.
        """
        content = self.metrics.generate_metrics()
        return web.Response(
            body=content,
            content_type=self.metrics.get_content_type(),
        )

    async def _handle_info(self, request: web.Request) -> web.Response:
        """
        Agent info endpoint (/api/v1/info).

        Returns agent configuration and status.
        """
        info = {
            "agent_id": self.agent_id,
            "version": "1.0.0",
            "hostname": socket.gethostname(),
            "metrics_enabled": self.metrics.enabled,
        }

        if self.agent_manager:
            info["agents"] = list(self.agent_manager._agents.keys()) if hasattr(self.agent_manager, '_agents') else []
            info["config_count"] = len(self.agent_manager._configs) if hasattr(self.agent_manager, '_configs') else 0

        return web.json_response(info)

    async def _handle_agents(self, request: web.Request) -> web.Response:
        """
        List available agents endpoint (/api/agents).

        Returns list of available agent configurations with metadata.
        """
        if not self.agent_manager:
            return web.json_response({
                "agents": [],
                "default": None,
                "error": "Agent manager not configured"
            })

        agents = []
        default_agent = None

        # Get configs from agent manager
        configs = getattr(self.agent_manager, '_configs', {})

        for name, config in configs.items():
            agent_info = {
                "name": name,
                "description": config.description or f"Voice agent: {name}",
                "instructions": config.instructions[:200] + "..." if len(config.instructions) > 200 else config.instructions,
                "tags": config.tags,
                "version": config.version,
                "greeting": config.behavior.greeting,
                "modules": {
                    "asr": config.asr.backend,
                    "llm": config.llm.backend,
                    "tts": config.tts.backend,
                },
            }
            agents.append(agent_info)

            # Use first config with "default" tag or just first one
            if default_agent is None:
                if "default" in config.tags:
                    default_agent = name
                elif not default_agent:
                    default_agent = name

        # Sort by name for consistent ordering
        agents.sort(key=lambda x: x["name"])

        # If no default found, use first agent
        if default_agent is None and agents:
            default_agent = agents[0]["name"]

        return web.json_response({
            "agents": agents,
            "default": default_agent,
            "count": len(agents),
        })

    async def _handle_token(self, request: web.Request) -> web.Response:
        """
        LiveKit token endpoint (/api/token).

        Query parameters:
            - room: Room name to join (required)
            - identity: User identity (required)
            - name: Display name (optional, defaults to identity)
            - agent: Agent configuration name (optional, used to spawn specific agent)
            - metadata: JSON metadata string (optional, 64KB max)
            - attributes: JSON object of key-value attributes (optional)
            - permissions: Comma-separated permissions (optional)
                Options: publish, subscribe, data, updateMetadata
                Default: publish,subscribe,data
            - hidden: Make participant invisible to others (optional, default: false)

        Returns:
            JSON with token, room, identity, permissions, and agent info

        Examples:
            # Basic token
            GET /api/token?room=my-room&identity=user-123

            # With custom permissions (subscribe-only observer)
            GET /api/token?room=my-room&identity=observer&permissions=subscribe&hidden=true

            # With metadata and attributes
            GET /api/token?room=my-room&identity=user-123&metadata={"role":"admin"}&attributes={"language":"es"}
        """
        import json

        if not LIVEKIT_API_AVAILABLE:
            return web.json_response(
                {"error": "livekit-api not installed"},
                status=501
            )

        # Get LiveKit credentials from environment
        api_key = os.environ.get("LIVEKIT_API_KEY", "devkey")
        api_secret = os.environ.get("LIVEKIT_API_SECRET", "secret")

        # Get required parameters
        room = request.query.get("room")
        identity = request.query.get("identity")

        if not room or not identity:
            return web.json_response(
                {"error": "room and identity query parameters are required"},
                status=400
            )

        # Get optional parameters
        name = request.query.get("name", identity)
        agent_name = request.query.get("agent")
        metadata_str = request.query.get("metadata")
        attributes_str = request.query.get("attributes")
        permissions_str = request.query.get("permissions", "publish,subscribe,data")
        hidden = request.query.get("hidden", "false").lower() == "true"

        # Parse permissions
        permissions = set(p.strip().lower() for p in permissions_str.split(",") if p.strip())
        can_publish = "publish" in permissions
        can_subscribe = "subscribe" in permissions
        can_publish_data = "data" in permissions
        can_update_own_metadata = "updatemetadata" in permissions or "updateownmetadata" in permissions

        # Parse attributes
        attributes = {}
        if attributes_str:
            try:
                attributes = json.loads(attributes_str)
                if not isinstance(attributes, dict):
                    return web.json_response(
                        {"error": "attributes must be a JSON object"},
                        status=400
                    )
                # Ensure all values are strings
                attributes = {k: str(v) for k, v in attributes.items()}
            except json.JSONDecodeError as e:
                return web.json_response(
                    {"error": f"Invalid attributes JSON: {e}"},
                    status=400
                )

        # Parse/build metadata
        metadata = {}
        if metadata_str:
            try:
                metadata = json.loads(metadata_str)
            except json.JSONDecodeError as e:
                return web.json_response(
                    {"error": f"Invalid metadata JSON: {e}"},
                    status=400
                )

        # Add agent to metadata if specified
        if agent_name:
            metadata["agent"] = agent_name

        # Validate agent config if specified
        agent_config = None
        if agent_name and self.agent_manager:
            configs = getattr(self.agent_manager, '_configs', {})
            if agent_name not in configs:
                available = list(configs.keys())
                return web.json_response(
                    {"error": f"Unknown agent '{agent_name}'. Available: {available}"},
                    status=400
                )
            agent_config = configs[agent_name]

        try:
            # Create access token with fine-grained permissions
            token = livekit_api.AccessToken(api_key, api_secret) \
                .with_identity(identity) \
                .with_name(name) \
                .with_grants(livekit_api.VideoGrants(
                    room_join=True,
                    room=room,
                    can_publish=can_publish,
                    can_subscribe=can_subscribe,
                    can_publish_data=can_publish_data,
                    can_update_own_metadata=can_update_own_metadata,
                    hidden=hidden,
                ))

            # Add metadata if present
            if metadata:
                token.with_metadata(json.dumps(metadata))

            # Add attributes if present
            if attributes:
                token.with_attributes(attributes)

            jwt_token = token.to_jwt()

            logger.info(
                f"Generated token: room={room}, identity={identity}, "
                f"permissions={permissions_str}, hidden={hidden}, agent={agent_name or 'default'}"
            )

            response_data = {
                "token": jwt_token,
                "room": room,
                "identity": identity,
                "name": name,
                "permissions": {
                    "canPublish": can_publish,
                    "canSubscribe": can_subscribe,
                    "canPublishData": can_publish_data,
                    "canUpdateOwnMetadata": can_update_own_metadata,
                    "hidden": hidden,
                },
            }

            # Include metadata/attributes in response if set
            if metadata:
                response_data["metadata"] = metadata
            if attributes:
                response_data["attributes"] = attributes

            # Include agent info in response
            if agent_name and agent_config:
                response_data["agent"] = {
                    "name": agent_name,
                    "description": agent_config.description,
                    "greeting": agent_config.behavior.greeting,
                }

            return web.json_response(response_data)

        except Exception as e:
            logger.error(f"Failed to generate token: {e}")
            return web.json_response(
                {"error": f"Failed to generate token: {str(e)}"},
                status=500
            )

    async def _handle_room_metadata(self, request: web.Request) -> web.Response:
        """
        Update room metadata endpoint (POST /api/room/metadata).

        Room metadata is visible to all participants and can only be set server-side.
        Use for shared room state like game state, shared context, or configuration.

        Request body (JSON):
            - room: Room name (required)
            - metadata: JSON string or object to set as metadata (required)

        Returns:
            JSON with success status and updated metadata
        """
        import json

        if not LIVEKIT_API_AVAILABLE:
            return web.json_response(
                {"error": "livekit-api not installed"},
                status=501
            )

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400
            )

        room_name = body.get("room")
        metadata = body.get("metadata")

        if not room_name:
            return web.json_response(
                {"error": "room is required"},
                status=400
            )

        if metadata is None:
            return web.json_response(
                {"error": "metadata is required"},
                status=400
            )

        # Convert to string if dict/list
        if isinstance(metadata, (dict, list)):
            metadata_str = json.dumps(metadata)
        else:
            metadata_str = str(metadata)

        # Check size limit (64KB)
        if len(metadata_str.encode('utf-8')) > 65536:
            return web.json_response(
                {"error": "metadata exceeds 64KB limit"},
                status=400
            )

        try:
            # Import protocol classes for request objects
            from livekit.protocol.room import UpdateRoomMetadataRequest

            # Use RoomService API to update metadata
            livekit_url = os.environ.get("LIVEKIT_URL", "http://localhost:7880")
            api_key = os.environ.get("LIVEKIT_API_KEY", "devkey")
            api_secret = os.environ.get("LIVEKIT_API_SECRET", "secret")

            lk_api = livekit_api.LiveKitAPI(livekit_url, api_key, api_secret)
            await lk_api.room.update_room_metadata(
                UpdateRoomMetadataRequest(
                    room=room_name,
                    metadata=metadata_str,
                )
            )

            logger.info(f"Updated room metadata: room={room_name}")

            return web.json_response({
                "success": True,
                "room": room_name,
                "metadata": metadata,
            })

        except Exception as e:
            logger.error(f"Failed to update room metadata: {e}")
            return web.json_response(
                {"error": f"Failed to update room metadata: {str(e)}"},
                status=500
            )

    async def _handle_participant_attributes(self, request: web.Request) -> web.Response:
        """
        Update participant attributes endpoint (POST /api/participant/attributes).

        Participant attributes are key-value pairs synced to all participants.
        Use for agent state, mood, persona, or context flags visible to UI.

        Request body (JSON):
            - room: Room name (required)
            - identity: Participant identity (required)
            - attributes: Object of key-value pairs to set/update (required)

        Returns:
            JSON with success status and updated attributes

        Note: To delete an attribute, set its value to empty string "".
        """
        import json

        if not LIVEKIT_API_AVAILABLE:
            return web.json_response(
                {"error": "livekit-api not installed"},
                status=501
            )

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400
            )

        room_name = body.get("room")
        identity = body.get("identity")
        attributes = body.get("attributes")

        if not room_name or not identity:
            return web.json_response(
                {"error": "room and identity are required"},
                status=400
            )

        if not attributes or not isinstance(attributes, dict):
            return web.json_response(
                {"error": "attributes must be a non-empty object"},
                status=400
            )

        # Convert all values to strings
        attributes = {k: str(v) for k, v in attributes.items()}

        try:
            # Import protocol classes for request objects
            from livekit.protocol.room import UpdateParticipantRequest

            # Use RoomService API to update participant
            livekit_url = os.environ.get("LIVEKIT_URL", "http://localhost:7880")
            api_key = os.environ.get("LIVEKIT_API_KEY", "devkey")
            api_secret = os.environ.get("LIVEKIT_API_SECRET", "secret")

            lk_api = livekit_api.LiveKitAPI(livekit_url, api_key, api_secret)
            await lk_api.room.update_participant(
                UpdateParticipantRequest(
                    room=room_name,
                    identity=identity,
                    attributes=attributes,
                )
            )

            logger.info(f"Updated participant attributes: room={room_name}, identity={identity}")

            return web.json_response({
                "success": True,
                "room": room_name,
                "identity": identity,
                "attributes": attributes,
            })

        except Exception as e:
            logger.error(f"Failed to update participant attributes: {e}")
            return web.json_response(
                {"error": f"Failed to update participant attributes: {str(e)}"},
                status=500
            )

    def create_app(self) -> web.Application:
        """Create the aiohttp application with all routes."""
        app = web.Application()

        # Health endpoints
        app.router.add_get("/healthz", self._handle_health)
        app.router.add_get("/readyz", self._handle_ready)
        app.router.add_get("/health", self._handle_health)  # Alias

        # Metrics endpoint
        app.router.add_get("/metrics", self._handle_metrics)

        # API endpoints
        app.router.add_get("/api/v1/info", self._handle_info)
        app.router.add_get("/api/agents", self._handle_agents)
        app.router.add_get("/api/token", self._handle_token)

        # Room/Participant state management (server-side)
        app.router.add_post("/api/room/metadata", self._handle_room_metadata)
        app.router.add_post("/api/participant/attributes", self._handle_participant_attributes)

        # Setup CORS for frontend
        if aiohttp_cors:
            cors = aiohttp_cors.setup(app, defaults={
                "*": aiohttp_cors.ResourceOptions(
                    allow_credentials=True,
                    expose_headers="*",
                    allow_headers="*",
                    allow_methods=["GET", "POST", "OPTIONS"],
                )
            })
            # Apply CORS to all routes
            for route in list(app.router.routes()):
                cors.add(route)
            logger.info("CORS enabled for all origins")

        # Store reference
        self._app = app
        return app

    async def start(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """Start the HTTP server."""
        if self._app is None:
            self.create_app()

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()

        logger.info(f"Server started on http://{host}:{port}")
        logger.info(f"  Health: http://{host}:{port}/healthz")
        logger.info(f"  Metrics: http://{host}:{port}/metrics")

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("Server stopped")


async def create_app(
    agent_manager: Optional["AgentManager"] = None,
    metrics: Optional[MetricsCollector] = None,
    agent_id: Optional[str] = None,
) -> web.Application:
    """
    Create an aiohttp application with all routes configured.

    This is a convenience function for ASGI deployment.
    """
    server = AgentServer(agent_manager, metrics, agent_id)
    return server.create_app()


async def run_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    agent_manager: Optional["AgentManager"] = None,
    metrics: Optional[MetricsCollector] = None,
    agent_id: Optional[str] = None,
) -> None:
    """
    Run the HTTP server until cancelled.

    This is a convenience function for standalone deployment.
    """
    server = AgentServer(agent_manager, metrics, agent_id)
    await server.start(host, port)

    try:
        # Run forever until cancelled
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()


# CLI entry point
def main():
    """Run the server from command line."""
    import argparse

    parser = argparse.ArgumentParser(description="Agent Playground HTTP Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    parser.add_argument("--agent-id", help="Agent ID (defaults to hostname)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    asyncio.run(run_server(
        host=args.host,
        port=args.port,
        agent_id=args.agent_id,
    ))


if __name__ == "__main__":
    main()
