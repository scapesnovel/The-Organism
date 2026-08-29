"""Multi-provider model router for The Organism.

The organism is NOT tied to Gemini. Gemini is only the birth brain — the
free tier that carries it through the baby stage. As it grows it discovers
new models, asks the founder to add their API keys to the repository
secrets, registers them in ``api_keys/providers.json`` and this router
automatically starts using them.

Key design points (from the founder's specification):

* "It should not crash or stop. Even hibernating is better than crashing."
  → every provider failure falls through to the next one in priority
  order; only when ALL providers fail does the router return "" (yield
  until the next wake), never raise.
* "It should remember all the apis it has collected since I will be saving
  them on secret variable." → the registry file stores provider metadata
  (name, endpoint, env-var NAME — never the key itself). Keys live only in
  GitHub Secrets. The workflow passes the whole secrets store as the
  ``ALL_SECRETS`` JSON env var, so a key the founder adds becomes usable on
  the very next wake without any workflow edit.
* "It requests/picks the best api key either free or paid... but it should
  be the best it got." → providers carry a ``priority`` (lower = better);
  the organism edits the registry (an editable file) as its knowledge of
  model strengths and weaknesses grows.

Supported provider kinds:

* ``gemini``  — Google Generative Language API (native REST).
* ``openai``  — any OpenAI-compatible chat endpoint (OpenAI, Groq,
  Mistral, DeepSeek, Together, OpenRouter, Fireworks, ...). This one kind
  covers most of the free-tier ecosystem the organism will discover.
* ``anthropic`` — Anthropic Messages API.

No secret value is ever logged; only provider names and env-var names.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import requests

from core import config

LOGGER = logging.getLogger("organism.model_router")

# Registry of known providers. Committed, contains NO secrets — only the
# NAMES of the environment variables that hold the keys.
PROVIDERS_FILE: Path = config.API_KEYS_DIR / "providers.json"

# The workflow exports the entire GitHub Secrets store here (JSON object)
# so newly added keys are visible without editing the workflow.
ENV_ALL_SECRETS = "ALL_SECRETS"

REQUEST_TIMEOUT = 90

# The birth brain. Always present so the organism can never end up with an
# empty registry, even if providers.json is deleted or corrupted.
_GEMINI_PROVIDER: Dict = {
    "name": "gemini",
    "kind": "gemini",
    "env_key": config.ENV_GEMINI_API_KEY,
    "model": "",  # resolved from GEMINI_MODEL / gemini_api default at call time
    "priority": 10,
    "enabled": True,
    "notes": "Birth brain. Free tier. Managed by integrations/gemini_api.py.",
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def load_providers() -> List[Dict]:
    """Return the provider list, sorted by priority (lower first).

    The Gemini birth brain is always included exactly once.
    """
    providers: List[Dict] = []
    try:
        if PROVIDERS_FILE.exists():
            data = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("providers", [])
            for entry in data:
                if isinstance(entry, dict) and entry.get("name") and entry.get("env_key"):
                    providers.append(entry)
    except Exception as exc:
        LOGGER.warning("providers.json unreadable (%s); using built-in registry.", exc)
        providers = []

    if not any(p.get("name") == "gemini" for p in providers):
        providers.append(dict(_GEMINI_PROVIDER))

    providers.sort(key=lambda p: p.get("priority", 100))
    return providers


def save_providers(providers: List[Dict]) -> None:
    """Persist the registry (the organism may edit this as it learns)."""
    PROVIDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROVIDERS_FILE.write_text(
        json.dumps({"providers": providers}, indent=2) + "\n", encoding="utf-8"
    )


def register_provider(
    name: str,
    kind: str,
    env_key: str,
    model: str,
    base_url: str = "",
    priority: int = 50,
    notes: str = "",
) -> Dict:
    """Add or update a provider entry (never stores a key value)."""
    providers = load_providers()
    entry = {
        "name": name.strip(),
        "kind": kind.strip().lower(),
        "env_key": env_key.strip(),
        "model": model.strip(),
        "base_url": base_url.strip(),
        "priority": int(priority),
        "enabled": True,
        "notes": notes.strip(),
    }
    providers = [p for p in providers if p.get("name") != entry["name"]]
    providers.append(entry)
    providers.sort(key=lambda p: p.get("priority", 100))
    save_providers(providers)
    LOGGER.info("Provider '%s' registered (env var: %s).", entry["name"], entry["env_key"])
    return entry


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------
def _all_secrets() -> Dict[str, str]:
    """Parse the ALL_SECRETS JSON blob exported by the workflow, if any."""
    raw = os.environ.get(ENV_ALL_SECRETS, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if v}
    except Exception:
        LOGGER.warning("ALL_SECRETS is set but not valid JSON; ignoring it.")
    return {}


def resolve_key(env_key: str) -> Optional[str]:
    """Look a key up: direct env var first, then the ALL_SECRETS store."""
    value = os.environ.get(env_key, "").strip()
    if value:
        return value
    value = _all_secrets().get(env_key, "").strip()
    return value or None


# Maximum numbered key variants scanned per provider (BASE, BASE_2 .. BASE_N).
MAX_KEY_VARIANTS = 10


def resolve_keys(env_key: str) -> List[str]:
    """Resolve EVERY key variant for a provider: BASE, BASE_2, BASE_3, ...

    The founder may add extra keys for the SAME provider (e.g. a second
    free-tier Gemini key under ``GEMINI_API_KEY_2``) so the organism has
    variety when one key's quota runs out. Variants follow the naming rule
    ``<ENV_KEY>_<n>`` for n = 2..MAX_KEY_VARIANTS. Scanning stops at the
    first missing number so the founder controls the pool size simply by
    which secrets exist. Returned in order; duplicates removed.
    """
    keys: List[str] = []
    base = resolve_key(env_key)
    if base:
        keys.append(base)
    for n in range(2, MAX_KEY_VARIANTS + 1):
        variant = resolve_key(f"{env_key}_{n}")
        if not variant:
            break  # numbering is contiguous by convention; stop at first gap
        if variant not in keys:
            keys.append(variant)
    return keys


def available_providers() -> List[Dict]:
    """Providers that are enabled AND have a resolvable key right now."""
    return [
        p
        for p in load_providers()
        if p.get("enabled", True) and resolve_key(p.get("env_key", ""))
    ]


# ---------------------------------------------------------------------------
# Completion backends
# ---------------------------------------------------------------------------
def _complete_gemini(provider: Dict, prompt: str, max_output_tokens: int) -> str:
    """Gemini backend with key rotation: try every configured key variant.

    Quota exhaustion on GEMINI_API_KEY falls through to GEMINI_API_KEY_2,
    _3, ... before the router moves on to a different provider entirely.
    """
    from integrations import gemini_api

    keys = resolve_keys(provider.get("env_key", config.ENV_GEMINI_API_KEY))
    if not keys:
        raise RuntimeError("No Gemini key resolvable.")
    last_exc: Optional[Exception] = None
    for index, key in enumerate(keys, 1):
        try:
            result = gemini_api.complete(
                prompt, max_output_tokens=max_output_tokens, api_key=key
            )
            if result:
                if index > 1:
                    LOGGER.info("Answered via Gemini key variant #%s.", index)
                return result
        except Exception as exc:  # quota, auth, transport — try the next key
            last_exc = exc
            LOGGER.warning(
                "Gemini key variant #%s failed (%s); trying next variant.",
                index,
                type(exc).__name__,
            )
    if last_exc is not None:
        raise last_exc
    return ""


def _complete_openai(provider: Dict, prompt: str, max_output_tokens: int) -> str:
    last_exc: Optional[Exception] = None
    for index, key in enumerate(resolve_keys(provider["env_key"]), 1):
        try:
            base_url = (provider.get("base_url") or "https://api.openai.com/v1").rstrip("/")
            model = provider.get("model") or "gpt-4o-mini"
            response = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_output_tokens,
                    "temperature": 0.7,
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            if choices:
                text = (choices[0].get("message", {}).get("content") or "").strip()
                if text:
                    if index > 1:
                        LOGGER.info("Answered via '%s' key variant #%s.", provider.get("name"), index)
                    return text
        except Exception as exc:
            last_exc = exc
            LOGGER.warning(
                "Provider '%s' key variant #%s failed (%s); trying next variant.",
                provider.get("name"), index, type(exc).__name__,
            )
    if last_exc is not None:
        raise last_exc
    return ""


def _complete_anthropic(provider: Dict, prompt: str, max_output_tokens: int) -> str:
    last_exc: Optional[Exception] = None
    for index, key in enumerate(resolve_keys(provider["env_key"]), 1):
        try:
            base_url = (provider.get("base_url") or "https://api.anthropic.com").rstrip("/")
            model = provider.get("model") or "claude-3-5-haiku-latest"
            response = requests.post(
                f"{base_url}/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model,
                    "max_tokens": max_output_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            blocks = data.get("content") or []
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            if text:
                if index > 1:
                    LOGGER.info("Answered via '%s' key variant #%s.", provider.get("name"), index)
                return text
        except Exception as exc:
            last_exc = exc
            LOGGER.warning(
                "Provider '%s' key variant #%s failed (%s); trying next variant.",
                provider.get("name"), index, type(exc).__name__,
            )
    if last_exc is not None:
        raise last_exc
    return ""


_BACKENDS = {
    "gemini": _complete_gemini,
    "openai": _complete_openai,
    "anthropic": _complete_anthropic,
}


# ---------------------------------------------------------------------------
# The router itself
# ---------------------------------------------------------------------------
def complete(prompt: str, max_output_tokens: int = 1500) -> str:
    """Run a completion through the best available provider.

    Tries providers in priority order and falls through on any failure —
    quota exhaustion on one provider must never stop the organism while
    another key is available. Returns "" only when every provider failed
    (callers treat that as "no answer this cycle" and retry next wake).
    """
    candidates = available_providers()
    if not candidates:
        LOGGER.warning(
            "No model provider has a usable key. The organism is brainless "
            "this cycle; it should ask the founder for a key."
        )
        return ""

    for provider in candidates:
        kind = (provider.get("kind") or "").lower()
        backend = _BACKENDS.get(kind)
        if backend is None:
            LOGGER.warning("Provider '%s' has unknown kind '%s'; skipping.", provider.get("name"), kind)
            continue
        try:
            result = backend(provider, prompt, max_output_tokens)
            if result:
                if provider.get("name") != "gemini":
                    LOGGER.info("Answered via fallback provider '%s'.", provider.get("name"))
                return result
            LOGGER.warning("Provider '%s' returned an empty answer; trying next.", provider.get("name"))
        except Exception as exc:
            # Log the class and provider, never the key or full URL.
            LOGGER.warning(
                "Provider '%s' failed (%s: %s); trying next.",
                provider.get("name"),
                type(exc).__name__,
                str(exc)[:200],
            )

    LOGGER.warning("All %s provider(s) failed; yielding until next wake.", len(candidates))
    return ""


def brain_status() -> Dict:
    """Summary used by health checks and daily reports (no secret values)."""
    providers = load_providers()
    usable = [p["name"] for p in providers if p.get("enabled", True) and resolve_key(p.get("env_key", ""))]
    waiting = [
        {"name": p["name"], "env_key": p["env_key"]}
        for p in providers
        if p.get("enabled", True) and not resolve_key(p.get("env_key", ""))
    ]
    # Key-variant depth per usable provider (COUNTS only, never values):
    # lets health checks and daily reports show e.g. gemini: 3 keys.
    key_counts = {
        p["name"]: len(resolve_keys(p.get("env_key", "")))
        for p in providers
        if p.get("enabled", True) and resolve_key(p.get("env_key", ""))
    }
    return {
        "usable": usable,
        "waiting_for_key": waiting,
        "total_registered": len(providers),
        "key_counts": key_counts,
    }
