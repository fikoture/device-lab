#!/usr/bin/env bash
# setup-console.sh
# Makes the text console readable on high-resolution laptop screens (for example Surface Book, 3000x2000).
#
# Usage:   sudo ./setup-console.sh          (large font only)
#          sudo ./setup-console.sh 1500x1000 (large font + lower console resolution, needs a reboot)
#
# Also stops kernel info messages (like Intel dptf messages on Surface) from flooding the console.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

# 1. Largest console font (Terminus 16x32), permanent.
sed -i 's/^FONTFACE=.*/FONTFACE="Terminus"/; s/^FONTSIZE=.*/FONTSIZE="16x32"/' /etc/default/console-setup
FONT=/usr/share/consolefonts/Lat15-TerminusBold32x16.psf.gz
[ -e "$FONT" ] && setfont -C /dev/tty1 "$FONT" || true
echo "Console font set to Terminus 16x32."

# 2. Keep kernel info messages off the console (they are still in the log: dmesg / journalctl -k).
echo "kernel.printk = 3 4 1 3" > /etc/sysctl.d/20-quiet-console.conf
sysctl -q -p /etc/sysctl.d/20-quiet-console.conf
echo "Kernel console messages limited to errors."

# 3. Optional: lower console resolution so the text is even bigger.
RES="${1:-}"
if [ -n "$RES" ]; then
    if ! [[ "$RES" =~ ^[0-9]+x[0-9]+$ ]]; then
        echo "Resolution must look like 1500x1000" >&2
        exit 1
    fi
    if grep -q 'video=' /etc/default/grub; then
        sed -i -E "s/video=[0-9]+x[0-9]+/video=$RES/" /etc/default/grub
    else
        sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=\"/&video=$RES /" /etc/default/grub
    fi
    update-grub
    echo "Console resolution set to $RES. Reboot to apply: sudo reboot"
fi
