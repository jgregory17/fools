#!/bin/sh
set -e

# Grafana Docker Entrypoint
# Reads admin password from Docker secret file

# Read Grafana admin password from secret if available
if [ -f "/run/secrets/grafana_admin_password" ]; then
    export GF_SECURITY_ADMIN_PASSWORD=$(cat /run/secrets/grafana_admin_password)
    echo "Loaded Grafana admin password from Docker secret"
elif [ -z "${GF_SECURITY_ADMIN_PASSWORD}" ]; then
    # Use default for development if not set
    export GF_SECURITY_ADMIN_PASSWORD="admin"
    echo "Using default Grafana admin password for development"
fi

# Support _FILE environment variable pattern (Grafana native support)
if [ -n "${GF_SECURITY_ADMIN_PASSWORD_FILE}" ] && [ -f "${GF_SECURITY_ADMIN_PASSWORD_FILE}" ]; then
    export GF_SECURITY_ADMIN_PASSWORD=$(cat "${GF_SECURITY_ADMIN_PASSWORD_FILE}")
    echo "Loaded Grafana admin password from file specified in GF_SECURITY_ADMIN_PASSWORD_FILE"
fi

# Execute Grafana with original entrypoint
exec /run.sh "$@"