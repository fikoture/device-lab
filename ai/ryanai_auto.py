"""
title: RyanAI Auto
author: Fikret (Ryan) Ture
description: Picks the best local model for each message, decides whether it should think, and streams its answer.
version: 0.4.0
"""
# Open WebUI "Pipe" function. Install: Admin Panel > Functions > + > paste this file > Save > enable.
# It shows up as a model named "RyanAI Auto". Change which model does which job under the function's Valves.
#
# How it chooses (first match wins):
#   1. the message has an image          -> vision model
#   2. it looks like code or an error    -> code model
#   3. it is long or asks for deep work  -> big model
#   4. everything else                   -> fast model
# Thinking: only "deep" requests think (and only on models that can). Quick, code and image answers come
# straight away. The thinking shows above the answer in a fold-out block, like Claude.
# The first line of every answer says which model answered, so the choice can be checked (and tested).

import json
import re
from typing import AsyncIterator, List

import httpx
from pydantic import BaseModel, Field

CODE_HINTS = re.compile(
    r"```|\bdef |\bclass |\bimport |\bfunction\b|\bconst |=>|traceback|exception|stack trace|syntaxerror|"
    r"\bpython\b|\bjavascript\b|\btypescript\b|\bjava\b|\bsql\b|\bregex\b|\bbash\b|\bplaywright\b|\bpytest\b|"
    r"\bkod\b|\bkodu\b|\bfonksiyon|\bhata mesaj",
    re.IGNORECASE,
)
DEEP_HINTS = re.compile(
    r"\banaly[sz]e\b|\bin detail\b|\bstep by step\b|\bcompare\b|\bplan\b|\bstrategy\b|\bwhy\b|"
    r"\banaliz|\bdetayl|\badim adim|\badım adım|\bkarsilastir|\bkarşılaştır|\bneden\b|\bstrateji",
    re.IGNORECASE,
)


def last_user_message(messages: List[dict]) -> dict:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m
    return {}


def text_and_images(message: dict):
    content = message.get("content", "")
    if isinstance(content, str):
        return content, False
    text, has_image = [], False
    for part in content or []:
        if part.get("type") == "text":
            text.append(part.get("text", ""))
        elif part.get("type") == "image_url":
            has_image = True
    return "\n".join(text), has_image


def to_ollama(messages: List[dict]) -> List[dict]:
    """OpenAI style messages (Open WebUI) -> Ollama /api/chat messages (text + base64 images)."""
    out = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            out.append({"role": m.get("role", "user"), "content": content})
            continue
        texts, images = [], []
        for part in content or []:
            if part.get("type") == "text":
                texts.append(part.get("text", ""))
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                if url.startswith("data:") and "," in url:
                    images.append(url.split(",", 1)[1])
        msg = {"role": m.get("role", "user"), "content": "\n".join(texts)}
        if images:
            msg["images"] = images
        out.append(msg)
    return out


def choose(messages: List[dict], v) -> (str, str):
    msg = last_user_message(messages)
    text, has_image = text_and_images(msg)
    if has_image or msg.get("images"):
        return v.VISION_MODEL, "image"
    if CODE_HINTS.search(text):
        return v.CODE_MODEL, "code"
    if len(text) > v.LONG_CHARS or DEEP_HINTS.search(text):
        return v.BIG_MODEL, "deep"
    return v.FAST_MODEL, "quick"


