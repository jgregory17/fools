"""
Agent Manager - Orchestrates multiple agents with different configurations.

Provides:
- Agent lifecycle management (create, start, stop, reload)
- Config-driven agent instantiation
- Multi-agent room support
- Module factory and dependency injection
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Type, TypeVar

from .config import AgentConfig, ModuleConfig, load_all_configs
from .event_bus import EventBus, create_agent_bus
from .events import AgentState, AgentStateChange
from .interfaces import (
    ASRBackend,
    BaseASRBackend,
    BaseLLMBackend,
    BaseTTSBackend,
    ChatContext,
    LLMBackend,
    MemoryBackend,
    PolicyBackend,
    ToolBackend,
    ToolDefinition,
    TTSBackend,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ─────────────────────────────────────────────────────────────────────────────
# Module Factory
# ─────────────────────────────────────────────────────────────────────────────

class ModuleFactory:
    """
    Factory for creating module instances from configuration.

    Maintains a registry of backend implementations that can be
    instantiated by name.
    """

    def __init__(self) -> None:
        self._asr_backends: dict[str, Type[BaseASRBackend]] = {}
        self._llm_backends: dict[str, Type[BaseLLMBackend]] = {}
        self._tts_backends: dict[str, Type[BaseTTSBackend]] = {}
        self._memory_backends: dict[str, Type[Any]] = {}
        self._policy_backends: dict[str, Type[Any]] = {}

        # Register default implementations
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register built-in backend implementations."""
        # Import real backends
        from ..modules.asr import FasterWhisperASR, WhisperCppASR
        from ..modules.llm import OllamaLLM, VLLMAdapter
        from ..modules.tts import PiperTTS, CoquiTTS

        # ASR backends
        self.register_asr("faster_whisper", FasterWhisperASR)
        self.register_asr("whisper_cpp", WhisperCppASR)

        # LLM backends
        self.register_llm("ollama", OllamaLLM)
        self.register_llm("vllm", VLLMAdapter)

        # TTS backends
        self.register_tts("piper", PiperTTS)
        self.register_tts("coqui", CoquiTTS)

        # Optionally register dev stubs for testing
        try:
            from ..modules.dev_stubs import FakeASR, FakeLLM, FakeTTS
            self.register_asr("fake", FakeASR)
            self.register_llm("fake", FakeLLM)
            self.register_tts("fake", FakeTTS)
        except ImportError:
            pass  # Dev stubs not available

    def register_asr(self, name: str, backend_cls: Type[BaseASRBackend]) -> None:
        """Register an ASR backend implementation."""
        self._asr_backends[name] = backend_cls
        logger.debug(f"Registered ASR backend: {name}")

    def register_llm(self, name: str, backend_cls: Type[BaseLLMBackend]) -> None:
        """Register an LLM backend implementation."""
        self._llm_backends[name] = backend_cls
        logger.debug(f"Registered LLM backend: {name}")

    def register_tts(self, name: str, backend_cls: Type[BaseTTSBackend]) -> None:
        """Register a TTS backend implementation."""
        self._tts_backends[name] = backend_cls
        logger.debug(f"Registered TTS backend: {name}")

    def create_asr(self, config: ModuleConfig) -> ASRBackend:
        """Create an ASR backend from configuration."""
        backend_cls = self._asr_backends.get(config.backend)
        if backend_cls is None:
            raise ValueError(f"Unknown ASR backend: {config.backend}")

        kwargs = {"sample_rate": config.options.get("sample_rate", 24000)}
        if config.model:
            kwargs["model_size" if "whisper" in config.backend else "model"] = config.model
        if config.device != "cpu":
            kwargs["device"] = config.device
        kwargs.update(config.options)

        return backend_cls(**kwargs)

    def create_llm(self, config: ModuleConfig) -> LLMBackend:
        """Create an LLM backend from configuration."""
        backend_cls = self._llm_backends.get(config.backend)
        if backend_cls is None:
            raise ValueError(f"Unknown LLM backend: {config.backend}")

        kwargs = {}
        if config.model:
            kwargs["model" if config.backend == "ollama" else "model_name"] = config.model
        kwargs.update(config.options)

        return backend_cls(**kwargs)

    def create_tts(self, config: ModuleConfig) -> TTSBackend:
        """Create a TTS backend from configuration."""
        backend_cls = self._tts_backends.get(config.backend)
        if backend_cls is None:
            raise ValueError(f"Unknown TTS backend: {config.backend}")

        kwargs = {"sample_rate": config.options.get("sample_rate", 24000)}
        if config.model:
            kwargs["voice"] = config.model
        kwargs.update(config.options)

        return backend_cls(**kwargs)


