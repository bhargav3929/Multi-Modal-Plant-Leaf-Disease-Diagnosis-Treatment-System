"""Multi-provider LLM access with automatic failover (Gemini primary, Groq fallback).

Keys come from the browser-saved settings table first, then from environment / .env.
All calls go through LangChain chat models so LangGraph nodes stay provider-agnostic.
"""
import json
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from . import config, db
import os

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when no provider is configured or every provider failed."""


# --- key management --------------------------------------------------------------
def get_key(provider: str) -> Tuple[str, str]:
    """Return (key, source) where source is 'ui', 'env' or ''."""
    ui = db.get_setting(f"{provider}_api_key")
    if ui:
        return ui, "ui"
    env = os.getenv(config.ENV_KEY_NAMES[provider], "").strip()
    return (env, "env") if env else ("", "")


def mask(key: str) -> str:
    return "" if not key else (key[:4] + "..." + key[-4:] if len(key) > 10 else "****")


def configured_providers() -> List[str]:
    return [p for p in config.PROVIDERS if get_key(p)[0]]


def _redact(text: str) -> str:
    """Never let an API key leak into an error message shown to the browser."""
    for p in config.PROVIDERS:
        key = get_key(p)[0]
        if key:
            text = text.replace(key, "***")
    return text[:240]


# --- model construction -------------------------------------------------------------
def _build(provider: str, model: str, key: str, temperature: float, timeout: float,
           max_tokens: Optional[int], no_thinking: bool):
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        extra: Dict[str, Any] = {}
        if max_tokens:
            extra["max_output_tokens"] = max_tokens
        if no_thinking:  # thinking tokens would eat the small output budget
            extra["thinking_budget"] = 0
        return ChatGoogleGenerativeAI(model=model, google_api_key=key, temperature=temperature,
                                      timeout=timeout, max_retries=0, **extra)
    from langchain_groq import ChatGroq
    extra = {"max_tokens": max_tokens} if max_tokens else {}
    return ChatGroq(model=model, api_key=key, temperature=temperature, timeout=timeout, max_retries=0, **extra)


def _candidates(vision: bool) -> List[Tuple[str, str, str]]:
    """Ordered (provider, model, key) attempts."""
    out: List[Tuple[str, str, str]] = []
    gem = get_key("gemini")[0]
    if gem:
        out.append(("gemini", config.GEMINI_MODEL, gem))
    groq = get_key("groq")[0]
    if groq:
        if vision:
            models = [config.GROQ_VISION_MODEL]
            if config.GROQ_VISION_MODEL_FALLBACK and config.GROQ_VISION_MODEL_FALLBACK != config.GROQ_VISION_MODEL:
                models.append(config.GROQ_VISION_MODEL_FALLBACK)
        else:
            models = [config.GROQ_TEXT_MODEL]
        out += [("groq", m, groq) for m in models]
    return out


def _content_to_text(content: Any) -> str:
    """Gemini may return a list of content parts; flatten to plain text."""
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


# --- provider health (circuit breaker) --------------------------------------------
_cooldown: Dict[str, float] = {}   # provider -> monotonic time until which it is skipped
_cooldown_lock = threading.Lock()
COOLDOWN_SECONDS = 120.0


def _trip(provider: str) -> None:
    with _cooldown_lock:
        _cooldown[provider] = time.monotonic() + COOLDOWN_SECONDS


def _healthy(provider: str) -> bool:
    with _cooldown_lock:
        return _cooldown.get(provider, 0.0) <= time.monotonic()


# --- public API -------------------------------------------------------------------
def _retry_after(msg: str) -> Optional[float]:
    """Seconds suggested by a 429 ('Please try again in 6.5s' / '850ms'), else None."""
    if "429" not in msg and "rate limit" not in msg.lower():
        return None
    m = re.search(r"try again in\s+(?:(\d+)m)?\s*([\d.]+)(ms|s)", msg)
    if not m:
        return None
    secs = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
    return secs + (int(m.group(1)) * 60 if m.group(1) else 0)


def _attempt(provider: str, model: str, key: str, system: str, user_text: str, schema: Type[T],
             images: Optional[List[str]], temperature: float, timeout: float, max_tokens: Optional[int]) -> Tuple[T, str]:
    """One provider/model attempt (raises on any failure)."""
    no_thinking = provider == "gemini" and bool(max_tokens)
    for attempt in range(2):
        try:
            llm = _build(provider, model, key, temperature, timeout, max_tokens, no_thinking)
            if images:
                # Vision models are picky about system prompts + images, so fold everything into one human turn.
                messages = [_image_message(f"{system}\n\n{user_text}", images)]
            else:
                messages = [SystemMessage(content=system), HumanMessage(content=user_text)]
            reply = llm.invoke(messages)
            return schema.model_validate(extract_json(_content_to_text(reply.content))), f"{provider}/{model}"
        except Exception as exc:
            if attempt == 0 and no_thinking and "think" in str(exc).lower():
                no_thinking = False  # this model rejects thinking_budget=0 - retry once without it
                continue
            wait_s = _retry_after(str(exc))
            if attempt == 0 and wait_s is not None and wait_s <= 20:
                time.sleep(wait_s + 0.3)   # free-tier per-minute limit: a short pause is cheaper than failing
                continue
            raise
    raise RuntimeError("unreachable")


def invoke_json(system: str, user_text: str, schema: Type[T], images: Optional[List[str]] = None,
                temperature: float = 0.2, timeout: Optional[float] = None,
                max_tokens: Optional[int] = None, hedge_after: float = 4.0) -> Tuple[T, str]:
    """Run a prompt (optionally with images); return (validated_model, 'provider/model').

    Providers are tried in priority order, but a slow primary does not block the fallback: if it has not
    answered after `hedge_after` seconds (or fails) the next candidate starts in parallel and the first
    valid answer wins. A provider that just failed is skipped for COOLDOWN_SECONDS.
    """
    cands = _candidates(vision=bool(images))
    if not cands:
        raise LLMError("No AI provider key is configured. Click 'API Keys' and paste a free Gemini or Groq key.")
    timeout = timeout or config.LLM_TIMEOUT_SECONDS
    live = [c for c in cands if _healthy(c[0])] or cands   # if everything is cooling down, try anyway
    skipped = [c for c in cands if c not in live]
    errors: List[str] = [f"{p}/{m}: skipped (recent failure)" for p, m, _ in skipped]

    pool = ThreadPoolExecutor(max_workers=len(live))
    pending: Dict[Any, Tuple[str, str]] = {}
    queue = list(live)

    def launch() -> None:
        provider, model, key = queue.pop(0)
        fut = pool.submit(_attempt, provider, model, key, system, user_text, schema, images, temperature, timeout, max_tokens)
        pending[fut] = (provider, model)

    try:
        launch()
        while pending:
            done, _ = wait(list(pending), timeout=hedge_after if queue else None, return_when=FIRST_COMPLETED)
            if not done:            # primary is slow -> hedge with the next candidate
                launch()
                continue
            for fut in done:
                provider, model = pending.pop(fut)
                try:
                    result, label = fut.result()
                    note = "" if not errors else " (fallback)"
                    return result, label + note
                except Exception as exc:
                    _trip(provider)
                    errors.append(f"{provider}/{model}: {_redact(str(exc))}")
                    if queue and not pending:
                        launch()
            if queue and not pending:
                launch()
        raise LLMError("All AI providers failed. " + " | ".join(errors))
    finally:
        pool.shutdown(wait=False)   # never block on a straggler
