#!/usr/bin/env bash
# Remove the server-monitor agent from a Linux host.
# Usage:
#   curl -fsSL http://<monitor>/uninstall.sh | sudo bash
#
# This is a local-only cleanup. The server's row will remain in the monitor's
# database with an "agent offline" badge until an operator deletes it manually
# (see README "Removing a server").
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
    echo "must run as root (use sudo)" >&2; exit 2
fi

UNIT_PATH="/etc/systemd/system/server-monitor-agent.service"
BIN_PATH="/usr/local/bin/server-monitor-agent"
DATA_DIR="/etc/server-monitor-agent"

echo "==> stopping service"
systemctl stop server-monitor-agent 2>/dev/null || true
systemctl disable server-monitor-agent 2>/dev/null || true

echo "==> removing systemd unit"
rm -f "$UNIT_PATH"
systemctl daemon-reload

echo "==> removing binary and data"
rm -f "$BIN_PATH"
rm -rf "$DATA_DIR"

echo "==> done"
echo
echo "Note: this host will continue to appear on the monitor dashboard with"
echo "      'agent offline' until an operator removes it from the SQLite DB"
echo "      (see README, 'Removing a server')."
