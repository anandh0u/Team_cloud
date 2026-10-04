#!/usr/bin/env bash
# Self-signed HTTPS certificate for the phone page (mic + camera need HTTPS).
# Usage: scripts/make_cert.sh [IP ...]   (default: this machine's current IPs)
# Browsers will still warn once ("not private"): tap Advanced -> Proceed.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/tls
ips=("$@")
[ ${#ips[@]} -eq 0 ] && read -ra ips <<< "$(hostname -I)"
san="DNS:localhost,DNS:$(hostname).local,IP:127.0.0.1"
for ip in "${ips[@]}"; do [[ $ip == *:* ]] || san="$san,IP:$ip"; done
openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=bedside-assistant" \
  -addext "subjectAltName=$san" -keyout data/tls/key.pem -out data/tls/cert.pem 2>/dev/null
chmod 600 data/tls/key.pem
echo "Certificate for $san written to data/tls/"
