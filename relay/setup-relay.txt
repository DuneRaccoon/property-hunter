#!/usr/bin/env bash
# dune-relay: family-home WireGuard exit + HTTP proxy for the Domain crawler.
# Run ONCE on the freshly-flashed family Pi (over SSH). Idempotent-ish.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "Re-running with sudo..."; exec sudo bash "$0" "$@"; fi

echo "[1/5] Installing wireguard + tinyproxy..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq wireguard wireguard-tools tinyproxy
modprobe wireguard || true

echo "[2/5] Writing WireGuard config..."
umask 077
cat > /etc/wireguard/wg0.conf <<WG
[Interface]
Address = 10.9.0.2/24
PrivateKey = cEYIhY0ph1lIDnMRsQtZ44XXZnRmQDMlNWmvOJ6lKW4=

[Peer]
# home Pi (static public IP)
PublicKey = 7YcNb64BkKU4zjuuPIRGm3H4kwhF91SYcZKyNZXhfzw=
Endpoint = 180.150.79.154:51820
AllowedIPs = 10.9.0.0/24
PersistentKeepalive = 25
WG
chmod 600 /etc/wireguard/wg0.conf

echo "[3/5] Configuring tinyproxy (tunnel-only)..."
cat > /etc/tinyproxy/tinyproxy.conf <<TP
User tinyproxy
Group tinyproxy
Port 8888
Listen 10.9.0.2
Timeout 600
Allow 10.9.0.0/24
ConnectPort 443
ConnectPort 80
LogLevel Info
TP

echo "[4/5] Enabling services..."
systemctl enable wg-quick@wg0
systemctl restart wg-quick@wg0
# tinyproxy binds the tunnel IP, so it must start AFTER wg0 (survives reboots)
mkdir -p /etc/systemd/system/tinyproxy.service.d
cat > /etc/systemd/system/tinyproxy.service.d/override.conf <<OV
[Unit]
After=wg-quick@wg0.service
Requires=wg-quick@wg0.service
[Service]
Restart=on-failure
RestartSec=5
OV
systemctl daemon-reload
systemctl enable tinyproxy
sleep 2
systemctl restart tinyproxy

echo "[5/5] Status:"
wg show || true
ss -tlnp | grep 8888 || echo "  (tinyproxy not yet bound - may need: systemctl restart tinyproxy after tunnel is up)"
echo "DONE. Relay is up. Home Pi should now see a handshake."
