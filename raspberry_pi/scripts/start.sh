#!/usr/bin/env bash
# (Re)start the backend and, with --online, a Cloudflare quick tunnel; then print the links.
# Usage: scripts/start.sh [--online]
# Quick-tunnel addresses change on every start. Set ACCESS_PASSWORD in .env before --online.
set -euo pipefail
cd "$(dirname "$0")/.."

pkill -f '^\.venv/bin/python run\.py$' || true
pkill -f 'cloudflared tunnel .*--url http://localhost' || true
for _ in $(seq 1 30); do ss -ltn | grep -qE ':8000 |:8443 ' || break; sleep 0.5; done

nohup .venv/bin/python run.py > server.log 2>&1 &
for _ in $(seq 1 60); do curl -s -o /dev/null http://localhost:8000/ && break; sleep 0.5; done
if ! ss -ltn | grep -q ':8000 '; then echo "Server did not start, see server.log:"; tail -5 server.log; exit 1; fi

ip=$(hostname -I | cut -d' ' -f1)
https_port=$(grep -E '^HTTPS_PORT=' .env | cut -d= -f2- || true)
echo "Bedside phone (same Wi-Fi): ${https_port:+https://$ip:$https_port/phone}"
echo "Dashboard (same Wi-Fi):     http://$ip:8000/dashboard"

if [ "${1:-}" = "--online" ]; then
  if ! grep -qE '^ACCESS_PASSWORD=.+' .env; then echo "Refusing to go online: set ACCESS_PASSWORD in .env first."; exit 1; fi
  cloudflared=$(command -v cloudflared || echo "$HOME/.local/bin/cloudflared")
  nohup "$cloudflared" tunnel --no-autoupdate --url http://localhost:8000 > tunnel.log 2>&1 &
  url=""
  for _ in $(seq 1 40); do
    url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' tunnel.log | head -1 || true)
    [ -n "$url" ] && grep -q "Registered tunnel connection" tunnel.log && break
    sleep 1
  done
  [ -n "$url" ] || { echo "Tunnel did not come up, see tunnel.log"; exit 1; }
  echo "Dashboard (anywhere):       $url/dashboard"
  echo "Phone page (anywhere):      $url/phone"
fi
