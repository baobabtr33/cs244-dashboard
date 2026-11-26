#!/usr/bin/env bash
# Run on the Raspberry Pi without Docker. Uses uv for an isolated venv.
# Needs the proto/ and pi/ folders side by side. Usage: ./start.sh [stop]
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
PIDFILE=server.pid

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "==> stopping running server (pid $(cat "$PIDFILE"))"
  kill "$(cat "$PIDFILE")"
  sleep 1
fi
rm -f "$PIDFILE"
[ "${1:-}" = "stop" ] && exit 0

command -v uv >/dev/null || { echo "uv not found: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

echo "==> syncing deps"
uv sync -q

echo "==> generating gRPC stubs"
uv run -q python -m grpc_tools.protoc -I../proto --python_out=. --grpc_python_out=. ../proto/pimonitor.proto

echo "==> starting server on :50051 (log: $(pwd)/server.log)"
nohup uv run -q python -u server.py > server.log 2>&1 &
echo $! > "$PIDFILE"

sleep 2
cat server.log
echo "Pi gRPC target for desktop: $(hostname -I | awk '{print $1}'):50051"
