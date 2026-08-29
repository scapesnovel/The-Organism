"""Gemini API client for The Organism.

Uses the free tier's REST endpoint with exponential backoff, retry and
quota-aware sleep. The API key is read from the environment at call time
and never logged.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

from core import config

LOGGER = logging.getLogger("organism.gemini")

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
LIST_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"  # preferred default; override via GEMINI_MODEL

# Model names ROT: Google retires them (gemini-1.5-flash died as a 404 in
# the founder's own test). We therefore never trust a single hardcoded
# name: on a 404 the client asks the live ListModels endpoint what models
# THIS key can use for generateContent right now, and tries them all in
# preference order. Flash-class models first (cheapest / most generous
# free-tier quota), then pro-class, then anything else that works.
_MODEL_PREFERENCE = ("flash", "pro")

# Discovered model list cache (per process) so one wake cycle never calls
# ListModels more than once per key.
_model_cache: dict = {}

MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 4
# Hard wall-clock budget for ONE completion call (request time + backoff
# sleeps). During a Google outage (503s / read timeouts) unbounded retries
# burned ~5 minutes per call inside CI; with many calls per wake that
# exhausts the 15-minute job timeout and the Actions minute budget. When
# the budget is spent we yield: the router rotates to the next key/provider
# or the organism hibernates until the next wake.
CALL_BUDGET_SECONDS = 150
# Bounded quota wait. The previous value (4 HOURS) exceeded the workflow's
# own timeout and would have burned the entire GitHub Actions free-tier
# minute budget sleeping. Sleeping is never free inside CI — give quota a
# short chance to recover, then yield until the next scheduled wake.
QUOTA_POLL_SECONDS = 30
QUOTA_MAX_WAIT_SECONDS = 120


class GeminiQuotaExhausted(RuntimeError):
    """Raised when the free tier quota is exhausted for this window."""


def _api_key() -> str:
    key = os.environ.get(config.ENV_GEMINI_API_KEY, "").strip()
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY secret is not set. Add it in the repository "
            "settings -> Secrets and variables -> Actions."
        )
    return key


def _model_name() -> str:
    return os.environ.get(config.ENV_GEMINI_MODEL, DEFAULT_MODEL).strip() or DEFAULT_MODEL


def list_available_models(api_key: str = "") -> list:
    """Ask Google which models this key can use for generateContent NOW.

    Returns bare model names (e.g. ['gemini-2.5-flash', ...]) sorted by
    preference: flash-class first, then pro, then the rest. Cached per key
    for the lifetime of the process. Returns [] on any failure — callers
    fall back to the configured default.
    """
    key = api_key.strip() or _api_key()
    cache_token = key[-6:]  # cache key without holding the full secret
    if cache_token in _model_cache:
        return _model_cache[cache_token]
    names: list = []
    try:
        response = requests.get(
            LIST_MODELS_URL,
            headers={"x-goog-api-key": key},
            params={"pageSize": 100},
            timeout=30,
        )
        response.raise_for_status()
        for entry in response.json().get("models", []):
            methods = entry.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            name = (entry.get("name") or "").split("/")[-1]
            # Skip specialised variants that are wrong for a text brain.
            lowered = name.lower()
            if any(bad in lowered for bad in ("embedding", "aqa", "image", "tts", "audio", "vision", "veo", "imagen")):
                continue
            if name:
                names.append(name)
    except Exception as exc:
        LOGGER.warning("Could not list Gemini models (%s); using default only.", type(exc).__name__)
        return []

    def _rank(name: str) -> tuple:
        lowered = name.lower()
        for idx, cls in enumerate(_MODEL_PREFERENCE):
            if cls in lowered:
                # Prefer higher version numbers within a class (newer first).
                return (idx, -_version_of(lowered))
        return (len(_MODEL_PREFERENCE), -_version_of(lowered))

    names.sort(key=_rank)
    _model_cache[cache_token] = names
    if names:
        LOGGER.info("Gemini models available to this key: %s", ", ".join(names[:8]))
    return names


def _version_of(name: str) -> float:
    """Extract a sortable version number from a model name (0.0 if none)."""
    import re

    match = re.search(r"(\d+(?:\.\d+)?)", name)
    try:
        return float(match.group(1)) if match else 0.0
    except ValueError:
        return 0.0


def complete(prompt: str, max_output_tokens: int = 1500, api_key: str = "") -> str:
    """Run a completion, falling through EVERY currently available model.

    ``api_key`` lets the model router rotate through key variants
    (GEMINI_API_KEY, GEMINI_API_KEY_2, ...); when empty, the primary
    environment key is used as before.

    Model fallback: the configured model (GEMINI_MODEL or the default) is
    tried first. If Google answers 404 (model retired — names rot over the
    years), the live model list is fetched and every remaining candidate
    is tried in preference order before giving up.
    """
    key = api_key.strip() or _api_key()
    candidates = [_model_name()]
    tried: set = set()
    while candidates:
        model = candidates.pop(0)
        if model in tried:
            continue
        tried.add(model)
        result, model_gone = _complete_with_model(prompt, max_output_tokens, key, model)
        if result:
            return result
        if model_gone:
            # Discover what models actually exist for this key right now
            # and queue the ones we have not tried yet.
            for name in list_available_models(key):
                if name not in tried:
                    candidates.append(name)
            if candidates:
                LOGGER.warning(
                    "Model '%s' is gone (404); falling through to: %s",
                    model, ", ".join(candidates[:5]),
                )
            continue
        # Non-404 failure (quota/outage): the model exists but this key or
        # Google is struggling — switching model names will not help.
        return ""
    LOGGER.error("Every available Gemini model failed; yielding until next wake.")
    return ""


def _complete_with_model(
    prompt: str, max_output_tokens: int, key: str, model: str
) -> tuple:
    """One model attempt. Returns (text, model_gone_404)."""
    url = BASE_URL.format(model=model)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_output_tokens,
            "temperature": 0.7,
        },
    }

    attempt = 0
    deadline = time.monotonic() + CALL_BUDGET_SECONDS
    while attempt <= MAX_RETRIES:
        if time.monotonic() >= deadline:
            LOGGER.warning(
                "Gemini call budget (%ss) spent (outage or slow network); "
                "yielding so the router can rotate keys/providers.",
                CALL_BUDGET_SECONDS,
            )
            return "", False
        try:
            # The key travels in a header, never in the URL: query strings
            # end up in proxies, error messages and traceback URLs.
            response = requests.post(
                url,
                headers={"x-goog-api-key": key},
                json=payload,
                timeout=90,
            )
        except requests.RequestException as exc:
            LOGGER.warning("Gemini request failed (attempt %s): %s", attempt, exc)
            attempt += 1
            time.sleep(BASE_BACKOFF_SECONDS * (2 ** min(attempt, 4)))
            continue

        if response.status_code == 200:
            try:
                data = response.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    return "", False
                parts = candidates[0].get("content", {}).get("parts") or []
                text = "".join(part.get("text", "") for part in parts)
                return text.strip(), False
            except ValueError as exc:
                LOGGER.error("Could not parse Gemini response: %s", exc)
                return "", False

        if response.status_code in (429, 500, 503):
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else BASE_BACKOFF_SECONDS * (2 ** min(attempt, 4))
            except ValueError:
                delay = BASE_BACKOFF_SECONDS * (2 ** min(attempt, 4))
            LOGGER.warning(
                "Gemini returned %s; retrying in %.0fs (attempt %s)",
                response.status_code,
                delay,
                attempt,
            )
            if response.status_code == 429 and attempt >= 2:
                _wait_for_quota()
            else:
                time.sleep(delay)
            attempt += 1
            continue

        if response.status_code == 400:
            LOGGER.error("Gemini rejected the request (400): %s", response.text[:300])
            return "", False

        if response.status_code in (401, 403):
            LOGGER.error(
                "Gemini API key rejected (%s). Ask the founder to rotate "
                "the GEMINI_API_KEY secret.", response.status_code,
            )
            return "", False

        if response.status_code == 404:
            # The model was retired — signal the caller to discover live
            # models and fall through, instead of failing the whole call.
            LOGGER.warning("Gemini model '%s' not found (404); will try live model list.", model)
            return "", True

        LOGGER.error("Gemini unexpected status %s: %s", response.status_code, response.text[:300])
        attempt += 1
        time.sleep(BASE_BACKOFF_SECONDS * (2 ** min(attempt, 4)))

    # Do NOT crash the whole wake cycle over quota: callers treat an empty
    # string as "no answer this cycle" and simply retry on the next wake.
    LOGGER.warning("Gemini did not succeed after all retries; yielding until next wake.")
    return "", False


def _wait_for_quota() -> None:
    """Sleep until the free-tier quota window resets (bounded)."""
    LOGGER.warning(
        "Free-tier quota exhausted. Sleeping up to %s seconds before retrying.",
        QUOTA_MAX_WAIT_SECONDS,
    )
    waited = 0
    while waited < QUOTA_MAX_WAIT_SECONDS:
        time.sleep(min(QUOTA_POLL_SECONDS, QUOTA_MAX_WAIT_SECONDS - waited))
        waited += QUOTA_POLL_SECONDS
        LOGGER.info("Quota wait progress: %ss elapsed.", waited)


def is_healthy() -> bool:
    """Minimal health probe: ask the model for a one-word answer."""
    try:
        result = complete("Reply with exactly: OK", max_output_tokens=8)
        return "ok" in result.lower()
    except Exception:
        return False