# Global factory instance
_factory = ModuleFactory()


def get_factory() -> ModuleFactory:
    """Get the global module factory."""
    return _factory


# ─────────────────────────────────────────────────────────────────────────────
# Agent Instance
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentInstance:
    """
    A running agent instance with all wired modules.

    This is the composition root that brings together all
    configured modules into a working agent.
    """

    agent_id: str
    config: AgentConfig

    # Core modules
    asr: ASRBackend
    llm: LLMBackend
    tts: TTSBackend

    # Optional modules
    tools: list[ToolDefinition] = field(default_factory=list)
    memory: Optional[MemoryBackend] = None
    policy: Optional[PolicyBackend] = None

    # Runtime state
    state: AgentState = AgentState.INITIALIZING
    chat_context: ChatContext = field(default_factory=ChatContext)
    event_bus: Optional[EventBus] = None

    # Metadata
    room_name: Optional[str] = None
    participant_identity: Optional[str] = None
    userdata: dict[str, Any] = field(default_factory=dict)

    async def start(self) -> None:
        """Initialize and start the agent."""
        logger.info(f"Starting agent {self.agent_id} ({self.config.name})")

        # Set initial instructions
        if self.config.instructions:
            self.chat_context.add_system_message(self.config.instructions)

        self._set_state(AgentState.LISTENING)

    async def stop(self) -> None:
        """Stop the agent and release resources."""
        logger.info(f"Stopping agent {self.agent_id}")

        self._set_state(AgentState.CLOSED)

        # Close all modules
        await self.asr.close()
        await self.llm.close()
        await self.tts.close()

    def _set_state(self, new_state: AgentState, reason: Optional[str] = None) -> None:
        """Update agent state and emit event."""
        old_state = self.state
        self.state = new_state

        if self.event_bus:
            event = AgentStateChange(
                agent_id=self.agent_id,
                previous_state=old_state,
                new_state=new_state,
                reason=reason,
            )
            asyncio.create_task(self.event_bus.emit(event))


# ─────────────────────────────────────────────────────────────────────────────
# Agent Manager
# ─────────────────────────────────────────────────────────────────────────────

