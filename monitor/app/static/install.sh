#!/usr/bin/env bash
# Bootstrap the server-monitor agent on Linux.
# Downloaded via:  curl -fsSL https://<monitor>/install.sh | sudo bash -s -- --token <T> --hostname <H>
set -euo pipefail

MONITOR_URL="${MONITOR_URL:-https://monitor.lan}"
ARCH="$(uname -m)"
HOSTNAME="$(hostname)"
TOKEN=""
TOKEN_FILE="/etc/server-monitor-agent/token"
CA_FILE="/etc/server-monitor-agent/ca.pem"
BINDIR="/usr/local/bin"
UNIT_PATH="/etc/systemd/system/server-monitor-agent.service"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --token) TOKEN="$2"; shift 2 ;;
        --hostname) HOSTNAME="$2"; shift 2 ;;
        --monitor-url) MONITOR_URL="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$TOKEN" ]]; then
    echo "missing --token" >&2; exit 2
fi
if [[ "$EUID" -ne 0 ]]; then
    echo "must run as root (use sudo)" >&2; exit 2
fi

mkdir -p /etc/server-monitor-agent
chmod 700 /etc/server-monitor-agent

# 1. Trust the monitor's CA (one-time, used for all subsequent calls).
echo "==> downloading monitor CA"
curl -kfsSL "${MONITOR_URL}/ca.crt" -o "${CA_FILE}.tmp"
mv "${CA_FILE}.tmp" "$CA_FILE"
chmod 644 "$CA_FILE"

# 2. Download the agent binary using the now-pinned CA.
echo "==> downloading agent binary"
case "$ARCH" in
    x86_64|amd64) ARCH=x86_64 ;;
    aarch64|arm64) ARCH=aarch64 ;;
    *) echo "unsupported arch: $ARCH" >&2; exit 2 ;;
esac
TMPBIN="$(mktemp)"
curl -fsSL --cacert "$CA_FILE" \
    "${MONITOR_URL}/api/agent-binary?os=linux&arch=${ARCH}" -o "$TMPBIN"
chmod +x "$TMPBIN"
install -m 0755 "$TMPBIN" "${BINDIR}/server-monitor-agent"
rm -f "$TMPBIN"

# 3. Pre-register the server with the monitor (so the host appears even if enroll fails later).
"${BINDIR}/server-monitor-agent" \
    --monitor-url "$MONITOR_URL" --ca-bundle "$CA_FILE" --hostname "$HOSTNAME" \
    --token-file "$TOKEN_FILE" \
    enroll --enrollment-token "$TOKEN"

# 4. Install systemd unit.
cat > "$UNIT_PATH" <<UNIT
[Unit]
Description=Server Monitor Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=MONITOR_URL=${MONITOR_URL}
ExecStart=${BINDIR}/server-monitor-agent --monitor-url \${MONITOR_URL} --ca-bundle ${CA_FILE} --hostname ${HOSTNAME} --token-file ${TOKEN_FILE} run
Restart=on-failure
RestartSec=5
User=root
Group=root
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/etc/server-monitor-agent

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now server-monitor-agent
systemctl status server-monitor-agent --no-pager
echo "==> done"
