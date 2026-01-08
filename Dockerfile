# Agent Playground Dockerfile
# Multi-stage build for efficient image
#
# Self-hosted voice agent with:
#   - Vosk STT (local, CPU-based)
#   - Ollama LLM (local, various models)
#   - Piper TTS (local, neural TTS)
#
# Build: docker build -t agent-playground .
# Run:   docker run -p 8080:8080 agent-playground

# ============================================================================
# Build stage
# ============================================================================
FROM python:3.13-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
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
FROM python:3.13-slim as runtime

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
    vim \
    && rm -rf /var/lib/apt/lists/*

# Copy wheel from builder
COPY --from=builder /build/dist/*.whl /tmp/

# Install the package with selfhosted extras
# Includes: Vosk STT + Piper TTS + Ollama LLM support (all local, no cloud APIs)
RUN for whl in /tmp/*.whl; do pip install --no-cache-dir "$whl[selfhosted]"; done

# Copy configs directory
COPY configs/ /app/configs/

# Create non-root user
RUN useradd --create-home --shell /bin/bash agent && \
    chown -R agent:agent /app

USER agent
WORKDIR /home/agent

# Create cache directories for model downloads
RUN mkdir -p /home/agent/.cache/vosk /home/agent/.local/share/piper

# Pre-download Piper voice model (en_US-lessac-medium)
ARG PIPER_VOICE=en_US-lessac-medium
RUN echo "Pre-downloading Piper voice: $PIPER_VOICE" && \
    cd /home/agent/.local/share/piper && \
    curl -L -o "${PIPER_VOICE}.onnx" "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx" && \
    curl -L -o "${PIPER_VOICE}.onnx.json" "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json" && \
    echo "Piper voice $PIPER_VOICE downloaded successfully"

# Copy example configs
COPY --chown=agent:agent examples/ examples/

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # LLM config (Ollama)
    LLM_BASE_URL=http://ollama:11434/v1 \
    LLM_MODEL=llama3.2 \
    # LiveKit connection
    LIVEKIT_URL=ws://livekit:7880 \
    LIVEKIT_API_KEY=devkey \
    LIVEKIT_API_SECRET=secret \
    # Local model config
    VOSK_MODEL=vosk-model-small-en-us-0.15 \
    PIPER_VOICE=en_US-lessac-medium

# Expose port (for HTTP API if running alongside worker)
EXPOSE 8080

# Health check - the worker doesn't expose HTTP, so we check if process is running
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD pgrep -f "agent_playground.worker" || exit 1

# Default command: run the LiveKit agent worker
CMD ["python", "-m", "agent_playground.worker", "start"]
