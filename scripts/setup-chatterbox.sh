#!/usr/bin/env bash
# setup-chatterbox.sh
# Adds Chatterbox Multilingual (Resemble AI, MIT license, Turkish included) as a second RyanAI Voice engine.
# Run setup-xtts.sh first. Chatterbox gets its own venv because it pins different torch/transformers
# versions than XTTS. It listens only on 127.0.0.1:8021; the main RyanAI Voice server (port 8020) forwards
# to it when the page's Engine is set to Chatterbox.
#
# Usage: sudo ./setup-chatterbox.sh
#
# Notes:
#   - torch/torchaudio stay on 2.11.0 + CUDA 12.6, the newest prebuilt build that still runs on Pascal GPUs
#     (GTX 10xx). Chatterbox accepts torch >= 2.9 on Python 3.14.
#   - Resemble AI retired Chatterbox in 2026: the code is public (MIT) but gets no more fixes.
#   - Every output carries Resemble's inaudible Perth watermark.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DIR=/opt/ryanai-voice
VENV="$DIR/venv-chatterbox"
SVC_USER=ryanai-voice
TORCH=2.11.0
INDEX=https://download.pytorch.org/whl/cu126

if [ ! -d "$DIR" ] || ! id "$SVC_USER" >/dev/null 2>&1; then
    echo "Run setup-xtts.sh first." >&2
    exit 1
fi
apt-get install -y git

python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install "torch==$TORCH" "torchaudio==$TORCH" --index-url "$INDEX"
printf 'torch==%s\ntorchaudio==%s\n' "$TORCH" "$TORCH" > "$DIR/constraints-chatterbox.txt"
"$VENV/bin/pip" install -c "$DIR/constraints-chatterbox.txt" chatterbox-tts fastapi "uvicorn[standard]" numpy
"$VENV/bin/python" -c "from chatterbox.mtl_tts import ChatterboxMultilingualTTS" && echo "Chatterbox import ok"

cp "$HERE/ai/chatterbox_server.py" "$HERE/ai/voice_text.py" "$DIR/"
chown -R "$SVC_USER":"$SVC_USER" "$DIR"

cat > /etc/systemd/system/ryanai-voice-chatterbox.service <<UNIT
[Unit]
Description=RyanAI Voice - Chatterbox Multilingual engine
After=network-online.target

[Service]
User=$SVC_USER
WorkingDirectory=$DIR
Environment=HOME=$DIR
Environment=IDLE_UNLOAD_S=180
ExecStart=$VENV/bin/uvicorn chatterbox_server:app --host 127.0.0.1 --port 8021
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now ryanai-voice-chatterbox
sleep 3
systemctl is-active ryanai-voice-chatterbox
echo "Chatterbox engine is on 127.0.0.1:8021. Choose it under Engine on the RyanAI Voice page."
echo "The first request downloads the model from Hugging Face (a few GB) and takes several minutes."
