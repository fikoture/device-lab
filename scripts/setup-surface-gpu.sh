#!/usr/bin/env bash
# setup-surface-gpu.sh
# Makes the NVIDIA GPU (GTX 1050/1060 in the keyboard base) usable on a Surface Book 2 running Ubuntu Server,
# so the machine can run local AI models (for example Ollama) on the GPU.
#
# Run it twice:
#   1st run: adds the linux-surface repository, installs the Surface kernel, asks you to reboot.
#   2nd run (after reboot, on the Surface kernel): installs the NVIDIA 580 server driver and checks it.
#
# Usage: sudo ./setup-surface-gpu.sh
#
# Notes from building this lab:
#   - On the stock Ubuntu kernel the GPU is not visible at all (lspci shows nothing).
#     The linux-surface kernel makes it appear (lspci: "GeForce GTX 1060 Mobile").
#   - The Surface screen must be attached to the keyboard base; the GPU lives in the base.
#   - Pascal GPUs (GTX 10xx) need the classic driver, not "-open", and the 580 branch is the
#     last NVIDIA branch that supports them.
#   - Secure Boot was disabled on this machine. With Secure Boot on, also install
#     linux-surface-secureboot-mok and enroll the key at the next boot (see the linux-surface wiki).

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

DRIVER_BRANCH="${DRIVER_BRANCH:-580}"

if ! uname -r | grep -q surface; then
    echo "== Phase 1: Surface kernel =="
    apt-get update
    apt-get upgrade -y
    if [ ! -f /etc/apt/trusted.gpg.d/linux-surface.gpg ]; then
        wget -qO - https://raw.githubusercontent.com/linux-surface/linux-surface/master/pkg/keys/surface.asc \
            | gpg --dearmor | dd of=/etc/apt/trusted.gpg.d/linux-surface.gpg status=none
    fi
    echo "deb [arch=amd64] https://pkg.surfacelinux.com/debian release main" > /etc/apt/sources.list.d/linux-surface.list
    apt-get update
    apt-get install -y linux-image-surface linux-headers-surface surface-control
    echo
    echo "Surface kernel installed. Reboot, then run this script again:"
    echo "  sudo reboot"
    exit 0
fi

echo "== Phase 2: NVIDIA driver (running kernel: $(uname -r)) =="
if ! lspci | grep -qi nvidia; then
    echo "No NVIDIA GPU visible. Make sure the screen is attached to the keyboard base, then reboot." >&2
    exit 1
fi
lspci | grep -i nvidia

apt-get install -y "nvidia-headless-${DRIVER_BRANCH}-server" "nvidia-utils-${DRIVER_BRANCH}-server"

echo
echo "Driver installed. If nvidia-smi fails below, reboot once and run: nvidia-smi"
nvidia-smi || true
