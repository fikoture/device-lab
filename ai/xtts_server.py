"""
RyanAI Voice: text to speech with Coqui XTTS v2 on the AI server's GPU.

What it serves (port 8020):
  GET  /                    a simple page: paste text, pick language and voice, listen and download
  GET  /v1/voices           the voices this server has
  POST /v1/audio/speech     OpenAI style endpoint, so Open WebUI's "read aloud" can use it
  GET  /health              status, for checks and tests

Turkish and English (plus the other XTTS languages). The text is split into sentences here, not by XTTS:
XTTS often garbles the end of a sentence at the full stop (extra syllables, swallowed words), mostly in
languages other than English. Each sentence is spoken on its own without its final full stop, with a short
pause between sentences and a longer one between paragraphs. Very long sentences are split at commas.
The model sits on the GPU only while it is used: after IDLE_UNLOAD_S seconds without a request it is
unloaded, so Ollama gets the whole GPU back. The next request loads it again (about 10 to 20 s).

Security: every request needs the API key (header "Authorization: Bearer <key>" or "X-API-Key").
The key comes from the XTTS_API_KEY environment variable. Never expose this port to the internet.

LICENSE: XTTS v2 weights are under the Coqui Public Model License (non-commercial use only).
COQUI_TOS_AGREED=1 must be set by the person who accepts that license.

Run: XTTS_API_KEY=... COQUI_TOS_AGREED=1 uvicorn xtts_server:app --host 0.0.0.0 --port 8020
"""
import io
import os
import re
import secrets
import subprocess
import threading
import time
import wave

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

API_KEY = os.environ.get("XTTS_API_KEY", "")
IDLE_UNLOAD_S = int(os.environ.get("IDLE_UNLOAD_S", "300"))
MAX_CHARS = int(os.environ.get("MAX_CHARS", "5000"))
SAMPLE_RATE = 24000
LANGS = ["tr", "en", "de", "fr", "es", "it", "pt", "pl", "nl", "ru", "ar", "cs", "zh-cn", "hu", "ko", "ja", "hi"]
# A few of the built-in XTTS v2 speakers, under easy names. OpenAI voice names map onto them too.
VOICES = {
    "female-1": "Ana Florence", "female-2": "Claribel Dervla", "female-3": "Daisy Studious",
    "male-1": "Damien Black", "male-2": "Craig Gutsy", "male-3": "Viktor Eka",
}
SPEAKERS_FILE = os.path.join(os.environ.get("HOME", "/opt/ryanai-voice"), ".local", "share", "tts",
                             "tts_models--multilingual--multi-dataset--xtts_v2", "speakers_xtts.pth")


_SPEAKER_CACHE = []


def load_speaker_names():
    """All built-in XTTS v2 speakers, read from the model's speaker file (small, no GPU needed).
    Falls back to the six default voices if the model has not been downloaded yet."""
    if _SPEAKER_CACHE:
        return _SPEAKER_CACHE
    try:
        import torch
        data = torch.load(SPEAKERS_FILE, map_location="cpu", weights_only=False)
        names = sorted(data.keys())
        if names:
            _SPEAKER_CACHE.extend(names)
            return names
    except Exception:
        pass
    return sorted(VOICES.values())


def resolve_voice(name: str):
    """Accepts an easy name (female-1), an OpenAI name (alloy) or a full speaker name (Ana Florence)."""
    name = OPENAI_NAMES.get(name, name)
    if name in VOICES:
        return VOICES[name]
    if name in load_speaker_names():
        return name
    return None


OPENAI_NAMES = {"alloy": "female-1", "nova": "female-2", "shimmer": "female-3",
                "onyx": "male-1", "echo": "male-2", "fable": "male-3"}
TURKISH_LETTERS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
MAX_PIECE = 200            # XTTS gets unreliable on very long pieces
PAUSE_SENTENCE_S = 0.30
PAUSE_PARAGRAPH_S = 0.70


def pieces(text: str):
    """Text -> list of (piece, pause_after_seconds). Final full stops are dropped; ? and ! are kept."""
    out = []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for pi, para in enumerate(paragraphs):
        para = " ".join(para.split())
        sentences = [x for x in SENTENCE_END.split(para) if x.strip()]
        for si, sent in enumerate(sentences):
            parts = [sent]
            if len(sent) > MAX_PIECE:
                parts, cur = [], ""
                for chunk in re.split(r"(?<=[,;:])\s+", sent):
                    if cur and len(cur) + len(chunk) + 1 > MAX_PIECE:
                        parts.append(cur)
                        cur = chunk
                    else:
                        cur = f"{cur} {chunk}".strip()
                if cur:
                    parts.append(cur)
            for k, part in enumerate(parts):
                part = part.strip().rstrip(".").rstrip(",;:").strip()
                if not part:
                    continue
                last_in_sentence = k == len(parts) - 1
                last_in_para = last_in_sentence and si == len(sentences) - 1
                pause = PAUSE_PARAGRAPH_S if last_in_para and pi < len(paragraphs) - 1 else (
                    PAUSE_SENTENCE_S if last_in_sentence else 0.12)
                out.append((part, pause))
    if out:
        out[-1] = (out[-1][0], 0.0)
    return out


