"""
RyanAI: a small, fast chat app for the home AI server. Only what we need, nothing hidden.

- Accounts: each person sees only their own chats. Users are added with `python ryanai_app.py adduser <name>`.
- Chat: answers stream word by word from Ollama. "Auto" picks the model per message with the RyanAI rules
  (image -> vision model, code -> code model, long/"analyze" -> thinks first, everything else -> fast model).
  Only the conversation goes to the model: no hidden tool prompts, no background title/tag jobs.
- Read aloud: proxies RyanAI Voice (port 8020) with the server's key, so the key never reaches the browser.

Run: RYANAI_DATA=/var/lib/ryanai uvicorn ryanai_app:app --host 0.0.0.0 --port 8090
Security: session cookie is HttpOnly + SameSite=Strict, logins are rate limited, every chat query is scoped to
the logged-in user. LAN only: never forward this port to the internet.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from typing import List, Optional

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

DATA = os.environ.get("RYANAI_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
DB_PATH = os.path.join(DATA, "ryanai.db")
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
VOICE = os.environ.get("VOICE_URL", "http://127.0.0.1:8020")
VOICE_KEY = os.environ.get("XTTS_API_KEY", "")
SESSION_DAYS = 30
MODELS = {
    "quick": os.environ.get("FAST_MODEL", "qwen3.5:4b"),
    "deep": os.environ.get("BIG_MODEL", "qwen3.5:4b"),
    "code": os.environ.get("CODE_MODEL", "qwen2.5-coder:7b"),
    "image": os.environ.get("VISION_MODEL", "gemma4:e2b"),
}
FALLBACK = os.environ.get("FALLBACK_MODEL", "llama3.2:3b")
KEEP_TURNS = 12
# Short on purpose (about 80 tokens): small models get slower and worse at Turkish with long instructions.
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT", (
    "You are RyanAI, a private assistant running on Ryan's own home server. "
    "Do not say which company made you or which model you are unless asked. "
    "Always answer in the user's language; in Turkish write natural, correct Turkish. "
    "Be clear and to the point. Do not invent facts; say so when you are not sure. "
    "For health, legal or money questions give useful general information and add one short line "
    "suggesting a professional for personal decisions."))
MAX_MESSAGE = 20000
MAX_IMAGE_B64 = 8_000_000

CODE_HINTS = re.compile(
    r"```|\bdef |\bclass |\bimport |\bfunction\b|\bconst |=>|traceback|exception|stack trace|syntaxerror|"
    r"\bpython\b|\bjavascript\b|\btypescript\b|\bjava\b|\bsql\b|\bregex\b|\bbash\b|\bplaywright\b|\bpytest\b|"
    r"\bkod\b|\bkodu\b|\bfonksiyon|\bhata mesaj", re.IGNORECASE)
DEEP_HINTS = re.compile(
    r"\banaly[sz]e\b|\bin detail\b|\bstep by step\b|\bcompare\b|\bplan\b|\bstrategy\b|\bwhy\b|"
    r"\banaliz|\bdetayl|\badim adim|\badım adım|\bkarsilastir|\bkarşılaştır|\bneden\b|\bstrateji", re.IGNORECASE)


def route(text: str, has_image: bool):
    if has_image:
        return "image"
    if CODE_HINTS.search(text):
        return "code"
    if len(text) > 1200 or DEEP_HINTS.search(text):
        return "deep"
    return "quick"


# ------------------------------------------------------------------ database
def db():
    os.makedirs(DATA, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    con = db()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, pw TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id)
            ON DELETE CASCADE, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id)
            ON DELETE CASCADE, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES chats(id)
            ON DELETE CASCADE, role TEXT NOT NULL, content TEXT NOT NULL, thinking TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', seconds REAL NOT NULL DEFAULT 0,
            has_image INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id, updated);
        CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
    """)
    con.commit()
    con.close()


