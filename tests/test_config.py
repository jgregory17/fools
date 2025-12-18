"""Tests for configuration loading."""

import os
import tempfile
from pathlib import Path
import pytest
from agent_playground.core.config import AgentConfig, ModuleConfig, load_all_configs


def test_module_config_from_string():
    """Test parsing module config from string shorthand."""
    from agent_playground.core.config import _parse_module_config

    # Simple backend name
    config = _parse_module_config("fake")
    assert config.backend == "fake"
    assert config.model is None

    # Backend with model
    config = _parse_module_config("ollama/llama3.1")
    assert config.backend == "ollama"
    assert config.model == "llama3.1"


def test_module_config_from_dict():
    """Test parsing module config from dictionary."""
    from agent_playground.core.config import _parse_module_config

    config = _parse_module_config({
        "backend": "faster_whisper",
        "model": "base.en",
        "device": "cuda",
        "sample_rate": 16000,
    })

    assert config.backend == "faster_whisper"
    assert config.model == "base.en"
    assert config.device == "cuda"
    assert config.options["sample_rate"] == 16000


def test_agent_config_from_dict():
    """Test creating AgentConfig from dictionary."""
    data = {
        "name": "test_agent",
        "description": "A test agent",
        "instructions": "Be helpful",
        "modules": {
            "asr": {"backend": "fake"},
            "llm": {"backend": "fake"},
            "tts": {"backend": "fake"},
        },
        "behavior": {
            "greeting": "Hello!",
            "allow_interruptions": False,
        },
    }

    config = AgentConfig.from_dict(data)

    assert config.name == "test_agent"
    assert config.description == "A test agent"
    assert config.instructions == "Be helpful"
    assert config.asr.backend == "fake"
    assert config.behavior.greeting == "Hello!"
    assert config.behavior.allow_interruptions is False


def test_agent_config_from_yaml():
    """Test loading AgentConfig from YAML file."""
    yaml_content = """
name: yaml_test
description: Test from YAML
instructions: Be concise

modules:
  asr:
    backend: fake
  llm:
    backend: fake
    temperature: 0.5
  tts:
    backend: fake

behavior:
  greeting: "Hi there!"
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()

        try:
            config = AgentConfig.from_yaml(f.name)

            assert config.name == "yaml_test"
            assert config.llm.options.get("temperature") == 0.5
            assert config.behavior.greeting == "Hi there!"
        finally:
            os.unlink(f.name)


def test_env_var_interpolation():
    """Test environment variable interpolation in config."""
    from agent_playground.core.config import _interpolate_env_vars

    os.environ["TEST_VAR"] = "test_value"

    # Basic interpolation
    result = _interpolate_env_vars("${TEST_VAR}")
    assert result == "test_value"

    # With default
    result = _interpolate_env_vars("${MISSING_VAR:-default}")
    assert result == "default"

    # In dict
    result = _interpolate_env_vars({"key": "${TEST_VAR}"})
    assert result["key"] == "test_value"

    # In list
    result = _interpolate_env_vars(["${TEST_VAR}", "static"])
    assert result[0] == "test_value"
    assert result[1] == "static"

    del os.environ["TEST_VAR"]


def test_load_all_configs():
    """Test loading all configs from directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test config files
        for name in ["agent1", "agent2"]:
            path = Path(tmpdir) / f"{name}.yaml"
            path.write_text(f"""
name: {name}
modules:
  asr:
    backend: fake
  llm:
    backend: fake
  tts:
    backend: fake
""")

        configs = load_all_configs(tmpdir)

        assert len(configs) == 2
        assert "agent1" in configs
        assert "agent2" in configs
