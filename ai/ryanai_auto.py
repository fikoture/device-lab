"""
title: RyanAI Auto
author: Fikret (Ryan) Ture
description: Picks the best local model for each message, decides whether it should think, and streams its answer.
version: 0.2.0
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
from typing import Iterator, List, Union

import requests
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
        BIG_MODEL: str = Field(default="qwen3.5:9b", description="Long or hard work")
        CODE_MODEL: str = Field(default="qwen2.5-coder:7b", description="Code, errors, SQL, regex")
        VISION_MODEL: str = Field(default="gemma4:e2b", description="Messages with images")
        FALLBACK_MODEL: str = Field(default="llama3.2:3b", description="Used if the chosen model fails")
        LONG_CHARS: int = Field(default=1200, description="Messages longer than this go to the big model")
        SHOW_MODEL: bool = Field(default=True, description="Start each answer with the model name")
        THINK_ON_DEEP: bool = Field(default=True, description="Let the model think first on deep requests")

    def __init__(self):
        self.valves = self.Valves()
        self._caps = {}

    def _can_think(self, model: str) -> bool:
        if model not in self._caps:
            try:
                r = requests.post(f"{self.valves.OLLAMA_URL}/api/show", json={"model": model}, timeout=10)
                self._caps[model] = "thinking" in (r.json().get("capabilities") or [])
            except Exception:
                return False
        return self._caps[model]

    def pipes(self):
        return [{"id": "ryanai-auto", "name": "RyanAI Auto"}]

    def _stream(self, model: str, body: dict, think: bool) -> Iterator[str]:
        payload = {"model": model, "messages": to_ollama(body.get("messages", [])), "stream": True}
        if self._can_think(model):
            payload["think"] = think          # only sent to models that support it, else Ollama refuses
        options = {}
        if "temperature" in body:
            options["temperature"] = body["temperature"]
        if "top_p" in body:
            options["top_p"] = body["top_p"]
        if options:
            payload["options"] = options
        in_think = False
        with requests.post(f"{self.valves.OLLAMA_URL}/api/chat", json=payload, stream=True, timeout=(10, 900)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
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
        if in_think:
            yield "\n</think>\n\n"

    def pipe(self, body: dict, __user__: dict = None) -> Union[str, Iterator[str]]:
        model, reason = choose(body.get("messages", []), self.valves)
        think = reason == "deep" and self.valves.THINK_ON_DEEP

        def gen():
            if self.valves.SHOW_MODEL:
                yield f"*{model} ({reason})*\n\n"
            try:
                yield from self._stream(model, body, think)
            except Exception as e:
                fb = self.valves.FALLBACK_MODEL
                yield f"\n\n*{model} failed ({type(e).__name__}), answering with {fb}*\n\n"
                try:
                    yield from self._stream(fb, body, False)
                except Exception as e2:
                    yield f"\n\nNo model could answer: {e2}"

        return gen()
