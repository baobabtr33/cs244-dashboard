#!/usr/bin/env bash
# Run on the Raspberry Pi. Starts rpicam-vid + yolo predict under vision.py.
# Usage: ./start.sh [stop]        MODEL=yolo11n.pt ./start.sh   to pick a model
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
PIDFILE=vision.pid

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "==> stopping vision pipeline (pid $(cat "$PIDFILE"))"
  kill "$(cat "$PIDFILE")"
  while kill -0 "$(cat "$PIDFILE")" 2>/dev/null; do sleep 0.5; done
fi
rm -f "$PIDFILE"
[ "${1:-}" = "stop" ] && exit 0

echo "==> syncing ultralytics env (first run downloads torch, a few minutes)"
uv sync -q

echo "==> starting vision pipeline (logs: $(pwd)/logs/)"
nohup python3 -u vision.py > vision.log 2>&1 &
echo $! > "$PIDFILE"
sleep 3
cat vision.log
