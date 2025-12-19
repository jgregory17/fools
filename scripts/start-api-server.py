#!/usr/bin/env python3
"""
Start the Agent Playground API server with Agent Manager configured.
"""
import asyncio
import logging
import os
from pathlib import Path

from agent_playground.core.agent_manager import AgentManager
from agent_playground.server import run_server


def main():
    """Start API server with agent manager."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    logger = logging.getLogger(__name__)

    # Configuration
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8080"))
    agent_id = os.getenv("AGENT_ID")
    config_dir = os.getenv("AGENT_CONFIG_DIR", "/app/configs/agents")

    # Initialize agent manager with config directory
    logger.info(f"Initializing AgentManager with config_dir: {config_dir}")
    if not Path(config_dir).exists():
        logger.warning(f"Config directory not found: {config_dir}")
        logger.info("Server will start without agent configurations")
        agent_manager = None
    else:
        agent_manager = AgentManager(config_dir=config_dir)
        # Must call initialize() asynchronously to load configs
        asyncio.run(agent_manager.initialize())
        logger.info(f"AgentManager initialized with {len(agent_manager._configs)} configurations")

    # Start server
    logger.info(f"Starting API server on {host}:{port}")
    asyncio.run(run_server(
        host=host,
        port=port,
        agent_manager=agent_manager,
        agent_id=agent_id,
    ))


if __name__ == "__main__":
    main()