"""Talk to a local Ollama server. This module never calls a paid API."""

from __future__ import annotations

import json
from urllib.error import URLError
from urllib.request import Request, urlopen

from .config import LLM_ALLOW_REMOTE, MODEL, OPENAI_API_KEY, OPENAI_BASE_URL

RECOVERY = (
    "Starta Ollama på den här datorn: installera från https://ollama.com, "
    "kör `ollama serve`, sedan `ollama pull qwen2.5:3b`. "
    "Workern använder http://127.0.0.1:11434/v1. Ingen API-nyckel behövs. "
    "På fri Colab-CPU räcker qwen2.5:3b; en större modell får ofta slut på minne."
)


def _local_url(url: str) -> bool:
    host = url.split("://", 1)[-1].split("/", 1)[0].split("@")[-1]
    host = host.split(":")[0].lower()
    return host in {"127.0.0.1", "localhost", "0.0.0.0"}


def health(timeout: float = 1.5) -> dict:
    """Check the configured model server. A failure does not try another provider."""
    remote = not _local_url(OPENAI_BASE_URL)
    if remote and not LLM_ALLOW_REMOTE:
        return {
            "ok": False,
            "backend": "unavailable",
            "model": MODEL,
            "baseUrl": OPENAI_BASE_URL,
            "error": "Basadressen är inte lokal Ollama, och LLM_ALLOW_REMOTE är inte satt.",
            "recovery": RECOVERY,
        }
    try:
        request = Request(OPENAI_BASE_URL.rstrip("/") + "/models", method="GET")
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        return {
            "ok": False,
            "backend": "unavailable",
            "model": MODEL,
            "baseUrl": OPENAI_BASE_URL,
            "error": f"Ollama svarade inte på {OPENAI_BASE_URL} ({error}).",
            "recovery": RECOVERY,
        }
    names = [str(item.get("id") or "") for item in payload.get("data") or [] if isinstance(item, dict)]
    present = not names or any(MODEL == name or name.startswith(MODEL + ":") or MODEL.startswith(name) for name in names)
    if not present:
        return {
            "ok": False,
            "backend": "unavailable",
            "model": MODEL,
            "baseUrl": OPENAI_BASE_URL,
            "error": f"Ollama är igång men modellen {MODEL} är inte nedladdad. Installerade: {', '.join(names[:6])}.",
            "recovery": f"Kör `ollama pull {MODEL}`. " + RECOVERY,
        }
    return {
        "ok": True,
        "backend": "ollama",
        "model": MODEL,
        "baseUrl": OPENAI_BASE_URL,
        "error": None,
        "recovery": None,
        "models": names,
    }


def complete(messages: list[dict], timeout: float = 90) -> str:
    """One chat completion. Raises if the local server does not answer."""
    body = json.dumps({
        "model": MODEL,
        "temperature": 0,
        "messages": messages,
        "max_tokens": 700,
    }).encode("utf-8")
    request = Request(
        OPENAI_BASE_URL.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return str(payload["choices"][0]["message"].get("content") or "")
    finally:
        # Free the weights before FFmpeg. A free CPU cannot hold the model and a render.
        if _local_url(OPENAI_BASE_URL):
            _unload()


def _unload() -> None:
    host = OPENAI_BASE_URL.split("/v1", 1)[0]
    body = json.dumps({"model": MODEL, "keep_alive": 0, "prompt": ""}).encode("utf-8")
    request = Request(host + "/api/generate", data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=8) as response:
            response.read()
    except (URLError, TimeoutError, OSError):
        return
