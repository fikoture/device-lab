#!/usr/bin/env bash
# setup-power-saving.sh
# Makes a laptop server use less power when it runs on battery (for example while its charger is borrowed).
# Installs TLP, the standard Linux laptop power manager: on battery it puts the CPU, USB and Wi-Fi into
# power-saving modes; on the charger it goes back to normal by itself. The defaults are good, nothing to tune.
#
# Usage: sudo ./setup-power-saving.sh
# Check: sudo tlp-stat -s     (State = enabled, Mode = battery or AC)
#
# The screen is the biggest power user. On a headless server keep the lid closed (see setup-server-power.sh).

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

apt-get update
apt-get install -y tlp
systemctl enable --now tlp
tlp-stat -s | grep -E "State|Mode|Power source"
