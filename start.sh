#!/usr/bin/env bash
# ==========================================================================
# Start the LiteLLM gateway with this project's accounting callback.
#
# The script checks for the files the gateway needs, then hands over to
# litellm. It never edits the price table or the raw logs.
# ==========================================================================

set -euo pipefail

cd "$(dirname "$0")"

echo "=== ai-gateway start ==="

# 1. The gateway configuration must exist.
if [ ! -f "config/config.yaml" ]; then
    echo "ERROR: config/config.yaml not found."
    echo "Copy config/config.example.yaml to config/config.yaml and fill it in."
    exit 1
fi

# 2. The price table must exist, because reports need it.
if [ ! -f "config/pricing.yaml" ]; then
    echo "ERROR: config/pricing.yaml not found."
    echo "Copy and fill in a price table before starting."
    exit 1
fi

# 3. The raw log directory is created here so the callback never has to.
if [ ! -d "logs" ]; then
    mkdir -p "logs"
    echo "Created logs/"
fi

# 4. The secrets are read from the environment, never from the config.
if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    echo "WARNING: DEEPSEEK_API_KEY is not set. The gateway will reject calls."
fi
if [ -z "${DATABASE_URL:-}" ]; then
    echo "WARNING: DATABASE_URL is not set. Virtual keys will not work."
fi

# 5. Hand over. The port can be overridden by setting PORT first.
PORT="${PORT:-4000}"
echo "Starting LiteLLM on port ${PORT}"
echo

exec litellm --config "config/config.yaml" --port "${PORT}"
