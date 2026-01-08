#!/bin/sh
set -e

# Agent Docker Entrypoint
# Reads secrets from Docker secret files and exports as environment variables

# Function to read secret from file if it exists
read_secret() {
    local secret_name="$1"
    local env_var="$2"
    local default_value="$3"
    
    if [ -f "/run/secrets/${secret_name}" ]; then
        export "${env_var}"=$(cat "/run/secrets/${secret_name}")
        echo "Loaded ${env_var} from Docker secret"
    elif [ -z "$(eval echo \$${env_var})" ]; then
        # Only set default if env var not already set
        export "${env_var}"="${default_value}"
        echo "Using default ${env_var} for development"
    fi
}

# Read LiveKit credentials from secrets if available
read_secret "livekit_api_key" "LIVEKIT_API_KEY" "devkey"
read_secret "livekit_api_secret" "LIVEKIT_API_SECRET" "secret"

# Support _FILE environment variables for compatibility
if [ -n "${LIVEKIT_API_KEY_FILE}" ] && [ -f "${LIVEKIT_API_KEY_FILE}" ]; then
    export LIVEKIT_API_KEY=$(cat "${LIVEKIT_API_KEY_FILE}")
    echo "Loaded LIVEKIT_API_KEY from file specified in LIVEKIT_API_KEY_FILE"
fi

if [ -n "${LIVEKIT_API_SECRET_FILE}" ] && [ -f "${LIVEKIT_API_SECRET_FILE}" ]; then
    export LIVEKIT_API_SECRET=$(cat "${LIVEKIT_API_SECRET_FILE}")
    echo "Loaded LIVEKIT_API_SECRET from file specified in LIVEKIT_API_SECRET_FILE"
fi

# Execute the original command
exec "$@"