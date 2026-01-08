#!/bin/sh
set -e

# LiveKit Docker Entrypoint
# Reads secrets from Docker secret files and generates config from template

# Default values for development
DEFAULT_API_KEY="devkey"
DEFAULT_API_SECRET="secret"

# Read API key from secret file or use default
if [ -f "/run/secrets/livekit_api_key" ]; then
    API_KEY=$(cat /run/secrets/livekit_api_key)
    echo "Using LiveKit API key from Docker secret"
else
    API_KEY="${LIVEKIT_API_KEY:-$DEFAULT_API_KEY}"
    echo "Using default LiveKit API key for development"
fi

# Read API secret from secret file or use default
if [ -f "/run/secrets/livekit_api_secret" ]; then
    API_SECRET=$(cat /run/secrets/livekit_api_secret)
    echo "Using LiveKit API secret from Docker secret"
else
    API_SECRET="${LIVEKIT_API_SECRET:-$DEFAULT_API_SECRET}"
    echo "Using default LiveKit API secret for development"
fi

# Check if template exists
if [ -f "/etc/livekit.yaml.tpl" ]; then
    # Generate config from template
    # Use sed to replace placeholders - avoid printing secrets
    sed -e "s|{{API_KEY}}|${API_KEY}|g" \
        -e "s|{{API_SECRET}}|${API_SECRET}|g" \
        /etc/livekit.yaml.tpl > /etc/livekit.yaml
    echo "Generated LiveKit configuration from template"
else
    # Fallback: use existing config if no template
    echo "No template found, using existing configuration"
fi

# Execute LiveKit server with all provided arguments
# The LiveKit image has the binary at /livekit-server
exec /livekit-server "$@"