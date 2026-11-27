#!/usr/bin/env bash
# setup-server-power.sh
# Keeps a laptop running as a server: closing the lid does nothing, and the machine never sleeps.
#
# Usage: sudo ./setup-server-power.sh
# Verify: close the lid, then from another machine: curl http://<server-ip>:<port>/...

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

mkdir -p /etc/systemd/logind.conf.d
printf "[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\nHandleLidSwitchDocked=ignore\n" \
    > /etc/systemd/logind.conf.d/no-lid-sleep.conf
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
systemctl restart systemd-logind

echo "Lid close is ignored and sleep is disabled."
systemctl is-enabled suspend.target sleep.target || true
