#!/usr/bin/env bash
# setup-xtts.sh
# Installs RyanAI Voice (Coqui XTTS v2 text to speech, Turkish and English) as a systemd service on the
# AI server, on port 8020. Open WebUI can use it for "read aloud", and it has its own page for pasting
# a text and downloading the audio.
#
# Usage:  sudo ./setup-xtts.sh --agree-coqui-license
#
# LICENSE: the XTTS v2 model is under the Coqui Public Model License, which allows NON-COMMERCIAL use only.
# The script does not accept that license for you: you pass --agree-coqui-license yourself.
#
# GPU notes (GTX 1060 / Pascal, compute capability 6.1):
#   - New PyTorch builds for CUDA 12.8+ and 13 dropped Pascal; CUDA 12.6 builds still run on it. torchaudio
#     (needed by XTTS) has CUDA 12.6 wheels only up to 2.11, so torch and torchaudio are both pinned to 2.11.0 cu126,
#     and kept pinned for every later
#     pip install (constraints file), otherwise coqui-tts would pull a newer torch that runs on the CPU only.
#   - The model is loaded on first use and unloaded after 5 idle minutes, so Ollama gets the GPU back.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0 --agree-coqui-license" >&2
    exit 1
fi
if [ "${1:-}" != "--agree-coqui-license" ]; then
    echo "XTTS v2 is for non-commercial use only (Coqui Public Model License)."
    echo "If you accept that license, run again with: sudo $0 --agree-coqui-license"
    exit 1
fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DIR=/opt/ryanai-voice
SVC_USER=ryanai-voice
TORCH=2.11.0
INDEX=https://download.pytorch.org/whl/cu126

apt-get update
apt-get install -y python3-venv python3-dev ffmpeg

id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DIR" --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$DIR"
usermod -aG video,render "$SVC_USER" 2>/dev/null || true

python3 -m venv "$DIR/venv"
PIP="$DIR/venv/bin/pip"
"$PIP" install --upgrade pip
"$PIP" install "torch==$TORCH" "torchaudio==$TORCH" --index-url "$INDEX"
printf 'torch==%s\ntorchaudio==%s\n' "$TORCH" "$TORCH" > "$DIR/constraints.txt"
"$PIP" install -c "$DIR/constraints.txt" coqui-tts torchcodec fastapi "uvicorn[standard]" numpy

cp "$HERE/ai/xtts_server.py" "$DIR/xtts_server.py"

echo "== GPU check =="
"$DIR/venv/bin/python" - <<'PY'
import torch
ok = torch.cuda.is_available()
print("torch", torch.__version__, "cuda available:", ok)
if ok:
    cap = torch.cuda.get_device_capability(0)
    archs = torch.cuda.get_arch_list()
    print("GPU", torch.cuda.get_device_name(0), "capability", cap, "built for", archs)
    if not any(a in archs for a in ("sm_60", "sm_61")):
        print("WARNING: this torch build has no Pascal kernels; XTTS would fail or fall back to the CPU")
else:
    print("WARNING: no CUDA. XTTS will run on the CPU (slow). Check nvidia-smi.")
PY

ENV=/etc/ryanai-voice.env
if [ ! -f "$ENV" ]; then
    KEY="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
    printf 'XTTS_API_KEY=%s\nCOQUI_TOS_AGREED=1\nIDLE_UNLOAD_S=300\nHOME=%s\n' "$KEY" "$DIR" > "$ENV"
fi
chmod 600 "$ENV"
chown -R "$SVC_USER":"$SVC_USER" "$DIR"

cat > /etc/systemd/system/ryanai-voice.service <<EOF
[Unit]
Description=RyanAI Voice (XTTS v2 text to speech)
After=network-online.target
Wants=network-online.target

[Service]
User=$SVC_USER
WorkingDirectory=$DIR
EnvironmentFile=$ENV
ExecStart=$DIR/venv/bin/uvicorn xtts_server:app --host 0.0.0.0 --port 8020
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now ryanai-voice
sleep 3
systemctl is-active ryanai-voice
echo
echo "RyanAI Voice is on http://$(hostname -I | awk '{print $1}'):8020"
echo "Access key is in $ENV (sudo cat $ENV). Keep it private."
echo "The first request downloads the XTTS v2 model (about 1.8 GB) and takes a few minutes."
