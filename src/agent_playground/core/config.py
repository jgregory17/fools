"""
Configuration System for Agent Playground.

Provides:
- YAML-based agent configuration
- Module configuration with dependency injection
- Validation and defaults
- Environment variable interpolation
- Configuration for all pipeline components (audio, backpressure, interrupts, etc.)

All defaults are tuned for safe real-time voice operation.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Dict

import yaml


# ─────────────────────────────────────────────────────────────────────────────
# Audio Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AudioConfig:
    """
    Canonical audio format configuration.

    These defaults are chosen for real-time voice:
    - 24kHz: Good for TTS quality, efficient for ASR
    - Mono: Voice agents don't need stereo
    - 20ms frames: Balance between latency and efficiency
    """

    sample_rate: int = 24000
    num_channels: int = 1
    frame_duration_ms: int = 20

    # Format
    format: str = "pcm_s16le"  # Signed 16-bit little-endian

    # Resampling
    auto_resample: bool = True
    resample_quality: str = "medium"  # quick, low, medium, high, veryhigh


# ─────────────────────────────────────────────────────────────────────────────
# Backpressure Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BackpressureConfig:
    """
    Backpressure configuration for bounded queues.

    Defaults prefer freshness over completeness (drop oldest frames).
    """

    # Queue sizes
    audio_in_queue_size: int = 100     # ~2 seconds at 20ms frames
    audio_out_queue_size: int = 100
    transcript_queue_size: int = 10
    tts_queue_size: int = 50

    # Mode: "drop_oldest", "drop_newest", "block"
    mode: str = "drop_oldest"

    # For block mode
    block_timeout_ms: float = 100.0

    # Logging
    log_drops: bool = True
    log_every_n_drops: int = 10


# ─────────────────────────────────────────────────────────────────────────────
# Echo Suppression Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EchoConfig:
    """
    Echo/feedback prevention configuration.

    Default: Suppress ASR during agent speech to prevent echo.
    """

    enabled: bool = True
    suppress_asr_during_speech: bool = True
    post_speech_suppression_ms: float = 200.0
    enable_aec: bool = False  # Acoustic Echo Cancellation
    min_speech_energy: float = 0.01


# ─────────────────────────────────────────────────────────────────────────────
# Interruption Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InterruptionConfig:
    """
    Barge-in/interruption handling configuration.

    Default: Allow interruptions with reasonable thresholds.
    """

    allow_interruptions: bool = True
    vad_threshold: float = 0.5
    min_duration_ms: float = 200.0
    min_consecutive_frames: int = 3
    grace_period_ms: float = 500.0
    immediate_cancel: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# VAD Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VADConfig:
    """Voice Activity Detection configuration."""

    backend: str = "energy"  # "energy", "silero"

    # Energy VAD settings
    energy_threshold: float = 0.02
    adaptive: bool = True

    # Common settings
    speech_threshold: float = 0.5
    silence_threshold: float = 0.3
    min_speech_frames: int = 3
    min_silence_frames: int = 5


# ─────────────────────────────────────────────────────────────────────────────
# Turn-Taking Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TurnTakingConfig:
    """Turn-taking behavior configuration."""

    eou_threshold: float = 0.7
    silence_timeout_ms: float = 1000.0
    min_turn_duration_ms: float = 100.0
    max_turn_duration_ms: float = 30000.0
    response_delay_ms: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Memory Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MemoryConfig:
    """Conversation memory configuration."""

    max_turns: int = 50
    max_context_tokens: int = 8000
    summarize_threshold: float = 0.8
    summary_max_tokens: int = 500
    min_recent_turns: int = 5
    auto_summarize: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# Resource Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ResourceConfig:
    """GPU/device resource configuration."""

    # Device assignments by module
    asr_device: str = "auto"
    llm_device: str = "auto"
    tts_device: str = "cpu"  # TTS often fast enough on CPU
    vad_device: str = "cpu"

    # Memory limits (MB, 0 = no limit)
    memory_limit_mb: int = 0

    # Serialize GPU operations
    serialize_gpu_ops: bool = False

    # Verbose logging
    verbose: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Module Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModuleConfig:
    """Configuration for a single module (ASR, LLM, TTS, etc.)."""

    backend: str  # e.g., "fake", "faster_whisper", "ollama", "piper"
    model: Optional[str] = None
    device: str = "auto"  # "cpu", "cuda", "cuda:0", "auto"
    options: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Get an option value with default."""
        return self.options.get(key, default)