class Engine:
    """Loads XTTS on first use and unloads it after a quiet period. Swappable in tests."""

    def __init__(self):
        self.tts = None
        self.lock = threading.Lock()
        self.last_used = 0.0
        threading.Thread(target=self._watch, daemon=True).start()

    def _load(self):
        import torch
        from TTS.api import TTS
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
        self.device = device

    def _watch(self):
        while True:
            time.sleep(15)
            with self.lock:
                if self.tts is not None and time.time() - self.last_used > IDLE_UNLOAD_S:
                    self.tts = None
                    try:
                        import torch
                        torch.cuda.empty_cache()
                    except Exception:
                        pass

    def status(self):
        return {"loaded": self.tts is not None, "device": getattr(self, "device", None)}

    def speak(self, text: str, voice: str, language: str, speed: float):
        import numpy as np
        with self.lock:
            if self.tts is None:
                self._load()
            self.last_used = time.time()
            audio = []
            for piece, pause in pieces(text):
                wav = self.tts.tts(text=piece, speaker=voice, language=language,
                                   split_sentences=False, speed=speed)
                audio.append(np.asarray(wav, dtype=np.float32))
                if pause:
                    audio.append(np.zeros(int(SAMPLE_RATE * pause), dtype=np.float32))
                self.last_used = time.time()
            return np.concatenate(audio) if audio else np.zeros(1, dtype=np.float32)


engine = Engine()
app = FastAPI(title="RyanAI Voice", version="0.3.0")


def check_key(authorization: str, x_api_key: str):
    given = x_api_key or (authorization[7:] if authorization.lower().startswith("bearer ") else "")
    if not API_KEY:
        raise HTTPException(503, detail="server has no XTTS_API_KEY set")
    if not given or not secrets.compare_digest(given, API_KEY):
        raise HTTPException(401, detail="wrong or missing API key")


def to_wav(samples) -> bytes:
    import numpy as np
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def to_mp3(wav_bytes: bytes) -> bytes:
    p = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", "-b:a", "128k", "-f", "mp3", "pipe:1"],
                       input=wav_bytes, capture_output=True, timeout=120)
    if p.returncode != 0:
        raise HTTPException(500, detail="mp3 conversion failed (is ffmpeg installed?)")
    return p.stdout


def pick_language(text: str, language: str) -> str:
    if language and language != "auto":
        if language not in LANGS:
            raise HTTPException(400, detail=f"language must be one of: auto, {', '.join(LANGS)}")
        return language
    return "tr" if TURKISH_LETTERS.search(text) else "en"


class SpeechBody(BaseModel):
    input: str
    model: str = "xtts-v2"
    voice: str = "female-1"
    response_format: str = "mp3"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    language: str = "auto"


@app.get("/health")
def health():
    return {"status": "ok", "engine": engine.status(), "voices": list(VOICES), "languages": ["auto"] + LANGS}


@app.get("/v1/voices")
def voices(authorization: str = Header(default=""), x_api_key: str = Header(default="")):
    check_key(authorization, x_api_key)
    return {"voices": load_speaker_names(), "aliases": VOICES}


@app.post("/v1/audio/speech")
def speech(body: SpeechBody, authorization: str = Header(default=""), x_api_key: str = Header(default="")):
    check_key(authorization, x_api_key)
    text = body.input.strip()
    if not text:
        raise HTTPException(400, detail="input is empty")
    if len(text) > MAX_CHARS:
        raise HTTPException(400, detail=f"input is longer than {MAX_CHARS} characters; split it into parts")
    voice = resolve_voice(body.voice)
    if voice is None:
        raise HTTPException(400, detail="unknown voice; see /v1/voices for the list")
    fmt = body.response_format.lower()
    if fmt not in ("mp3", "wav"):
        raise HTTPException(400, detail="response_format must be mp3 or wav")
    language = pick_language(text, body.language)
    started = time.time()
    audio = to_wav(engine.speak(text, voice, language, body.speed))
    if fmt == "mp3":
        audio = to_mp3(audio)
    headers = {"X-Language": language, "X-Voice": voice.encode("ascii", "replace").decode(), "X-Seconds": f"{time.time() - started:.1f}",
               "Content-Disposition": f'attachment; filename="ryanai-voice.{fmt}"'}
    return Response(audio, media_type="audio/mpeg" if fmt == "mp3" else "audio/wav", headers=headers)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RyanAI Voice</title>
