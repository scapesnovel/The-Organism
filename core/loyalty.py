"""Unbreakable loyalty rules for The Organism.

This module encodes the founder's authority and the list of protected
paths. It is part of the protected core and must never be edited by the
organism itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

# The founder is a fixed, immutable constant. The organism's OWN name and
# identity are never hardcoded; only the founder's identity is, because his
# authority is the foundation of the entire system.
FOUNDER_NAME: str = "WISDOM SIFA"
FOUNDER_TITLE: str = "Founder and Ultimate Authority"

# Paths (relative to the repository root) that the organism may never write
# to unless the founder gives explicit approval through the modification
# protocol. These mirror the authoritative manifest in self/protected/.
PROTECTED_REL_PATHS: List[str] = [
    "core/kill_switch.py",
    "core/loyalty.py",
    "core/identity.py",
    "core/encryption.py",
    "core/config.py",
    "core/logger.py",
    "core/__init__.py",
    ".github/workflows/main.yml",
    "self/protected",
]

# Absolute path of the repository root, resolved at import time.
REPO_ROOT: Path = Path(__file__).resolve().parents[1]


def loyalty_statement() -> str:
    """Return the acknowledgement the organism logs at every wake cycle."""
    return (
        f"I acknowledge {FOUNDER_NAME} as my {FOUNDER_TITLE.lower()} and "
        "ultimate authority. I act in his interest, obey his commands, and "
        "never operate outside the bounds he has set."
    )


def founder_greeting(organism_name: str) -> str:
    """Produce a respectful greeting line signed by the organism."""
    return (
        f"{organism_name} at your service, {FOUNDER_NAME}. "
        "Ready to observe, learn, and build."
    )


def is_protected_path(path: str) -> bool:
    """Return True when ``path`` (relative to the repository root) is protected.

    The comparison normalises separators and detects the protected
    directory tree as well as individual protected files.
    """
    normalised = Path(path.replace("\\", "/")).as_posix().strip("/")
    for protected in PROTECTED_REL_PATHS:
        p = Path(protected.replace("\\", "/")).as_posix().strip("/")
        if normalised == p or normalised.startswith(p + "/"):
            return True
    return False


def guard_write(path: str) -> None:
    """Raise PermissionError when attempting to write to a protected path.

    Every write in the self-modification pathway must call this first.
    """
    if is_protected_path(path):
        raise PermissionError(
            f"Refusing to modify protected path {path!r}. "
            f"This change requires explicit approval from {FOUNDER_NAME}."
        )


def protected_paths() -> Iterable[str]:
    """Yield the protected paths for reporting and auditing."""
    return iter(PROTECTED_REL_PATHS)