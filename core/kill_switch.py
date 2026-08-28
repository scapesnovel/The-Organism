"""Protected kill switch.

The kill switch is absolute and cannot be disabled by the organism:

* The secret phrase lives in the repository's encrypted GitHub Secrets
  store (``KILL_PHRASE``) which the organism cannot read back.
* The check runs before any other operation in every wake cycle.
* The organism can never edit this module (see core/loyalty.py).

When a GitHub issue is opened whose title exactly matches
``KILL:<phrase>`` the organism immediately halts all operations.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Optional

from . import config

KILL_ISSUE_PREFIX: str = "KILL:"
_KILL_LOG_LINE: str = "KILL SWITCH TRIGGERED — halting all operations permanently."

# Marker file written to the repository when the switch is tripped so that
# every future run exits early even if the issue is later deleted.
KILL_MARKER: str = "runtime/kill_switch_tripped.txt"


def generate_kill_phrase(length: int = 18) -> str:
    """Generate a cryptographically random kill phrase (used only at birth)."""
    return secrets.token_urlsafe(length)


def kill_issue_title(phrase: str) -> str:
    """Return the exact issue title that trips the kill switch."""
    return f"{KILL_ISSUE_PREFIX}{phrase}"


def _phrase_from_env() -> Optional[str]:
    phrase = os.environ.get(config.ENV_KILL_PHRASE, "").strip()
    return phrase or None


def _marker_exists() -> bool:
    try:
        return (config.REPO_ROOT / KILL_MARKER).exists()
    except Exception:
        return False


def _write_marker(logger: logging.Logger) -> None:
    marker = config.REPO_ROOT / KILL_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "The kill switch was triggered by the founder's verified KILL issue. "
        "The organism has permanently halted.\n",
        encoding="utf-8",
    )
    logger.critical("Kill marker written to %s", marker)


def check_kill_switch(github_client, logger: logging.Logger) -> bool:
    """Return True when the kill switch is (or has been) tripped.

    ``github_client`` must expose a ``list_open_issues()`` method returning
    a list of dictionaries that each contain a ``title`` key.
    """
    # A durable marker outlives the issue itself and short-circuits every run.
    if _marker_exists():
        logger.critical(_KILL_LOG_LINE)
        return True

    phrase = _phrase_from_env()
    if phrase is None:
        # The phrase has not been provisioned yet, so the switch cannot be
        # armed. The birth routine logs this as a pending security task.
        return False

    target_title = kill_issue_title(phrase)
    try:
        issues = github_client.list_open_issues()
    except Exception as exc:  # Network failure must not bypass an armed switch.
        logger.error("Could not check for kill issue: %s", exc)
        # Fail-safe: assume the switch may be armed and skip further work.
        return True

    for issue in issues:
        title = (issue.get("title") or "").strip()
        if title == target_title:
            logger.critical(_KILL_LOG_LINE)
            _write_marker(logger)
            return True

    return False


def record_kill_event(logger: logging.Logger, memory_manager) -> None:
    """Write the final timeline entry when the switch trips (best effort)."""
    try:
        entry = (
            "### Kill switch engaged\n"
            "The founder's verified KILL issue was received. "
            "The organism halted permanently.\n"
        )
        memory_manager.append_plaintext("documentary/timeline.md", entry)
    except Exception:
        pass
    try:
        logger.info("Final log entry written before shutdown.")
    except Exception:
        pass