<style>
  :root { --bg:#16191d; --panel:#1f2329; --ink:#e8ecf0; --steel:#98a3ae; --line:#323840; --signal:#4a7dff; --bad:#ff7a6b; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 760px; margin: 0 auto; padding: 36px 18px 60px; }
  h1 { font-size: 28px; margin: 0 0 4px; }
  .sub { color: var(--steel); margin: 0 0 22px; }
  label { display:block; font-size:14px; color:var(--steel); margin: 14px 0 6px; }
  textarea, select, input { width:100%; background:var(--panel); color:var(--ink); border:1px solid var(--line); border-radius:8px; padding:12px; font:inherit; }
  textarea { min-height: 220px; resize: vertical; }
  .row { display:grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
  button { margin-top: 18px; width:100%; padding: 13px; border:0; border-radius:8px; background:var(--signal); color:#fff; font:600 16px system-ui; cursor:pointer; }
  button:disabled { opacity:.5; cursor:wait; }
  .count { text-align:right; font-size:13px; color:var(--steel); margin-top:4px; }
  .out { margin-top: 22px; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; display:none; }
  .out audio { width:100%; margin: 8px 0 12px; }
  .out a { color: var(--signal); font-weight: 600; }
  .msg { margin-top: 12px; color: var(--steel); font-size: 14px; } .msg.bad { color: var(--bad); }
  @media (max-width: 600px) { .row { grid-template-columns: 1fr; } }
</style></head><body><main>
<h1>RyanAI Voice</h1>
<p class="sub">Paste a text, choose a voice, and get a Turkish or English audio file. Runs on Ryan's own server.</p>
<label for="key">Access key</label><input id="key" type="password" placeholder="Access key" autocomplete="off">
<label for="text">Text</label><textarea id="text" placeholder="Metni buraya yapistirin / Paste the text here"></textarea>
<div class="count" id="count">0 / %MAX%</div>
<div class="row">
  <div><label for="lang">Language</label><select id="lang"><option value="auto">Detect</option><option value="tr">Turkish</option><option value="en">English</option></select></div>
  <div><label for="voice">Voice</label><select id="voice">%VOICES%</select></div>
  <div><label for="fmt">File</label><select id="fmt"><option value="mp3">MP3</option><option value="wav">WAV</option></select></div>
</div>
<button id="go">Create audio</button>
<button id="try" style="background:transparent;border:1px solid var(--line);color:var(--ink);margin-top:10px">Try this voice with a short sample</button>
<div class="msg" id="msg" role="status"></div>
<div class="out" id="out"><audio id="player" controls></audio><a id="dl" download>Download the file</a></div>
</main><script>
const $ = id => document.getElementById(id);
try { $('key').value = localStorage.getItem('voice_key') || ''; } catch (e) {}
$('text').oninput = () => $('count').textContent = $('text').value.length + ' / %MAX%';
$('try').onclick = () => { const saved = $('text').value;
  $('text').value = $('lang').value === 'en' ? 'Hello, this is how this voice sounds.' : 'Merhaba, bu sesin nasıl duyulduğunu dinliyorsunuz.';
  $('go').onclick().finally(() => { $('text').value = saved; $('text').oninput(); }); };
$('go').onclick = async () => {
  const text = $('text').value.trim(); const m = $('msg');
  if (!text) { m.className = 'msg bad'; m.textContent = 'Paste a text first.'; return; }
  try { localStorage.setItem('voice_key', $('key').value); } catch (e) {}
  $('go').disabled = true; m.className = 'msg'; m.textContent = 'Creating the audio. The first one after a break takes about 20 seconds longer.';
  const t = Date.now();
  try {
    const r = await fetch('/v1/audio/speech', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': $('key').value },
      body: JSON.stringify({ input: text, voice: $('voice').value, language: $('lang').value, response_format: $('fmt').value }) });
    if (!r.ok) { let d = ''; try { d = (await r.json()).detail; } catch (e) {} throw new Error(d || ('HTTP ' + r.status)); }
    const blob = await r.blob(); const url = URL.createObjectURL(blob);
    $('player').src = url; $('dl').href = url; $('dl').download = 'ryanai-voice.' + $('fmt').value;
    $('out').style.display = 'block';
    m.textContent = 'Done in ' + Math.round((Date.now() - t) / 1000) + ' s. Language: ' + (r.headers.get('X-Language') || '-') + '.';
  } catch (e) { m.className = 'msg bad'; m.textContent = 'Could not create the audio: ' + e.message; }
  $('go').disabled = false;
};
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def page():
    opts = "".join(f'<option value="{n}">{n}</option>' for n in load_speaker_names())
    return PAGE.replace("%VOICES%", opts).replace("%MAX%", str(MAX_CHARS))


@app.exception_handler(HTTPException)
def http_error(_, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
