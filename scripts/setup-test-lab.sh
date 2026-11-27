#!/usr/bin/env bash
# setup-test-lab.sh
# Turns an Ubuntu Server machine into the test lab: the WebDevice portal + 6 simulated devices,
# started by systemd at boot and restarted on failure.
#
# Usage (on the test lab machine, after copying the project to ~/webdevice-sdet-project):
#   sudo ./setup-test-lab.sh            (lab version 1.0, the clean build)
#   sudo LAB_VERSION=1.1 ./setup-test-lab.sh   (version with hidden bugs)
#
# Copy the project from your laptop first:
#   rsync -av --exclude node_modules --exclude .venv --exclude __pycache__ \
#         ~/Desktop/webdevice-sdet-project <user>@<test-lab-ip>:~/

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

LAB_USER="${SUDO_USER:-ryan}"
LAB_DIR="$(getent passwd "$LAB_USER" | cut -d: -f6)/webdevice-sdet-project"
LAB_VERSION="${LAB_VERSION:-1.0}"

[ -f "$LAB_DIR/start_lab.sh" ] || { echo "Project not found at $LAB_DIR" >&2; exit 1; }

apt-get update
apt-get install -y python3-venv curl

sudo -u "$LAB_USER" python3 -m venv "$LAB_DIR/.venv"
sudo -u "$LAB_USER" "$LAB_DIR/.venv/bin/pip" install -q -r "$LAB_DIR/requirements.txt"

# The portal must listen on all interfaces so phones, tablets and Jenkins agents can reach it.
grep -q -- "--host 0.0.0.0" "$LAB_DIR/start_lab.sh" || \
    sed -i 's/--port 8000/--host 0.0.0.0 --port 8000/' "$LAB_DIR/start_lab.sh"

cat > /etc/systemd/system/webdevice-lab.service <<UNIT
[Unit]
Description=WebDevice SDET test lab (portal + 6 simulated devices)
After=network-online.target
Wants=network-online.target

[Service]
User=$LAB_USER
WorkingDirectory=$LAB_DIR
Environment=PATH=$LAB_DIR/.venv/bin:/usr/bin:/bin
ExecStart=$LAB_DIR/start_lab.sh $LAB_VERSION
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now webdevice-lab
sleep 5

echo
curl -s http://localhost:8000/api/health && echo
curl -s http://localhost:5001/api/status | head -c 120 && echo
IP="$(hostname -I | awk '{print $1}')"
echo "Test lab is up. Portal: http://$IP:8000   Devices: http://$IP:5001 ... 5006"
echo "Logs: journalctl -u webdevice-lab -f"
