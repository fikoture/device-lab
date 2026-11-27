#!/usr/bin/env bash
# setup-ryanai-app.sh
# Installs RyanAI, our own small chat app, on the AI server (port 8090) as a systemd service.
# It talks straight to Ollama with the RyanAI routing rules (no hidden prompts, no background jobs), keeps
# each user's chats private, and reads answers aloud through RyanAI Voice if that is installed.
#
# Usage: sudo ./setup-ryanai-app.sh
# Then add users (asks for a password, at least 8 characters):
#   sudo ryanai-user adduser ryan --admin
#   sudo ryanai-user adduser <friend>
#   sudo ryanai-user users | passwd <name> | deluser <name>

set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo: sudo $0" >&2; exit 1; fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DIR=/opt/ryanai-app
DATA=/var/lib/ryanai
SVC_USER=ryanai-app

apt-get install -y python3-venv
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DIR" --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$DIR" "$DATA"
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --upgrade pip
"$DIR/venv/bin/pip" install fastapi "uvicorn[standard]" httpx
cp "$HERE/ai/ryanai_app.py" "$HERE/ai/ryanai_ui.html" "$DIR/"

ENV=/etc/ryanai-app.env
if [ ! -f "$ENV" ]; then
    KEY=""
    [ -f /etc/ryanai-voice.env ] && KEY="$(grep '^XTTS_API_KEY=' /etc/ryanai-voice.env | cut -d= -f2-)"
    cat > "$ENV" <<CONF
RYANAI_DATA=$DATA
OLLAMA_URL=http://127.0.0.1:11434
VOICE_URL=http://127.0.0.1:8020
XTTS_API_KEY=$KEY
FAST_MODEL=qwen3.5:4b
BIG_MODEL=qwen3.5:4b
CODE_MODEL=qwen2.5-coder:7b
VISION_MODEL=gemma4:e2b
FALLBACK_MODEL=llama3.2:3b
CONF
fi
chmod 600 "$ENV"
chown -R "$SVC_USER":"$SVC_USER" "$DIR" "$DATA"
chmod 700 "$DATA"

cat > /usr/local/bin/ryanai-user <<CMD
#!/usr/bin/env bash
exec sudo -u $SVC_USER env RYANAI_DATA=$DATA $DIR/venv/bin/python $DIR/ryanai_app.py "\$@"
CMD
chmod 755 /usr/local/bin/ryanai-user

cat > /etc/systemd/system/ryanai-app.service <<UNIT
[Unit]
Description=RyanAI chat app
After=network-online.target ollama.service

[Service]
User=$SVC_USER
WorkingDirectory=$DIR
EnvironmentFile=$ENV
ExecStart=$DIR/venv/bin/uvicorn ryanai_app:app --host 0.0.0.0 --port 8090
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now ryanai-app
sleep 2
systemctl is-active ryanai-app
echo "RyanAI is on http://$(hostname -I | awk '{print $1}'):8090"
echo "Add your account: sudo ryanai-user adduser ryan --admin"