class AgentManager:
    """
    Manages multiple agent instances across rooms.

    Features:
    - Create agents from configs
    - Attach agents to rooms
    - Start/stop/reload agents
    - Route events to correct agents
    """

    def __init__(
        self,
        config_dir: str = "configs",
        factory: Optional[ModuleFactory] = None,
    ):
        self._config_dir = config_dir
        self._factory = factory or get_factory()

        # Loaded configurations
        self._configs: dict[str, AgentConfig] = {}

        # Active agent instances
        self._agents: dict[str, AgentInstance] = {}

        # Room to agent mapping
        self._room_agents: dict[str, list[str]] = {}

        # Global event bus
        self._event_bus = EventBus("agent_manager")

        # Lifecycle callbacks
        self._on_agent_created: list[Callable[[AgentInstance], None]] = []
        self._on_agent_stopped: list[Callable[[AgentInstance], None]] = []

    async def initialize(self) -> None:
        """Load configurations and prepare manager."""
        self._configs = load_all_configs(self._config_dir)
        logger.info(f"Loaded {len(self._configs)} agent configurations")

    def get_available_agents(self) -> list[str]:
        """Get list of available agent config names."""
        return list(self._configs.keys())

    def get_config(self, name: str) -> Optional[AgentConfig]:
        """Get an agent configuration by name."""
        return self._configs.get(name)

    def add_config(self, config: AgentConfig) -> None:
        """Add or update an agent configuration."""
        self._configs[config.name] = config
        logger.info(f"Added agent config: {config.name}")

    async def create_agent(
        self,
        config_name: str,
        room_name: Optional[str] = None,
        participant_identity: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> AgentInstance:
        """
        Create a new agent instance from configuration.

        Args:
            config_name: Name of the agent configuration to use
            room_name: Optional room to attach the agent to
            participant_identity: Optional participant identity for the agent
            agent_id: Optional custom agent ID (auto-generated if not provided)

        Returns:
            The created AgentInstance
        """
        config = self._configs.get(config_name)
        if config is None:
            raise ValueError(f"Unknown agent config: {config_name}")

        agent_id = agent_id or f"{config_name}_{str(uuid.uuid4())[:8]}"

        logger.info(f"Creating agent {agent_id} from config {config_name}")

        # Create modules via factory
        asr = self._factory.create_asr(config.asr)
        llm = self._factory.create_llm(config.llm)
        tts = self._factory.create_tts(config.tts)

        # Create agent-specific event bus
        agent_bus = create_agent_bus(self._event_bus, agent_id)

        # Create instance
        agent = AgentInstance(
            agent_id=agent_id,
            config=config,
            asr=asr,
            llm=llm,
            tts=tts,
            room_name=room_name,
            participant_identity=participant_identity,
            event_bus=self._event_bus,
        )

        # Store instance
        self._agents[agent_id] = agent

        # Track room association
        if room_name:
            if room_name not in self._room_agents:
                self._room_agents[room_name] = []
            self._room_agents[room_name].append(agent_id)

        # Notify callbacks
        for callback in self._on_agent_created:
            try:
                callback(agent)
            except Exception as e:
                logger.error(f"Agent created callback error: {e}")

        return agent

    async def start_agent(self, agent_id: str) -> None:
        """Start an agent instance."""
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ValueError(f"Unknown agent: {agent_id}")

        await agent.start()

    async def stop_agent(self, agent_id: str) -> None:
        """Stop and remove an agent instance."""
        agent = self._agents.get(agent_id)
        if agent is None:
            return

        await agent.stop()

        # Remove from tracking
        del self._agents[agent_id]

        if agent.room_name and agent.room_name in self._room_agents:
            self._room_agents[agent.room_name].remove(agent_id)

        # Notify callbacks
        for callback in self._on_agent_stopped:
            try:
                callback(agent)
            except Exception as e:
                logger.error(f"Agent stopped callback error: {e}")

    async def reload_agent(self, agent_id: str) -> AgentInstance:
        """Reload an agent with fresh configuration."""
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ValueError(f"Unknown agent: {agent_id}")

        # Stop current instance
        room_name = agent.room_name
        participant_identity = agent.participant_identity
        config_name = agent.config.name

        await self.stop_agent(agent_id)

        # Create new instance
        new_agent = await self.create_agent(
            config_name,
            room_name=room_name,
            participant_identity=participant_identity,
            agent_id=agent_id,
        )

        await self.start_agent(agent_id)

        return new_agent

    def get_agent(self, agent_id: str) -> Optional[AgentInstance]:
        """Get an agent by ID."""
        return self._agents.get(agent_id)

    def get_room_agents(self, room_name: str) -> list[AgentInstance]:
        """Get all agents in a room."""
        agent_ids = self._room_agents.get(room_name, [])
        return [self._agents[aid] for aid in agent_ids if aid in self._agents]

    def list_agents(self) -> list[AgentInstance]:
        """List all active agent instances."""
        return list(self._agents.values())

    def on_agent_created(self, callback: Callable[[AgentInstance], None]) -> None:
        """Register a callback for when agents are created."""
        self._on_agent_created.append(callback)

    def on_agent_stopped(self, callback: Callable[[AgentInstance], None]) -> None:
        """Register a callback for when agents are stopped."""
        self._on_agent_stopped.append(callback)

    @property
    def event_bus(self) -> EventBus:
        """Get the global event bus."""
        return self._event_bus

    async def shutdown(self) -> None:
        """Shutdown all agents and cleanup."""
        logger.info("Shutting down agent manager")

        # Stop all agents
        for agent_id in list(self._agents.keys()):
            await self.stop_agent(agent_id)

        self._event_bus.stop()
