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
# Preferred default; override via GEMINI_MODEL. Founder-verified working
# (2026-08-29). If Google retires or overloads it, the ladder below
# discovers and uses whatever is actually available — the default is a
# starting rung, never a dependency.
DEFAULT_MODEL = "gemini-3.6-flash"

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

BASE_BACKOFF_SECONDS = 4
# Hard wall-clock budget for ONE completion call (request time + backoff
# sleeps). During a Google outage (503s / read timeouts) unbounded retries
# burned ~5 minutes per call inside CI; with many calls per wake that
# exhausts the 15-minute job timeout and the Actions minute budget. When
# the budget is spent we yield: the router rotates to the next key/provider
# or the organism hibernates until the next wake.
CALL_BUDGET_SECONDS = 150
# NOTE: quota sleeping was removed entirely — free-tier quotas are per
# model, so on 429 the client slides DOWN the model ladder immediately
# (a sibling model has its own untouched quota); sleeping in CI burns
# billable minutes for nothing.


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


# Per-model attempt cap for TRANSIENT failures (503 high demand, 500,
# timeouts). Two quick tries, then slide DOWN the version ladder to the
# next model instead of hammering an overloaded one — the founder's own
# probes showed gemini-3.7-flash drowning in traffic (503) while 3.6 and
# 3.5 answered instantly. Newest first, degrade gracefully.
MODEL_MAX_ATTEMPTS = 2

# Thinking-control escalation. Gemini models burn hidden reasoning tokens
# against maxOutputTokens (the founder measured ~300 thought tokens for a
# 4-token answer — HTTP 200 with EMPTY text at small budgets). But each
# model generation accepts a DIFFERENT control and 400-rejects the others
# with a generic "invalid argument" that never names the field (the
# founder's live run proved this). So on any 400 we escalate down this
# list deterministically instead of parsing error prose:
#   1. thinkingBudget 0  — 2.5-class models: disables thinking entirely.
#   2. thinkingLevel low — 3.x-class models: thinking cannot be disabled,
#      only minimised.
#   3. None              — no thinking field at all (always accepted);
#      THINKING_HEADROOM_TOKENS is added to the budget so hidden
#      reasoning cannot starve the visible answer to empty.
# The first accepted config is cached per model for the process lifetime.
_THINKING_CONFIGS = (
    {"thinkingBudget": 0},
    {"thinkingLevel": "low"},
    None,
)
THINKING_HEADROOM_TOKENS = 1024
_config_cache: dict = {}


def complete(prompt: str, max_output_tokens: int = 1500, api_key: str = "") -> str:
    """Run a completion, sliding down the model ladder until one answers.

    ``api_key`` lets the model router rotate through key variants
    (GEMINI_API_KEY, GEMINI_API_KEY_2, ...); when empty, the primary
    environment key is used as before.

    Ladder behaviour (newest → oldest):

    * The configured model (GEMINI_MODEL or default) is tried first.
    * ``gone`` (404, retired name) → discover the live model list and
      queue every remaining candidate, newest preferred class first.
    * ``busy`` (503 high demand / 500 / timeouts after MODEL_MAX_ATTEMPTS,
      or 429 per-model quota) → fall through to the NEXT model — Gemini
      free-tier quotas and demand spikes are per model, so a sibling
      often answers instantly while the newest drowns in traffic.
    * ``fatal`` (400 bad request / 401 / 403 key rejected) → stop; no
      model name can fix a broken key or request.

    One shared wall-clock budget (CALL_BUDGET_SECONDS) covers the WHOLE
    ladder so a bad day still cannot burn CI minutes.
    """
    key = api_key.strip() or _api_key()
    deadline = time.monotonic() + CALL_BUDGET_SECONDS
    candidates = [_model_name()]
    tried: set = set()
    discovered = False
    while candidates:
        if time.monotonic() >= deadline:
            LOGGER.warning(
                "Gemini call budget (%ss) spent; yielding so the router can "
                "rotate keys/providers.", CALL_BUDGET_SECONDS,
            )
            return ""
        model = candidates.pop(0)
        if model in tried:
            continue
        tried.add(model)
        text, verdict = _complete_with_model(prompt, max_output_tokens, key, model, deadline)
        if verdict == "ok" and text:
            return text
        if verdict == "fatal":
            return ""
        # "gone", "busy" or "empty": widen the ladder once with the live
        # model list, then keep sliding down to the next candidate.
        if not discovered:
            discovered = True
            for name in list_available_models(key):
                if name not in tried and name not in candidates:
                    candidates.append(name)
        if candidates:
            LOGGER.warning(
                "Model '%s' unavailable (%s); sliding down the ladder to: %s",
                model, verdict, ", ".join(candidates[:5]),
            )
    LOGGER.error("Every available Gemini model failed; yielding until next wake.")
    return ""


