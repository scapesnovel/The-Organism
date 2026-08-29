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
DEFAULT_MODEL = "gemini-2.5-flash"  # current free-tier default; override via GEMINI_MODEL

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


def complete(prompt: str, max_output_tokens: int = 1500, api_key: str = "") -> str:
    """Run a single completion with retries and quota-aware backoff.

    ``api_key`` lets the model router rotate through key variants
    (GEMINI_API_KEY, GEMINI_API_KEY_2, ...); when empty, the primary
    environment key is used as before.
    """
    key = api_key.strip() or _api_key()
    model = _model_name()
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
            return ""
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
                    return ""
                parts = candidates[0].get("content", {}).get("parts") or []
                text = "".join(part.get("text", "") for part in parts)
                return text.strip()
            except ValueError as exc:
                LOGGER.error("Could not parse Gemini response: %s", exc)
                return ""

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
            return ""

        if response.status_code in (401, 403):
            LOGGER.error(
                "Gemini API key rejected (%s). Ask the founder to rotate "
                "the GEMINI_API_KEY secret.", response.status_code,
            )
            return ""

        if response.status_code == 404:
            LOGGER.error(
                "Gemini model '%s' not found (404). Set the GEMINI_MODEL "
                "secret to an available model name.", model,
            )
            return ""

        LOGGER.error("Gemini unexpected status %s: %s", response.status_code, response.text[:300])
        attempt += 1
        time.sleep(BASE_BACKOFF_SECONDS * (2 ** min(attempt, 4)))

    # Do NOT crash the whole wake cycle over quota: callers treat an empty
    # string as "no answer this cycle" and simply retry on the next wake.
    LOGGER.warning("Gemini did not succeed after all retries; yielding until next wake.")
    return ""


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