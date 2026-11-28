"""LLM access via Groq only (vision model + text model, each with a fallback model).

Key order: key saved in the app (download version only), then GROQ_API_KEY from env / .env.
Every call has a timeout, retries with backoff on busy/transient errors, falls back to the
second model, and turns failures into plain-English LLMError(code=...) messages.

Groq's free tier limits input tokens per minute, and a photo costs a fixed ~1,300 tokens whatever its size.
So when Groq says "try again in Xs" we wait (telling the UI through WAIT_HOOK) instead of failing, and
invoke_json_cached() reuses the saved answer when the exact same photo and question come again.
"""
import contextvars
import hashlib
import json
import logging
import os
import random
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from . import config, db

log = logging.getLogger("agri")
T = TypeVar("T", bound=BaseModel)

MESSAGES = {
    "AI_NOT_CONFIGURED": "No Groq key is set yet. Open API Keys and paste a free key from console.groq.com/keys.",
    "AI_KEY_INVALID": "Your Groq key was rejected. Open API Keys and paste a fresh key from console.groq.com/keys.",
    "AI_BUSY": "The AI service is busy right now. Wait a minute and try again.",
    "AI_FAILED": "The AI couldn't process this input.",
}
# Groq's free tier also caps tokens per day (about 65 photo scans). Waiting a minute won't help then, so say so.
DAILY_LIMIT_MESSAGE = ("Today's free AI limit for this Groq key is used up. Samples and saved results still work. "
                       "Try again later (Groq resets it within 24 hours), or paste another key under API Keys.")


RATE_LIMIT_WAIT_BUDGET_S = 60     # total time we are willing to wait for Groq's per-minute limit to reset

# Set by the streaming route: called with the number of seconds we are about to wait, so the UI can count down.
WAIT_HOOK: contextvars.ContextVar[Optional[Callable[[float], None]]] = contextvars.ContextVar("WAIT_HOOK", default=None)


class LLMError(RuntimeError):
    """Plain-English AI failure carrying a contract error code."""

    def __init__(self, code: str, message: Optional[str] = None):
        super().__init__(message or MESSAGES[code])
        self.code = code


# --- key management --------------------------------------------------------------
def usable_key(key: Optional[str]) -> bool:
    key = (key or "").strip()
    return bool(key) and key != config.KEY_PLACEHOLDER and " " not in key and key.startswith("gsk_")


def get_key(provider: str = "groq") -> Tuple[str, str]:
    """Return (key, source): source is 'ui', 'env' or ''. Saved keys are ignored in demo mode."""
    if not config.DEMO_MODE:
        ui = (db.get_setting(f"{provider}_api_key") or "").strip()
        if usable_key(ui):
            return ui, "ui"
    env = os.getenv(config.ENV_KEY_NAMES[provider], "").strip()
    return (env, "env") if usable_key(env) else ("", "")


def mask(key: str) -> str:
    return "" if not key else (f"gsk_…{key[-4:]}" if len(key) > 8 else "****")


def configured_providers() -> List[str]:
    return [p for p in config.PROVIDERS if get_key(p)[0]]


def _redact(text: str) -> str:
    """Never let an API key leak into logs or messages."""
    key = get_key("groq")[0]
    if key:
        text = text.replace(key, "***")
    return re.sub(r"gsk_[A-Za-z0-9]{8,}", "gsk_***", text)[:240]


def _models(vision: bool) -> List[str]:
    primary, fallback = ((config.GROQ_VISION_MODEL, config.GROQ_VISION_FALLBACK_MODEL) if vision
                         else (config.GROQ_MODEL, config.GROQ_FALLBACK_MODEL))
    return [primary] + ([fallback] if fallback and fallback != primary else [])


# --- model construction -------------------------------------------------------------
def _build(model: str, key: str, temperature: float, timeout: float, max_tokens: Optional[int]):
    from langchain_groq import ChatGroq
    extra = {"max_tokens": max_tokens} if max_tokens else {}
    return ChatGroq(model=model, api_key=key, temperature=temperature, timeout=timeout, max_retries=0, **extra)


