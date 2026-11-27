"""
RyanAI Voice, second engine: Chatterbox Multilingual (Resemble AI, MIT license) on the GPU.

Runs in its own venv (it pins different torch/transformers versions than XTTS) and listens only on
127.0.0.1:8021. The main RyanAI Voice server (xtts_server.py, port 8020) forwards requests here when the
"Chatterbox" engine is chosen, and asks this one to unload before it uses XTTS, so only one engine sits on
the 6 GB GPU at a time.

  GET  /health    status
  POST /speak     {"text", "language", "temperature"} -> WAV
  POST /unload    free the GPU now

Text is already normalized by the main server. It is split into sentences here with the same rules as XTTS.
Note: Resemble AI retired Chatterbox in 2026 (no more updates); every output carries their inaudible
Perth watermark.
"""
import io
import os
import threading
import time
import wave

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from voice_text import pieces

IDLE_UNLOAD_S = int(os.environ.get("IDLE_UNLOAD_S", "180"))
LANGS = {"ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms", "nl", "no", "pl",
         "pt", "ru", "sv", "sw", "tr", "zh"}


class Engine:
    def __init__(self):
        self.model = None
        self.sr = 24000
        self.lock = threading.Lock()
        self.last_used = 0.0
        threading.Thread(target=self._watch, daemon=True).start()

    def _load(self):
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        device = "cuda" if torch.cuda.is_available() else "cpu"
        try:   # newer source versions take t3_model; the PyPI release (0.1.7) does not
            self.model = ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model="v3")
        except TypeError:
            self.model = ChatterboxMultilingualTTS.from_pretrained(device=device)
        self.sr = self.model.sr
        self.device = device

    def unload(self):
        with self.lock:
            if self.model is not None:
                self.model = None
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:
                    pass

    def _watch(self):
        while True:
            time.sleep(15)
            if self.model is not None and time.time() - self.last_used > IDLE_UNLOAD_S:
                self.unload()

    def speak(self, text: str, language: str, temperature: float):
        import numpy as np
        with self.lock:
            if self.model is None:
                self._load()
            self.last_used = time.time()
            out = []
            for piece, pause in pieces(text):
                wav = self.model.generate(piece, language_id=language, temperature=temperature)
                out.append(wav.squeeze().detach().cpu().numpy().astype(np.float32))
                if pause:
                    out.append(np.zeros(int(self.sr * pause), dtype=np.float32))
                self.last_used = time.time()
            return (np.concatenate(out) if out else np.zeros(1, dtype=np.float32)), self.sr


engine = Engine()
app = FastAPI(title="RyanAI Voice - Chatterbox", version="0.1.1")


class SpeakBody(BaseModel):
    text: str
    language: str = "tr"
    temperature: float = Field(default=0.8, ge=0.1, le=1.5)


def to_wav(samples, sr: int) -> bytes:
    import numpy as np
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


@app.get("/health")
def health():
    return {"status": "ok", "loaded": engine.model is not None}


@app.post("/unload")
def unload():
    engine.unload()
    return {"unloaded": True}


@app.post("/speak")
def speak(body: SpeakBody):
    if not body.text.strip():
        raise HTTPException(400, detail="text is empty")
    if body.language not in LANGS:
        raise HTTPException(400, detail=f"Chatterbox does not support language '{body.language}'")
    samples, sr = engine.speak(body.text, body.language, body.temperature)
    return Response(to_wav(samples, sr), media_type="audio/wav")
