#!/usr/bin/env bash
# Run on the desktop. Thin wrapper around docker compose so `docker compose down` always works.
# Usage:
#   ./run.sh [<pi-ip>:50051[,<pi2-ip>:50051...]]   start/rebuild (default: 172.31.227.194:50051)
#   ./run.sh down                                    stop and remove containers + network
#   ./run.sh clean                                   same as down, also deletes Prometheus/Grafana data volumes
set -euo pipefail
cd "$(dirname "$0")"

# Older versions of this script used plain `docker run`, which compose cannot see or remove.
remove_legacy() {
  for c in grpc-bridge prometheus grafana; do
    if [ -n "$(docker ps -aq -f "name=^${c}$" -f "label=com.docker.compose.project" 2>/dev/null)" ]; then
      continue  # owned by compose, leave it
    fi
    docker rm -f "$c" >/dev/null 2>&1 || true
  done
  docker network rm pi-monitor >/dev/null 2>&1 || true
}

case "${1:-}" in
  down)
    docker compose down --remove-orphans
    remove_legacy
    exit 0
    ;;
  clean)
    docker compose down --remove-orphans --volumes --rmi local
    remove_legacy
    docker volume rm grafana-data prometheus-data >/dev/null 2>&1 || true
    exit 0
    ;;
esac

mkdir -p images
export PI_TARGETS="${1:-172.31.227.194:50051}"

remove_legacy
echo "==> starting stack (pulling from $PI_TARGETS)"
docker compose up -d --build --remove-orphans

echo "Grafana:    http://localhost:3000  (admin/admin)"
echo "Prometheus: http://localhost:9090/targets"
echo "Images:     http://localhost:9110/images/  (newest file in $PWD/images)"
echo "Bridge log: docker compose logs -f grpc-bridge"
echo "Stop:       ./run.sh down   (or: docker compose down)"
