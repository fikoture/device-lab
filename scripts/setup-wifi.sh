#!/usr/bin/env bash
# setup-wifi.sh
# Connects an Ubuntu Server machine to Wi-Fi with netplan, without editing YAML by hand.
#
# Usage:   sudo ./setup-wifi.sh            (finds the Wi-Fi card by itself)
#          sudo ./setup-wifi.sh wlp1s0     (or give the card name)
#
# What it does:
#   1. Finds the Wi-Fi card (a network interface whose name starts with "wl").
#   2. Asks for the Wi-Fi name and password (the password is never shown or stored in this repo).
#   3. Moves away installer netplan files that match Wi-Fi by MAC address.
#      The systemd-networkd backend does not support Wi-Fi with "match", so these files
#      make "netplan apply" fail. They are backed up, not deleted.
#   4. Writes /etc/netplan/60-wifi.yaml with correct indentation and permissions (600).
#   5. Applies the config and waits for an IP address.
#
# Testing without touching the real system:
#   NETPLAN_DIR=/tmp/np DRY_RUN=1 ./setup-wifi.sh wlp1s0

set -euo pipefail

NETPLAN_DIR="${NETPLAN_DIR:-/etc/netplan}"
DRY_RUN="${DRY_RUN:-0}"

if [ "$DRY_RUN" != "1" ] && [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

IFACE="${1:-}"
if [ -z "$IFACE" ]; then
    IFACE="$(ls /sys/class/net 2>/dev/null | grep '^wl' | head -n 1 || true)"
fi
if [ -z "$IFACE" ]; then
    echo "No Wi-Fi card found. Check with: ls /sys/class/net" >&2
    exit 1
fi
echo "Wi-Fi card: $IFACE"

if [ "$DRY_RUN" != "1" ] && ! command -v wpa_supplicant >/dev/null 2>&1; then
    echo "wpasupplicant is not installed. Install it from the Ubuntu ISO or with a cable connection:" >&2
    echo "  sudo apt install wpasupplicant" >&2
    exit 1
fi

read -rp "Wi-Fi name (SSID): " SSID
read -rsp "Wi-Fi password: " PASS
echo
if [ -z "$SSID" ] || [ -z "$PASS" ]; then
    echo "Wi-Fi name and password cannot be empty." >&2
    exit 1
fi

# Escape backslash and double quote so the values are safe inside YAML double quotes.
yaml_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    printf '%s' "$s"
}
SSID_Y="$(yaml_escape "$SSID")"
PASS_Y="$(yaml_escape "$PASS")"

mkdir -p "$NETPLAN_DIR"

# Back up conflicting installer files (Wi-Fi matched by MAC address).
BACKUP_DIR="$NETPLAN_DIR/../netplan-backup-$(date +%Y%m%d-%H%M%S)"
for f in "$NETPLAN_DIR"/*.yaml; do
    [ -e "$f" ] || continue
    [ "$(basename "$f")" = "60-wifi.yaml" ] && continue
    if grep -q "macaddress" "$f"; then
        mkdir -p "$BACKUP_DIR"
        mv "$f" "$BACKUP_DIR/"
        echo "Moved conflicting file $(basename "$f") to $BACKUP_DIR"
    fi
done

CONF="$NETPLAN_DIR/60-wifi.yaml"
umask 077
cat > "$CONF" <<YAML
network:
  version: 2
  wifis:
    $IFACE:
      dhcp4: true
      access-points:
        "$SSID_Y":
          password: "$PASS_Y"
YAML
chmod 600 "$CONF"
echo "Wrote $CONF"

if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN=1: not applying."
    exit 0
fi

netplan generate
netplan apply

echo -n "Waiting for an IP address"
IP=""
for _ in $(seq 1 30); do
    IP="$(ip -4 -o addr show "$IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n 1)"
    [ -n "$IP" ] && break
    echo -n "."
    sleep 1
done
echo

if [ -n "$IP" ]; then
    echo "Connected. IP address: $IP"
    echo "From your laptop:  ssh $(logname 2>/dev/null || echo USER)@$IP"
    echo "Tip: reserve this IP in your router so it never changes."
else
    echo "No IP address after 30 seconds. Check the Wi-Fi name and password, then run the script again." >&2
    exit 1
fi