@dataclass
class ToolConfig:
    """Configuration for a tool."""

    name: str
    handler: str  # Module path, e.g., "tools.transfers.support_handoff"
    description: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Behavior Configuration (combines several sub-configs)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BehaviorConfig:
    """
    Agent behavior configuration.

    Combines greeting, interruptions, turn-taking, and echo suppression
    into a single config block for ease of use.
    """

    # Basic behavior
    greeting: Optional[str] = None
    max_turns: int = 100
    user_away_timeout: float = 15.0

    # Interruption handling (from InterruptionConfig)
    allow_interruptions: bool = True
    interruption_threshold: float = 0.5
    min_interruption_duration_ms: float = 200.0
    interruption_grace_period_ms: float = 500.0

    # Echo suppression (from EchoConfig)
    suppress_asr_during_speech: bool = True
    post_speech_suppression_ms: float = 200.0
    enable_aec: bool = False

    # Turn-taking (from TurnTakingConfig)
    silence_timeout_ms: float = 1000.0
    response_delay_ms: float = 0.0

    # Generation
    min_speech_delay: float = 0.0
    preemptive_generation: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    """
    Pipeline-level configuration.

    Controls audio format, backpressure, and scheduling.
    """

    audio: AudioConfig = field(default_factory=AudioConfig)
    backpressure: BackpressureConfig = field(default_factory=BackpressureConfig)
    resources: ResourceConfig = field(default_factory=ResourceConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    # Metrics
    enable_metrics: bool = True
    metrics_interval_seconds: float = 30.0


# ─────────────────────────────────────────────────────────────────────────────
# Agent Configuration (top-level)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentConfig:
    """
    Complete configuration for an agent.

    Loaded from YAML files in the configs/ directory.
    All defaults are tuned for safe real-time voice operation.
    """

    name: str
    description: str = ""
    instructions: str = "You are a helpful voice AI assistant."

    # Module configurations - real backends by default
    asr: ModuleConfig = field(default_factory=lambda: ModuleConfig(backend="faster_whisper"))
    llm: ModuleConfig = field(default_factory=lambda: ModuleConfig(backend="ollama"))
    tts: ModuleConfig = field(default_factory=lambda: ModuleConfig(backend="piper"))

    # Optional modules
    vad: Optional[ModuleConfig] = None
    memory: Optional[ModuleConfig] = None
    policy: Optional[ModuleConfig] = None

    # Tools
    tools: list[ToolConfig] = field(default_factory=list)

    # Behavior
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)

    # Pipeline settings
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)

    # Metadata
    version: str = "1.0"
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path | str) -> AgentConfig:
        """Load configuration from a YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path) as f:
            raw = yaml.safe_load(f)

        # Interpolate environment variables
        raw = _interpolate_env_vars(raw)

        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentConfig:
        """Create configuration from a dictionary."""
        # Parse module configs - real backends by default
        modules_data = data.get("modules", {})
        asr_data = modules_data.get("asr", data.get("asr", {"backend": "faster_whisper"}))
        llm_data = modules_data.get("llm", data.get("llm", {"backend": "ollama"}))
        tts_data = modules_data.get("tts", data.get("tts", {"backend": "piper"}))

        asr = _parse_module_config(asr_data)
        llm = _parse_module_config(llm_data)
        tts = _parse_module_config(tts_data)

        # Optional modules
        vad = None
        if "vad" in modules_data or "vad" in data:
            vad_data = modules_data.get("vad", data.get("vad"))
            if vad_data:
                vad = _parse_module_config(vad_data)

        memory = None
        if "memory" in modules_data or "memory" in data:
            memory_data = modules_data.get("memory", data.get("memory"))
            if memory_data:
                memory = _parse_module_config(memory_data)

        policy = None
        if "policy" in modules_data or "policy" in data:
            policy_data = modules_data.get("policy", data.get("policy"))
            if policy_data:
                policy = _parse_module_config(policy_data)

        # Parse tools
        tools = []
        for tool_data in data.get("tools", []):
            tools.append(ToolConfig(
                name=tool_data["name"],
                handler=tool_data["handler"],
                description=tool_data.get("description"),
                parameters=tool_data.get("parameters", {}),
            ))

        # Parse behavior
        behavior_data = data.get("behavior", {})
        behavior = BehaviorConfig(
            greeting=behavior_data.get("greeting"),
            max_turns=behavior_data.get("max_turns", 100),
            user_away_timeout=behavior_data.get("user_away_timeout", 15.0),
            allow_interruptions=behavior_data.get("allow_interruptions", True),
            interruption_threshold=behavior_data.get("interruption_threshold", 0.5),
            min_interruption_duration_ms=behavior_data.get("min_interruption_duration_ms", 200.0),
            interruption_grace_period_ms=behavior_data.get("interruption_grace_period_ms", 500.0),
            suppress_asr_during_speech=behavior_data.get("suppress_asr_during_speech", True),
            post_speech_suppression_ms=behavior_data.get("post_speech_suppression_ms", 200.0),
            enable_aec=behavior_data.get("enable_aec", False),
            silence_timeout_ms=behavior_data.get("silence_timeout_ms", 1000.0),
            response_delay_ms=behavior_data.get("response_delay_ms", 0.0),
            min_speech_delay=behavior_data.get("min_speech_delay", 0.0),
            preemptive_generation=behavior_data.get("preemptive_generation", False),
        )

        # Parse pipeline config
        pipeline_data = data.get("pipeline", {})
        pipeline = _parse_pipeline_config(pipeline_data)

        return cls(
            name=data.get("name", "unnamed_agent"),
            description=data.get("description", ""),
            instructions=data.get("instructions", "You are a helpful voice AI assistant."),
            asr=asr,
            llm=llm,
            tts=tts,
            vad=vad,
            memory=memory,
            policy=policy,
            tools=tools,
            behavior=behavior,
            pipeline=pipeline,
            version=data.get("version", "1.0"),
            tags=data.get("tags", []),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "modules": {
                "asr": {
                    "backend": self.asr.backend,
                    "model": self.asr.model,
                    "device": self.asr.device,
                    **self.asr.options,
                },
                "llm": {
                    "backend": self.llm.backend,
                    "model": self.llm.model,
                    "device": self.llm.device,
                    **self.llm.options,
                },
                "tts": {
                    "backend": self.tts.backend,
                    "model": self.tts.model,
                    "device": self.tts.device,
                    **self.tts.options,
                },
            },
            "tools": [
                {"name": t.name, "handler": t.handler, "description": t.description}
                for t in self.tools
            ],
            "behavior": {
                "greeting": self.behavior.greeting,
                "max_turns": self.behavior.max_turns,
                "allow_interruptions": self.behavior.allow_interruptions,
                "interruption_threshold": self.behavior.interruption_threshold,
                "suppress_asr_during_speech": self.behavior.suppress_asr_during_speech,
                "silence_timeout_ms": self.behavior.silence_timeout_ms,
            },
            "pipeline": {
                "audio": {
                    "sample_rate": self.pipeline.audio.sample_rate,
                    "num_channels": self.pipeline.audio.num_channels,
                    "frame_duration_ms": self.pipeline.audio.frame_duration_ms,
                },
                "backpressure": {
                    "mode": self.pipeline.backpressure.mode,
                    "audio_in_queue_size": self.pipeline.backpressure.audio_in_queue_size,
                },
            },
            "version": self.version,
            "tags": self.tags,
        }

        # Add optional modules
        if self.vad:
            result["modules"]["vad"] = {
                "backend": self.vad.backend,
                **self.vad.options,
            }

        if self.memory:
            result["modules"]["memory"] = {
                "backend": self.memory.backend,
                **self.memory.options,
            }

        return result


def _parse_module_config(data: Dict[str, Any] | str) -> ModuleConfig:
    """Parse a module configuration from dict or string."""
    if isinstance(data, str):
        # Short form: "backend_name" or "backend_name/model"
        if "/" in data:
            backend, model = data.split("/", 1)
            return ModuleConfig(backend=backend, model=model)
        return ModuleConfig(backend=data)

    backend = data.get("backend", "fake")
    model = data.get("model")
    device = data.get("device", "auto")

    # Everything else goes into options
    options = {k: v for k, v in data.items() if k not in ("backend", "model", "device")}

    return ModuleConfig(
        backend=backend,
        model=model,
        device=device,
        options=options,
    )


def _parse_pipeline_config(data: Dict[str, Any]) -> PipelineConfig:
    """Parse pipeline configuration."""
    audio_data = data.get("audio", {})
    audio = AudioConfig(
        sample_rate=audio_data.get("sample_rate", 24000),
        num_channels=audio_data.get("num_channels", 1),
        frame_duration_ms=audio_data.get("frame_duration_ms", 20),
        format=audio_data.get("format", "pcm_s16le"),
        auto_resample=audio_data.get("auto_resample", True),
        resample_quality=audio_data.get("resample_quality", "medium"),
    )

    bp_data = data.get("backpressure", {})
    backpressure = BackpressureConfig(
        audio_in_queue_size=bp_data.get("audio_in_queue_size", 100),
        audio_out_queue_size=bp_data.get("audio_out_queue_size", 100),
        transcript_queue_size=bp_data.get("transcript_queue_size", 10),
        tts_queue_size=bp_data.get("tts_queue_size", 50),
        mode=bp_data.get("mode", "drop_oldest"),
        block_timeout_ms=bp_data.get("block_timeout_ms", 100.0),
        log_drops=bp_data.get("log_drops", True),
        log_every_n_drops=bp_data.get("log_every_n_drops", 10),
    )

    res_data = data.get("resources", {})
    resources = ResourceConfig(
        asr_device=res_data.get("asr_device", "auto"),
        llm_device=res_data.get("llm_device", "auto"),
        tts_device=res_data.get("tts_device", "cpu"),
        vad_device=res_data.get("vad_device", "cpu"),
        memory_limit_mb=res_data.get("memory_limit_mb", 0),
        serialize_gpu_ops=res_data.get("serialize_gpu_ops", False),
        verbose=res_data.get("verbose", False),
    )

    vad_data = data.get("vad", {})
    vad = VADConfig(
        backend=vad_data.get("backend", "energy"),
        energy_threshold=vad_data.get("energy_threshold", 0.02),
        adaptive=vad_data.get("adaptive", True),
        speech_threshold=vad_data.get("speech_threshold", 0.5),
        silence_threshold=vad_data.get("silence_threshold", 0.3),
        min_speech_frames=vad_data.get("min_speech_frames", 3),
        min_silence_frames=vad_data.get("min_silence_frames", 5),
    )

    mem_data = data.get("memory", {})
    memory = MemoryConfig(
        max_turns=mem_data.get("max_turns", 50),
        max_context_tokens=mem_data.get("max_context_tokens", 8000),
        summarize_threshold=mem_data.get("summarize_threshold", 0.8),
        summary_max_tokens=mem_data.get("summary_max_tokens", 500),
        min_recent_turns=mem_data.get("min_recent_turns", 5),
        auto_summarize=mem_data.get("auto_summarize", True),
    )

    return PipelineConfig(
        audio=audio,
        backpressure=backpressure,
        resources=resources,
        vad=vad,
        memory=memory,
        enable_metrics=data.get("enable_metrics", True),
        metrics_interval_seconds=data.get("metrics_interval_seconds", 30.0),
    )


def _interpolate_env_vars(data: Any) -> Any:
    """
    Recursively interpolate environment variables in config values.

    Supports ${VAR_NAME} and ${VAR_NAME:-default} syntax.
    """
    if isinstance(data, str):
        # Pattern: ${VAR_NAME} or ${VAR_NAME:-default}
        pattern = r"\$\{([^}:]+)(?::-([^}]*))?\}"

        def replace(match: re.Match) -> str:
            var_name = match.group(1)
            default = match.group(2)
            value = os.environ.get(var_name)
            if value is None:
                if default is not None:
                    return default
                raise ValueError(f"Environment variable {var_name} not set")
            return value

        return re.sub(pattern, replace, data)

    elif isinstance(data, dict):
        return {k: _interpolate_env_vars(v) for k, v in data.items()}

    elif isinstance(data, list):
        return [_interpolate_env_vars(item) for item in data]

    return data


def load_all_configs(config_dir: Path | str = "configs") -> Dict[str, AgentConfig]:
    """Load all agent configurations from a directory."""
    config_dir = Path(config_dir)
    configs = {}

    if not config_dir.exists():
        return configs

    for path in config_dir.glob("*.yaml"):
        try:
            config = AgentConfig.from_yaml(path)
            configs[config.name] = config
        except Exception as e:
            import logging
            logging.warning(f"Failed to load config {path}: {e}")

    return configs


def generate_default_config() -> str:
    """Generate a default configuration as YAML string."""
    return """# Agent Configuration