def hash_pw(pw: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_pw(pw: str, stored: str) -> bool:
    try:
        salt_b64, _ = stored.split("$", 1)
        return hmac.compare_digest(hash_pw(pw, base64.b64decode(salt_b64)), stored)
    except Exception:
        return False


# ------------------------------------------------------------------ auth
FAILED = {}           # name -> [timestamps]  (in memory; resets on restart)


def current_user(ryanai_session: str = Cookie(default="")):
    if not ryanai_session:
        raise HTTPException(401, detail="not signed in")
    con = db()
    row = con.execute("SELECT u.id, u.name, u.is_admin FROM sessions s JOIN users u ON u.id = s.user_id "
                      "WHERE s.token = ? AND s.expires > ?", (ryanai_session, time.time())).fetchone()
    con.close()
    if not row:
        raise HTTPException(401, detail="session expired, sign in again")
    return dict(row)


def own_chat(con, chat_id: int, user_id: int):
    row = con.execute("SELECT * FROM chats WHERE id = ? AND user_id = ?", (chat_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, detail="chat not found")
    return row


app = FastAPI(title="RyanAI", version="0.1.0")
init_db()


class LoginBody(BaseModel):
    name: str
    password: str


@app.post("/api/login")
def login(body: LoginBody, response: Response):
    name = body.name.strip().lower()
    now = time.time()
    recent = [t for t in FAILED.get(name, []) if now - t < 600]
    if len(recent) >= 5:
        raise HTTPException(429, detail="too many wrong passwords, wait 10 minutes")
    con = db()
    row = con.execute("SELECT id, pw FROM users WHERE name = ?", (name,)).fetchone()
    if not row or not check_pw(body.password, row["pw"]):
        con.close()
        FAILED[name] = recent + [now]
        raise HTTPException(401, detail="wrong name or password")
    FAILED.pop(name, None)
    token = secrets.token_urlsafe(32)
    con.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token, row["id"], now + SESSION_DAYS * 86400))
    con.execute("DELETE FROM sessions WHERE expires < ?", (now,))
    con.commit()
    con.close()
    response.set_cookie("ryanai_session", token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict")
    return {"name": name}


@app.post("/api/logout")
def logout(response: Response, ryanai_session: str = Cookie(default="")):
    con = db()
    con.execute("DELETE FROM sessions WHERE token = ?", (ryanai_session,))
    con.commit()
    con.close()
    response.delete_cookie("ryanai_session")
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"name": user["name"], "admin": bool(user["is_admin"])}


# ------------------------------------------------------------------ chats
@app.get("/api/chats")
def list_chats(user=Depends(current_user)):
    con = db()
    rows = [dict(r) for r in con.execute("SELECT id, title, updated FROM chats WHERE user_id = ? ORDER BY updated DESC "
                                         "LIMIT 200", (user["id"],))]
    con.close()
    return {"chats": rows}


@app.post("/api/chats")
def new_chat(user=Depends(current_user)):
    con = db()
    now = time.time()
    cur = con.execute("INSERT INTO chats (user_id, title, created, updated) VALUES (?, 'New chat', ?, ?)",
                      (user["id"], now, now))
    con.commit()
    cid = cur.lastrowid
    con.close()
    return {"id": cid, "title": "New chat"}


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: int, user=Depends(current_user)):
    con = db()
    chat = own_chat(con, chat_id, user["id"])
    msgs = [dict(r) for r in con.execute("SELECT id, role, content, thinking, model, reason, seconds, has_image "
                                         "FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,))]
    con.close()
    return {"id": chat["id"], "title": chat["title"], "messages": msgs}


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: int, user=Depends(current_user)):
    con = db()
    own_chat(con, chat_id, user["id"])
    con.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    con.commit()
    con.close()
    return {"deleted": chat_id}


@app.get("/api/models")
async def models(user=Depends(current_user)):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            names = sorted(m["name"] for m in (await c.get(f"{OLLAMA}/api/tags")).json().get("models", []))
    except Exception:
        names = []
    return {"auto": MODELS, "models": names}


class SendBody(BaseModel):
    text: str = Field(default="", max_length=MAX_MESSAGE)
    image: Optional[str] = None          # base64, no data: prefix
    model: str = "auto"


_caps = {}


async def can_think(client: httpx.AsyncClient, model: str) -> bool:
    if model not in _caps:
        try:
            r = await client.post(f"{OLLAMA}/api/show", json={"model": model}, timeout=10)
            _caps[model] = "thinking" in (r.json().get("capabilities") or [])
        except Exception:
            return False
    return _caps[model]


def sse(obj) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


@app.post("/api/chats/{chat_id}/send")
async def send(chat_id: int, body: SendBody, user=Depends(current_user)):
    text = body.text.strip()
    if not text and not body.image:
        raise HTTPException(400, detail="write a message first")
    if body.image and len(body.image) > MAX_IMAGE_B64:
        raise HTTPException(400, detail="image is too large (max about 6 MB)")
    con = db()
    chat = own_chat(con, chat_id, user["id"])
    history = [dict(r) for r in con.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id",
                                            (chat_id,))][-KEEP_TURNS:]
    now = time.time()
    con.execute("INSERT INTO messages (chat_id, role, content, has_image, created) VALUES (?, 'user', ?, ?, ?)",
                (chat_id, text, 1 if body.image else 0, now))
    if chat["title"] == "New chat":
        con.execute("UPDATE chats SET title = ? WHERE id = ?", ((text or "Image")[:60], chat_id))
    con.execute("UPDATE chats SET updated = ? WHERE id = ?", (now, chat_id))
    con.commit()
    con.close()

    reason = route(text, bool(body.image))
    model = MODELS[reason] if body.model == "auto" else body.model
    think = reason == "deep"
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}] if SYSTEM_PROMPT else []
    msgs += [{"role": m["role"], "content": m["content"]} for m in history]
    user_msg = {"role": "user", "content": text}
    if body.image:
        user_msg["images"] = [body.image]
    msgs.append(user_msg)

    async def stream():
        started = time.time()
        answer, thought, used, err = [], [], model, None
        yield sse({"type": "meta", "model": model, "reason": reason if body.model == "auto" else "manual"})
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=180, write=30, pool=10)) as client:
            for attempt, m in enumerate([model, FALLBACK] if model != FALLBACK else [model]):
                payload = {"model": m, "messages": msgs, "stream": True}
                if await can_think(client, m):
                    payload["think"] = think and attempt == 0
                try:
                    async with client.stream("POST", f"{OLLAMA}/api/chat", json=payload) as r:
                        if r.status_code != 200:
                            raise RuntimeError(f"HTTP {r.status_code}")
                        async for line in r.aiter_lines():
                            if not line:
                                continue
                            chunk = json.loads(line)
                            if chunk.get("error"):
                                raise RuntimeError(chunk["error"])
                            msg = chunk.get("message") or {}
                            if msg.get("thinking"):
                                thought.append(msg["thinking"])
                                yield sse({"type": "think", "text": msg["thinking"]})
                            if msg.get("content"):
                                answer.append(msg["content"])
                                yield sse({"type": "text", "text": msg["content"]})
                            if chunk.get("done"):
                                break
                    used, err = m, None
                    break
                except Exception as e:
                    err = f"{m} failed: {e}"
                    if attempt == 0 and model != FALLBACK:
                        yield sse({"type": "notice", "text": f"{m} failed, answering with {FALLBACK}"})
                        answer, thought = [], []
        seconds = round(time.time() - started, 1)
        content = "".join(answer) if answer else (f"No model could answer. {err}" if err else "")
        con2 = db()
        con2.execute("INSERT INTO messages (chat_id, role, content, thinking, model, reason, seconds, created) "
                     "VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?)",
                     (chat_id, content, "".join(thought), used, reason, seconds, time.time()))
        con2.execute("UPDATE chats SET updated = ? WHERE id = ?", (time.time(), chat_id))
        con2.commit()
        con2.close()
        yield sse({"type": "done", "model": used, "seconds": seconds, "error": err if not answer else None})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class SpeakBody(BaseModel):
    text: str = Field(max_length=5000)


