"""Shared text handling for RyanAI Voice engines (XTTS and Chatterbox).

- normalize(): written Turkish -> spoken form with trnorm (numbers, dates, money, abbreviations), if installed.
- pieces(): splits text into sentences and pauses. TTS models often garble the end of a long input at the
  full stop, so every sentence is spoken on its own without its final full stop.
"""
import re

TURKISH_LETTERS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")

try:                                   # Apache-2.0, https://github.com/ozguradmin/ahenk-turkish-tts
    from trnorm import normalize as _tr_normalize
except Exception:                      # optional: without it the text goes in as written
    _tr_normalize = None


def has_trnorm() -> bool:
    return _tr_normalize is not None


def normalize(text: str, language: str) -> str:
    if language == "tr" and _tr_normalize is not None:
        try:
            return _tr_normalize(text)
        except Exception:
            return text
    return text


SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
MAX_PIECE = 200            # XTTS gets unreliable on very long pieces
PAUSE_SENTENCE_S = 0.30
PAUSE_PARAGRAPH_S = 0.70


def pieces(text: str, keep_stops: bool = True):
    """Text -> list of (piece, pause_after_seconds). With keep_stops=False the final full stop is dropped."""
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
                part = part.strip()
                if keep_stops:
                    part = part.rstrip(",;:").strip()
                    if part and part[-1] not in ".!?…":
                        part += "."          # every piece ends like a sentence, so the model knows it is done
                else:
                    part = part.rstrip(".").rstrip(",;:").strip()
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