# All defaults are tuned for safe real-time voice operation
# Uses REAL local backends by default - requires:
#   - Ollama running locally: ollama serve && ollama pull llama3.2
#   - pip install faster-whisper piper-tts httpx numpy

name: my_agent
description: A voice AI assistant
instructions: |
  You are a helpful voice assistant. Be concise and natural.
  Speak in short sentences suitable for voice interaction.

# Module configurations (real backends by default)
modules:
  asr:
    backend: faster_whisper  # Options: faster_whisper, whisper_cpp
    model: base.en
    device: auto

  llm:
    backend: ollama  # Options: ollama, vllm
    model: llama3.2
    # base_url: http://localhost:11434  # Default Ollama URL
    # temperature: 0.7

  tts:
    backend: piper  # Options: piper, coqui
    voice: en_US-lessac-medium
    # Auto-downloads voice model on first use

  vad:
    backend: silero  # Options: energy, silero
    # Falls back to energy VAD if torch unavailable

# Behavior settings
behavior:
  greeting: "Hello! How can I help you today?"
  allow_interruptions: true
  interruption_threshold: 0.5
  suppress_asr_during_speech: true
  silence_timeout_ms: 1000

# Pipeline settings (usually defaults are fine)
pipeline:
  audio:
    sample_rate: 24000
    num_channels: 1
    frame_duration_ms: 20

  backpressure:
    mode: drop_oldest  # Options: drop_oldest, drop_newest, block
    audio_in_queue_size: 100

  resources:
    asr_device: auto
    llm_device: auto
    tts_device: cpu

  memory:
    max_turns: 50
    max_context_tokens: 8000
    auto_summarize: true

version: "1.0"
tags:
  - voice
  - assistant
"""