def _complete_with_model(
    prompt: str, max_output_tokens: int, key: str, model: str, deadline: float
) -> tuple:
    """One model's attempts. Returns (text, verdict).

    verdict: "ok" | "gone" (404 retired) | "busy" (demand/quota/transport)
    | "empty" (200 but no text — thinking burn or safety filter)
    | "fatal" (bad key or bad request — no other model can help).
    """
    url = BASE_URL.format(model=model)

    config_idx = _config_cache.get(model, 0)

    def _build_payload() -> dict:
        generation: dict = {
            "maxOutputTokens": max_output_tokens,
            "temperature": 0.7,
        }
        thinking = _THINKING_CONFIGS[config_idx]
        if thinking is not None:
            generation["thinkingConfig"] = dict(thinking)
        else:
            # No way to suppress thinking on this model: give the hidden
            # reasoning HEADROOM on top of the caller's budget so the
            # visible answer is never starved to empty.
            generation["maxOutputTokens"] = max_output_tokens + THINKING_HEADROOM_TOKENS
        return {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": generation,
        }

    payload = _build_payload()
    attempt = 0
    while attempt < MODEL_MAX_ATTEMPTS:
        if time.monotonic() >= deadline:
            return "", "busy"
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
            LOGGER.warning("Gemini '%s' request failed (attempt %s): %s", model, attempt, exc)
            attempt += 1
            time.sleep(BASE_BACKOFF_SECONDS)
            continue

        if response.status_code == 200:
            try:
                data = response.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    return "", "busy"
                parts = candidates[0].get("content", {}).get("parts") or []
                text = "".join(
                    part.get("text", "")
                    for part in parts
                    if not part.get("thought")  # never mistake thoughts for the answer
                ).strip()
                if text:
                    return text, "ok"
                # 200 with no text: the model spent the whole token budget
                # thinking, or a safety filter swallowed the answer. Name
                # the cause honestly so the founder's logs make sense.
                finish = (candidates[0].get("finishReason") or "?")
                thoughts = (
                    (data.get("usageMetadata") or {}).get("thoughtsTokenCount") or 0
                )
                LOGGER.warning(
                    "Gemini '%s' answered 200 but with EMPTY text "
                    "(finishReason=%s, thoughtTokens=%s).",
                    model, finish, thoughts,
                )
                return "", "empty"
            except ValueError as exc:
                LOGGER.error("Could not parse Gemini response: %s", exc)
                return "", "busy"

        if response.status_code in (500, 503):
            LOGGER.warning(
                "Gemini '%s' returned %s (high demand/outage), attempt %s.",
                model, response.status_code, attempt,
            )
            attempt += 1
            time.sleep(BASE_BACKOFF_SECONDS)
            continue

        if response.status_code == 429:
            # Free-tier quotas are PER MODEL: a sibling model usually has
            # its own untouched quota. Slide down immediately — no sleeping.
            LOGGER.warning("Gemini '%s' quota exhausted (429); sliding to next model.", model)
            return "", "busy"

        if response.status_code == 400:
            body = response.text[:300]
            # REQUEST REJECTION HANDLING. Different Gemini generations
            # accept different thinking controls and reject the others
            # with a GENERIC 400 ("Request contains an invalid argument")
            # that never names the offending field — the founder's live
            # run proved that message-sniffing does not work. So we never
            # guess from the error text: any 400 while a thinking config
            # is still applied means "this model rejects this config" —
            # escalate deterministically down _THINKING_CONFIGS and retry.
            # Only a 400 on the BARE request (no thinking config left to
            # remove) is a genuinely broken request, and only that is
            # fatal.
            if config_idx < len(_THINKING_CONFIGS) - 1:
                config_idx += 1
                _config_cache[model] = config_idx
                LOGGER.info(
                    "Gemini '%s' rejected the request shape (400); retrying "
                    "with thinking config %s/%s.",
                    model, config_idx + 1, len(_THINKING_CONFIGS),
                )
                payload = _build_payload()
                continue
            LOGGER.error("Gemini '%s' rejected a bare request (400): %s", model, body)
            return "", "fatal"

        if response.status_code in (401, 403):
            LOGGER.error(
                "Gemini API key rejected (%s). Ask the founder to rotate "
                "the GEMINI_API_KEY secret.", response.status_code,
            )
            return "", "fatal"

        if response.status_code == 404:
            # The model was retired — signal the caller to discover live
            # models and fall through, instead of failing the whole call.
            LOGGER.warning("Gemini model '%s' not found (404); will try live model list.", model)
            return "", "gone"

        LOGGER.error("Gemini unexpected status %s: %s", response.status_code, response.text[:300])
        attempt += 1
        time.sleep(BASE_BACKOFF_SECONDS)

    # Attempts for THIS model spent — the caller slides down the ladder.
    LOGGER.warning("Gemini '%s' did not answer after %s attempts; sliding down.", model, MODEL_MAX_ATTEMPTS)
    return "", "busy"


def is_healthy() -> bool:
    """Minimal health probe: ask the model for a one-word answer.

    The token budget is deliberately generous: thinking models may spend
    hidden reasoning tokens against maxOutputTokens, and a probe that
    starves the model of answer room reports false outages.
    """
    try:
        result = complete("Reply with exactly: OK", max_output_tokens=256)
        return "ok" in result.lower()
    except Exception:
        return False