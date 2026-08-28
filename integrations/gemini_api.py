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
DEFAULT_MODEL = "gemini-2.0-flash"

MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 4
QUOTA_POLL_SECONDS = 60
QUOTA_MAX_WAIT_SECONDS = 4 * 3600  # 4 hours


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


def complete(prompt: str, max_output_tokens: int = 1500) -> str:
    """Run a single completion with retries and quota-aware backoff."""
    key = _api_key()
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
    while attempt <= MAX_RETRIES:
        try:
            response = requests.post(
                url,
                params={"key": key},
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

        if response.status_code == 401:
            LOGGER.error(
                "Gemini API key rejected (401). Ask the founder to rotate "
                "the GEMINI_API_KEY secret."
            )
            return ""

        LOGGER.error("Gemini unexpected status %s: %s", response.status_code, response.text[:300])
        attempt += 1
        time.sleep(BASE_BACKOFF_SECONDS * (2 ** min(attempt, 4)))

    raise GeminiQuotaExhausted("Gemini did not succeed after all retries.")


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