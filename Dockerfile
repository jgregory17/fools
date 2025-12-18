# Agent Playground Dockerfile
# Multi-stage build for efficient image
#
# Build: docker build -t agent-playground .
# Run:   docker run -p 8080:8080 agent-playground
#
# For selfhosted mode (no cloud APIs):
#   docker build --build-arg AGENT_MODE=selfhosted -t agent-playground .

# Build argument to select mode
ARG AGENT_MODE=selfhosted

# ============================================================================
# Build stage
# ============================================================================
FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY src/ src/

# Build wheel
RUN pip install --no-cache-dir build && \
    python -m build --wheel

# ============================================================================
# Runtime stage
# ============================================================================
FROM python:3.11-slim as runtime

# Inherit build arg
ARG AGENT_MODE=selfhosted

WORKDIR /app

# Install runtime dependencies
# - libsndfile1: audio processing
# - ffmpeg: audio/video codecs
# - libportaudio2: audio I/O for some TTS
# - curl: health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    ffmpeg \
    libportaudio2 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy wheel from builder
COPY --from=builder /build/dist/*.whl /tmp/

# Install the package with appropriate extras based on mode
# selfhosted: Vosk STT + Piper TTS + Ollama LLM (no cloud APIs)
# livekit: Cloud/hybrid mode with LiveKit plugins
RUN if [ "$AGENT_MODE" = "selfhosted" ]; then \
        pip install --no-cache-dir "/tmp/*.whl[selfhosted]"; \
    else \
        pip install --no-cache-dir "/tmp/*.whl[livekit]"; \
    fi

# Copy configs directory
COPY configs/ /app/configs/

# Create non-root user
RUN useradd --create-home --shell /bin/bash agent && \
    chown -R agent:agent /app

USER agent
WORKDIR /home/agent

# Create cache directories for model downloads
RUN mkdir -p /home/agent/.cache/vosk /home/agent/.local/share/piper

# Copy example configs
COPY --chown=agent:agent examples/ examples/

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Agent mode (selfhosted = fully local, no cloud APIs)
    AGENT_MODE=selfhosted \
    # LLM config (Ollama)
    LLM_BASE_URL=http://ollama:11434/v1 \
    LLM_MODEL=llama3.2 \
    # LiveKit connection
    LIVEKIT_URL=ws://livekit:7880 \
    LIVEKIT_API_KEY=devkey \
    LIVEKIT_API_SECRET=secret \
    # Selfhosted model config
    VOSK_MODEL=vosk-model-small-en-us-0.15 \
    PIPER_VOICE=en_US-lessac-medium

# Expose port (for HTTP API if running alongside worker)
EXPOSE 8080

# Health check - the worker doesn't expose HTTP, so we check if process is running
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD pgrep -f "agent_playground.worker" || exit 1

# Default command: run the LiveKit agent worker
CMD ["python", "-m", "agent_playground.worker", "start"]
