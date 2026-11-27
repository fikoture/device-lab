#!/usr/bin/env bash
# setup-jenkins.sh
# Installs Jenkins LTS (official Jenkins apt repository) with Java 21 as a systemd service on port 8080.
# Used on the AI server (hostname JenkinsAiServer, formerly aiserver); works on any Ubuntu/Debian lab machine.
#
# Usage:  sudo ./setup-jenkins.sh
#
# After it finishes, open http://<server-ip>:8080 and paste the one-time unlock password that the
# script tells you how to read. Then choose "Install suggested plugins" and create your admin user.
#
# Security: Jenkins can run any command on this machine. Keep port 8080 on the home network only:
# no port forwarding. Remote access only through Tailscale or Cloudflare Tunnel + Access.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

if ss -ltn | grep -q ':8080 '; then
    echo "Port 8080 is already in use. Stop that service first (ss -ltnp | grep 8080)." >&2
    exit 1
fi

apt-get update
apt-get install -y fontconfig openjdk-21-jre-headless wget git
java -version

mkdir -p /etc/apt/keyrings
wget -qO /etc/apt/keyrings/jenkins-keyring.asc https://pkg.jenkins.io/debian-stable/jenkins.io-2026.key
echo "deb [signed-by=/etc/apt/keyrings/jenkins-keyring.asc] https://pkg.jenkins.io/debian-stable binary/" \
    > /etc/apt/sources.list.d/jenkins.list
apt-get update
apt-get install -y jenkins

systemctl enable --now jenkins
echo "Waiting for Jenkins to start (first start takes about a minute)..."
for _ in $(seq 1 60); do
    [ -f /var/lib/jenkins/secrets/initialAdminPassword ] && break
    sleep 3
done
systemctl is-active jenkins

echo
echo "Jenkins is on http://$(hostname -I | awk '{print $1}'):8080"
echo "Read the one-time unlock password yourself with:"
echo "  sudo cat /var/lib/jenkins/secrets/initialAdminPassword"
echo "Then: Install suggested plugins > create your admin user."
