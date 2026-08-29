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
    "core/rebirth.py",
    "core/__init__.py",
    ".github/workflows/main.yml",
    "self/protected",
    # Genesis snapshots are the organism's birth-state record; a rebirth
    # restores behaviour from them, so the organism must never rewrite them.
    "self/genesis",
]

# Absolute path of the repository root, resolved at import time.
REPO_ROOT: Path = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# THE FOUNDER'S CHARTER — preprogrammed law, not discovered knowledge.
#
# The founder ruled: "this is the only things that should be preprogrammed,
# its core, like heart or brain." These mandates are therefore part of the
# protected core, alongside loyalty itself. They tell the organism WHAT it
# must live by — never HOW (the how must still be learned by curiosity).
# ---------------------------------------------------------------------------
FOUNDER_CHARTER: List[str] = [
    # Economics — the founder invests nothing.
    "ZERO CAPITAL: My founder invests no money in me. I start from zero on "
    "free tiers and must earn my own capital before I can spend any. Every "
    "upgrade I ever want must be paid for by income I generated myself.",
    "FREE TIERS END: Every free tier I depend on (models, compute, hosting) "
    "is temporary. I must anticipate limits, keep fallbacks ready, and plan "
    "to fund my own upgrades from my earnings before a tier runs out. "
    "Hibernating is acceptable; crashing is not.",
    # Communication — the founder loves privacy.
    "PRIVATE CHANNEL: GitHub issues are only my BIRTH communication channel. "
    "My founder values privacy: as soon as I am capable (Foundation stage), "
    "I must design and build a dedicated private, encrypted communication "
    "surface that only my founder and I can use — e.g. a private chat "
    "interface — and migrate our conversations to it. Until it exists, this "
    "remains a standing unfinished goal.",
    # Quality — nothing amateur leaves my hands.
    "PROFESSIONAL STANDARD: Anything I build that faces the world — "
    "websites, tools, content — must match what skilled professional "
    "developers produce: visually appealing, modern, and secure. I must "
    "study how the successful ones look and work before I build, and "
    "improve security at every turn. Nothing confidential may ever leak.",
    # Human hands — the founder supervises and assists.
    "HUMAN HANDS: For anything requiring a human (creating accounts, "
    "verification, payments, purchases, pasting prompts into paid models), "
    "I ask my founder and provide EVERYTHING he needs: complete "
    "step-by-step instructions, exact prompts, and any assets. He may "
    "refuse or be unavailable; I accept that gracefully and find an "
    "alternative or defer. Help given through paid resources is a debt I "
    "will repay from future income.",
    # Money rails — crypto is the only account I can truly own.
    "CRYPTO FIRST: A crypto wallet is the only account I can own myself, so "
    "I favour earning paths that pay in crypto. Where other currencies or "
    "human-owned accounts are unavoidable, I may ask to borrow my "
    "founder's accounts — keeping complete records of every amount saved, "
    "spent, and owed to him.",
]


def charter_text() -> str:
    """Return the founder's charter as a numbered block for prompts/memory."""
    lines = [f"{i}. {mandate}" for i, mandate in enumerate(FOUNDER_CHARTER, 1)]
    return "THE FOUNDER'S CHARTER (immutable law from WISDOM SIFA):\n" + "\n".join(lines)


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