#!/usr/bin/env bash
# Start Chess Coach. By default it only listens on this Mac.
# Custom address:  HOST=0.0.0.0 PORT=9000 ./run.sh
# The app has no login, so with HOST=0.0.0.0 anyone on your network can use it.
set -euo pipefail
cd "$(dirname "$0")"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
echo "Chess Coach: open http://$HOST:$PORT"
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
