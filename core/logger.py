"""Logging infrastructure with secret redaction.

Every log line passes through a filter that replaces any secret value
found in the environment with ``[REDACTED]`` so that tokens and keys are
never written to disk in plaintext.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional

# Environment variable names whose *values* must never appear in logs.
_SECRET_ENV_NAMES: List[str] = [
    "GEMINI_API_KEY",
    "ORGANISM_PRIVATE_KEY",
    "FOUNDER_PUBLIC_KEY",
    "KILL_PHRASE",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "ORGANISM_WALLET_KEY",
]


class _SecretRedactor(logging.Filter):
    """Replaces known secret values with a redaction marker."""

    def __init__(self) -> None:
        super().__init__()
        self._needles = self._collect_secrets()

    @staticmethod
    def _collect_secrets() -> List[str]:
        values: List[str] = []
        for name in _SECRET_ENV_NAMES:
            value = os.environ.get(name)
            if value:
                values.append(value)
        # Longest first so that overlapping secrets are fully replaced.
        values.sort(key=len, reverse=True)
        return values

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            for needle in self._needles:
                if needle and len(needle) >= 8:
                    message = message.replace(needle, "[REDACTED]")
            record.msg = message
            record.args = ()
        except Exception:
            # Redaction must never break logging.
            pass
        return True


def setup_logging(
    log_path: Optional[Path] = None,
    level: int = logging.INFO,
    console: bool = True,
) -> logging.Logger:
    """Configure the root logger with a rotating file handler and a console.

    Returns the configured logger. Calling this more than once is safe
    because handlers are reset first.
    """
    logger = logging.getLogger("organism")
    logger.setLevel(level)

    # Reset handlers to avoid duplication when re-configuring.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    redactor = _SecretRedactor()

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(log_path),
            maxBytes=1_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        logger.addHandler(file_handler)

    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(redactor)
        logger.addHandler(stream_handler)

    logger.propagate = False
    return logger