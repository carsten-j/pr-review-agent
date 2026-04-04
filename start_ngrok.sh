#!/bin/bash
# start_ngrok.sh
# Starts an ngrok tunnel that forwards the static free-tier URL to the local
# PR Review Agent webhook server running on port 8000.
#
# Usage: ./start_ngrok.sh

PORT=8000

# Load NGROK_URL from .env file in the same directory as this script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  NGROK_URL=$(grep -E '^NGROK_URL=' "${ENV_FILE}" | cut -d '=' -f2-)
fi

if [[ -z "${NGROK_URL}" ]]; then
  echo "Error: NGROK_URL is not set. Add NGROK_URL=<your-domain> to .env"
  exit 1
fi

echo "Starting ngrok tunnel: https://${NGROK_URL} -> localhost:${PORT}"
ngrok http ${PORT} --url=${NGROK_URL}

