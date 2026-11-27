#!/usr/bin/env bash
# setup-jenkins-agent.sh
# Prepares a lab machine (the test-lab) to be a Jenkins SSH agent: the Jenkins controller logs in over SSH
# as the "jenkins" user, starts agent.jar, and runs test jobs here, next to the Android devices.
#
# Usage:  sudo ./setup-jenkins-agent.sh "ssh-ed25519 AAAA... jenkins@aiserver"
#   The argument is the controller's PUBLIC key (safe to share). Make it on the controller with:
#   sudo -u jenkins ssh-keygen -t ed25519 -N "" -f /var/lib/jenkins/.ssh/id_ed25519
#   sudo cat /var/lib/jenkins/.ssh/id_ed25519.pub
#
# What it does:
#   - Java 21 (agent.jar needs the same major Java as the controller), git, adb, Python venv
#   - "jenkins" user with home /home/jenkins (the agent's workspace lives here)
#   - only key login for that user (the key is the only way in; the user has no password)

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0 \"<controller public key>\"" >&2
    exit 1
fi
KEY="${1:-}"
case "$KEY" in
    ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*) ;;
    *) echo "Give the controller's PUBLIC key in quotes (starts with ssh-ed25519)." >&2; exit 1 ;;
esac

apt-get update
apt-get install -y openjdk-21-jre-headless git adb python3-venv python3-pip
java -version

id jenkins >/dev/null 2>&1 || useradd --create-home --home-dir /home/jenkins --shell /bin/bash jenkins
passwd -l jenkins >/dev/null    # no password login, key only
usermod -aG plugdev jenkins 2>/dev/null || true   # USB devices, if one is ever plugged in

install -d -m 700 -o jenkins -g jenkins /home/jenkins/.ssh
AUTH=/home/jenkins/.ssh/authorized_keys
touch "$AUTH"
grep -qxF "$KEY" "$AUTH" || echo "$KEY" >> "$AUTH"
chown jenkins:jenkins "$AUTH"
chmod 600 "$AUTH"

echo
echo "Agent ready. Check that the jenkins user sees the devices:"
echo "  sudo -u jenkins adb connect 192.168.1.190:5555 && sudo -u jenkins adb devices"
echo "(The tablet will ask \"Allow USB debugging?\" for this new user once: tick Always allow.)"