class Pipe:
    class Valves(BaseModel):
        OLLAMA_URL: str = Field(default="http://127.0.0.1:11434", description="Ollama address")
        FAST_MODEL: str = Field(default="qwen3.5:4b", description="Short everyday questions")
        BIG_MODEL: str = Field(default="qwen3.5:4b", description="Long or hard work (thinks first). qwen3.5:9b is smarter but does not fit a 6 GB GPU and is too slow")
        CODE_MODEL: str = Field(default="qwen2.5-coder:7b", description="Code, errors, SQL, regex")
        VISION_MODEL: str = Field(default="gemma4:e2b", description="Messages with images")
        FALLBACK_MODEL: str = Field(default="llama3.2:3b", description="Used if the chosen model fails")
        LONG_CHARS: int = Field(default=1200, description="Messages longer than this go to the big model")
        SHOW_MODEL: bool = Field(default=True, description="Start each answer with the model name")
        THINK_ON_DEEP: bool = Field(default=True, description="Let the model think first on deep requests")
        READ_TIMEOUT_S: int = Field(default=180, description="Give up if the model sends nothing for this many seconds")
        MAX_SYSTEM_CHARS: int = Field(default=1500, description="Drop system messages longer than this (Open WebUI tool and task instructions slow small models and hurt their Turkish)")
        KEEP_TURNS: int = Field(default=10, description="Send only the last N user/assistant messages")

    def __init__(self):
        self.valves = self.Valves()
        self._caps = {}

    async def _can_think(self, client: httpx.AsyncClient, model: str) -> bool:
        if model not in self._caps:
            try:
                r = await client.post(f"{self.valves.OLLAMA_URL}/api/show", json={"model": model}, timeout=10)
                self._caps[model] = "thinking" in (r.json().get("capabilities") or [])
            except Exception:
                return False
        return self._caps[model]

    def pipes(self):
        return [{"id": "ryanai-auto", "name": "RyanAI Auto"}]

    async def _stream(self, model: str, body: dict, think: bool) -> AsyncIterator[str]:
        """Async on purpose: a slow model must never block the Open WebUI server for other users."""
        payload = {"model": model, "messages": to_ollama(body.get("messages", [])), "stream": True}
        client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=float(self.valves.READ_TIMEOUT_S), write=30, pool=10))
        if await self._can_think(client, model):
            payload["think"] = think          # only sent to models that support it, else Ollama refuses
        options = {}
        if "temperature" in body:
            options["temperature"] = body["temperature"]
        if "top_p" in body:
            options["top_p"] = body["top_p"]
        if options:
            payload["options"] = options
        in_think = False
        try:
            async with client.stream("POST", f"{self.valves.OLLAMA_URL}/api/chat", json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise RuntimeError(chunk["error"])
                    msg = chunk.get("message") or {}
                    thought, text = msg.get("thinking"), msg.get("content")
                    if thought:
                        if not in_think:
                            yield "<think>\n"
                            in_think = True
                        yield thought
                    if text:
                        if in_think:
                            yield "\n</think>\n\n"
                            in_think = False
                        yield text
                    if chunk.get("done"):
                        break
        finally:
            await client.aclose()
        if in_think:
            yield "\n</think>\n\n"

    def _trim(self, messages: List[dict]):
        """Keeps short system messages and the last KEEP_TURNS turns. Returns (messages, chars_in, dropped_chars)."""
        system, turns, dropped = [], [], 0
        for m in messages:
            if m.get("role") == "system":
                text, _ = text_and_images(m)
                if len(text) <= self.valves.MAX_SYSTEM_CHARS:
                    system.append(m)
                else:
                    dropped += len(text)
            else:
                turns.append(m)
        if self.valves.KEEP_TURNS > 0:
            turns = turns[-self.valves.KEEP_TURNS:]
        kept = system + turns
        chars = sum(len(text_and_images(m)[0]) for m in kept)
        return kept, chars, dropped

    async def pipe(self, body: dict, __user__: dict = None) -> AsyncIterator[str]:
        messages, chars_in, dropped = self._trim(body.get("messages", []))
        body = {**body, "messages": messages}
        model, reason = choose(messages, self.valves)
        think = reason == "deep" and self.valves.THINK_ON_DEEP

        if self.valves.SHOW_MODEL:
            note = f", {chars_in} chars in" + (f", {dropped} chars of system text dropped" if dropped else "")
            yield f"*{model} ({reason}{note})*\n\n"
        try:
            async for piece in self._stream(model, body, think):
                yield piece
        except Exception as e:
            fb = self.valves.FALLBACK_MODEL
            yield f"\n\n*{model} failed ({type(e).__name__}), answering with {fb}*\n\n"
            try:
                async for piece in self._stream(fb, body, False):
                    yield piece
            except Exception as e2:
                yield f"\n\nNo model could answer: {e2}"