def _content_to_text(content: Any) -> str:
    """Models may return a list of content parts; flatten to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and p.get("type", "text") == "text":
                parts.append(str(p.get("text", "")))
        return "".join(parts)
    return str(content)


def extract_json(text: str) -> Dict[str, Any]:
    """Pull the first JSON object out of a model reply (tolerates ``` fences / chatter)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # Qwen-style reasoning preamble
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("model reply contained no JSON object")
    return json.loads(text[start:end + 1])


def _image_message(text: str, images: List[str]) -> HumanMessage:
    content: List[Dict[str, Any]] = [{"type": "text", "text": text}]
    for b64 in images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return HumanMessage(content=content)


# --- retry / classification ---------------------------------------------------------
def _status_of(exc: Exception) -> Optional[int]:
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    m = re.search(r"\b(401|413|429|400|500|502|503|504)\b", str(exc))
    return int(m.group(1)) if m else None


def _retry_after(msg: str) -> Optional[float]:
    """Seconds suggested by a 429 ('Please try again in 6.5s' / '850ms'), else None."""
    m = re.search(r"try again in\s+(?:(\d+)m)?\s*([\d.]+)(ms|s)", msg)
    if not m:
        return None
    secs = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
    return secs + (int(m.group(1)) * 60 if m.group(1) else 0)


def _attempt(model: str, key: str, system: str, user_text: str, schema: Type[T],
             images: Optional[List[str]], temperature: float, timeout: float, max_tokens: Optional[int]) -> T:
    llm = _build(model, key, temperature, timeout, max_tokens)
    if images:
        # Vision models are picky about system prompts + images, so fold everything into one human turn.
        messages = [_image_message(f"{system}\n\n{user_text}", images)]
    else:
        messages = [SystemMessage(content=system), HumanMessage(content=user_text)]
    reply = llm.invoke(messages)
    return schema.model_validate(extract_json(_content_to_text(reply.content)))


def _wait(seconds: float) -> None:
    hook = WAIT_HOOK.get()
    if hook and seconds >= 1:
        try:
            hook(seconds)
        except Exception:  # noqa: BLE001 - a UI notice must never break the AI call
            pass
    time.sleep(seconds)


def invoke_json(system: str, user_text: str, schema: Type[T], images: Optional[List[str]] = None,
                temperature: float = 0.2, timeout: Optional[float] = None,
                max_tokens: Optional[int] = None) -> Tuple[T, str]:
    """Run a prompt (optionally with images); return (validated_model, 'groq/<model>').

    Busy errors (429/5xx/timeouts) are retried with backoff (1 s, 2 s, 4 s + jitter). A 429 that says
    'try again in Xs' waits X seconds instead, for up to RATE_LIMIT_WAIT_BUDGET_S in total, because the
    per-minute limit always resets. Then the fallback model. 401 stops at once. Invalid JSON is retried once.
    """
    key = get_key("groq")[0]
    if not key:
        raise LLMError("AI_NOT_CONFIGURED")
    timeout = timeout or config.LLM_TIMEOUT_SECONDS
    busy = False
    waited = 0.0
    for model in _models(vision=bool(images)):
        bad_output = 0
        attempt = 0
        while True:
            try:
                return _attempt(model, key, system, user_text, schema, images, temperature, timeout, max_tokens), f"groq/{model}"
            except Exception as exc:  # noqa: BLE001 - classified below
                status = _status_of(exc)
                name = type(exc).__name__.lower()
                log.warning("groq %s attempt %d failed: %s %s", model, attempt + 1, type(exc).__name__, _redact(str(exc)))
                attempt += 1
                if status == 401:
                    raise LLMError("AI_KEY_INVALID")
                if isinstance(exc, (ValueError, json.JSONDecodeError)) or "validation" in name:
                    bad_output += 1
                    if bad_output >= 2:
                        break          # model keeps returning unusable output -> try the fallback model
                    continue
                if status in (400, 413):
                    break              # retrying the same request won't help; try the fallback model
                transient = status in (429, 500, 502, 503, 504) or status is None  # None: timeout / connection
                if not transient:
                    break
                busy = True
                if status == 429 and re.search(r"per day|\(TPD\)|\(RPD\)", str(exc)):
                    raise LLMError("AI_BUSY", DAILY_LIMIT_MESSAGE)
                wait_s = _retry_after(str(exc)) if status == 429 else None
                if wait_s is not None and waited + wait_s <= RATE_LIMIT_WAIT_BUDGET_S:
                    wait_s += 0.5      # per-minute limit: it will reset, so wait for it
                elif attempt < 3:
                    wait_s = (2 ** (attempt - 1)) + random.random() * 0.5
                else:
                    break
                waited += wait_s
                _wait(wait_s)
    raise LLMError("AI_BUSY" if busy else "AI_FAILED")


def _cache_key(system: str, user_text: str, schema: Type[BaseModel], images: Optional[List[str]]) -> str:
    h = hashlib.sha256()
    for part in (schema.__name__, system, user_text, *(images or [])):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def invoke_json_cached(system: str, user_text: str, schema: Type[T], images: Optional[List[str]] = None,
                       fresh: bool = False, **kwargs: Any) -> Tuple[T, str, bool]:
    """Like invoke_json, but the same photo + same question reuses the saved answer (no AI call, no tokens).

    Returns (validated_model, provider, reused). fresh=True always asks the AI and refreshes the saved answer.
    """
    key = _cache_key(system, user_text, schema, images)
    if not fresh:
        saved = db.get_ai_answer(key)
        if saved:
            try:
                return schema.model_validate(saved["data"]), saved["provider"], True
            except Exception:  # noqa: BLE001 - an old/invalid saved answer is simply ignored
                pass
    out, provider = invoke_json(system, user_text, schema, images=images, **kwargs)
    db.save_ai_answer(key, provider, out.model_dump())
    return out, provider, False