@app.post("/api/speak")
async def speak(body: SpeakBody, user=Depends(current_user)):
    if not VOICE_KEY:
        raise HTTPException(503, detail="RyanAI Voice is not configured on this server")
    clean = re.sub(r"```.*?```", " ", body.text, flags=re.S)
    clean = re.sub(r"[*_#>`]", "", clean).strip()
    if not clean:
        raise HTTPException(400, detail="nothing to read")
    try:
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{VOICE}/v1/audio/speech", headers={"X-API-Key": VOICE_KEY},
                             json={"input": clean[:5000], "response_format": "mp3"})
    except Exception:
        raise HTTPException(502, detail="RyanAI Voice is not reachable")
    if r.status_code != 200:
        raise HTTPException(502, detail=f"RyanAI Voice: {r.text[:200]}")
    return Response(r.content, media_type="audio/mpeg")


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ryanai_ui.html"), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------ admin CLI
def _cli():
    import getpass
    ok = (len(sys.argv) == 2 and sys.argv[1] == "users") or (len(sys.argv) >= 3 and sys.argv[1] in ("adduser", "passwd", "deluser"))
    if not ok:
        print("usage: python ryanai_app.py adduser <name> [--admin] | passwd <name> | deluser <name> | users")
        return
    init_db()
    con = db()
    cmd = sys.argv[1]
    if cmd == "users":
        for r in con.execute("SELECT name, is_admin FROM users ORDER BY name"):
            print(r["name"], "(admin)" if r["is_admin"] else "")
        return
    name = sys.argv[2].strip().lower()
    if cmd == "deluser":
        con.execute("DELETE FROM users WHERE name = ?", (name,))
        con.commit()
        print("deleted", name)
        return
    pw = getpass.getpass(f"Password for {name}: ")
    if len(pw) < 8:
        print("Use at least 8 characters.")
        return
    if pw != getpass.getpass("Again: "):
        print("The two passwords do not match.")
        return
    if cmd == "adduser":
        con.execute("INSERT INTO users (name, pw, is_admin, created) VALUES (?, ?, ?, ?)",
                    (name, hash_pw(pw), 1 if "--admin" in sys.argv else 0, time.time()))
    else:
        con.execute("UPDATE users SET pw = ? WHERE name = ?", (hash_pw(pw), name))
        con.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE name = ?)", (name,))
    con.commit()
    print("saved", name)


if __name__ == "__main__":
    _cli()
