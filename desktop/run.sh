#!/usr/bin/env bash
# Start the desktop dashboard. Prompts for the Pi IP, then runs docker compose.
# Stop: docker compose down   (wipe data too: docker compose down -v)
set -euo pipefail
cd "$(dirname "$0")"

read -rp "Pi IP [172.31.227.194]: " PI_IP
PI_IP="${PI_IP:-172.31.227.194}"
[[ "$PI_IP" == *:* ]] || PI_IP="$PI_IP:50051"
export PI_TARGETS="$PI_IP"

mkdir -p images
docker compose up -d --build --remove-orphans

echo "Grafana:    http://localhost:3000  (admin/admin)"
echo "Prometheus: http://localhost:9090/targets"
echo "Images:     http://localhost:9110/images/"
