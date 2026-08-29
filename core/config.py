"""Central configuration for The Organism.

This module only declares *where* things live and *which* environment
variable names hold secrets. It must never contain secret values itself.
Actual secret values are injected by the GitHub Actions workflow from the
repository's encrypted Secrets store.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

# Repository root: one directory up from this file (core/ -> repository root).
REPO_ROOT: Path = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Secret environment variable names (names only, never values).
# The workflow maps GitHub Secrets to these names.
# ---------------------------------------------------------------------------
ENV_GEMINI_API_KEY = "GEMINI_API_KEY"          # Brain API key (free tier)
ENV_GEMINI_MODEL = "GEMINI_MODEL"              # Optional model override
ENV_ORGANISM_PRIVATE_KEY = "ORGANISM_PRIVATE_KEY"  # PGP private key (armored)
ENV_FOUNDER_PUBLIC_KEY = "FOUNDER_PUBLIC_KEY"      # Founder's PGP public key
ENV_FOUNDER_GITHUB_USERNAME = "FOUNDER_GITHUB_USERNAME"  # Founder's GitHub login
ENV_KILL_PHRASE = "KILL_PHRASE"                # Secret kill phrase
ENV_GH_TOKEN = "GH_TOKEN"                      # PAT with repository + secrets scope
ENV_ORGANISM_WALLET_KEY = "ORGANISM_WALLET_KEY"    # ETH wallet private key (hex)

# Provided automatically by GitHub Actions at runtime.
ENV_GITHUB_TOKEN = "GITHUB_TOKEN"
ENV_REPOSITORY = "GITHUB_REPOSITORY"
ENV_EVENT_NAME = "GITHUB_EVENT_NAME"
ENV_EVENT_PATH = "GITHUB_EVENT_PATH"

# ---------------------------------------------------------------------------
# Directory layout (relative to the repository root).
# ---------------------------------------------------------------------------
CORE_DIR: Path = REPO_ROOT / "core"
MEMORY_DIR: Path = REPO_ROOT / "memory"
MEMORY_CORE_DIR: Path = MEMORY_DIR / "core"
MEMORY_KNOWLEDGE_DIR: Path = MEMORY_DIR / "knowledge"
MEMORY_SKILLS_DIR: Path = MEMORY_DIR / "skills"
MEMORY_WORLD_DIR: Path = MEMORY_DIR / "world"
HELPERS_DIR: Path = REPO_ROOT / "helpers"
FINANCE_DIR: Path = REPO_ROOT / "finance"
GOALS_DIR: Path = REPO_ROOT / "goals"
REPORTS_DIR: Path = REPO_ROOT / "reports"
REPORTS_DAILY_DIR: Path = REPORTS_DIR / "daily"
SELF_DIR: Path = REPO_ROOT / "self"
SELF_EDITABLE_DIR: Path = SELF_DIR / "editable"
SELF_PROTECTED_DIR: Path = SELF_DIR / "protected"
SELF_BACKUP_DIR: Path = SELF_DIR / "backup"
DOCUMENTARY_DIR: Path = REPO_ROOT / "documentary"
API_KEYS_DIR: Path = REPO_ROOT / "api_keys"
LOGS_DIR: Path = REPO_ROOT / "logs"
SECRETS_DIR: Path = REPO_ROOT / "secrets"
RUNTIME_DIR: Path = REPO_ROOT / "runtime"
# Durable, COMMITTED state (birth marker, kill marker, runtime counters).
# GitHub Actions checks out a fresh workspace on every run, so any state
# that must survive between wake cycles has to live in a committed path—
# the git-ignored runtime/ tree is wiped every time.
STATE_DIR: Path = REPO_ROOT / "state"

# ---------------------------------------------------------------------------
# Key files.
# ---------------------------------------------------------------------------
IDENTITY_FILE: str = "memory/core/identity.md"      # Encrypted identity blob
IDENTITY_PUB_FILE: Path = CORE_DIR / "identity.pub"  # Public PGP key (public)
LOG_FILE: Path = LOGS_DIR / "system.log"
# The run counter / runtime state must persist across runs → committed state/.
RUNTIME_STATE_FILE: Path = STATE_DIR / "runtime_state.json"
PRIVATE_KEY_BACKUP_FILE: str = "secrets/private_key_backup.asc"   # Self-encrypted
FOUNDER_BOOTSTRAP_FILE: str = "secrets/founder_bootstrap.asc"     # Founder-encrypted

# ---------------------------------------------------------------------------
# Directory set used at bootstrap time to guarantee the tree exists.
# ---------------------------------------------------------------------------
REQUIRED_DIRS: List[Path] = [
    CORE_DIR,
    MEMORY_CORE_DIR,
    MEMORY_KNOWLEDGE_DIR,
    MEMORY_SKILLS_DIR,
    MEMORY_WORLD_DIR,
    HELPERS_DIR,
    FINANCE_DIR,
    GOALS_DIR,
    REPORTS_DAILY_DIR,
    SELF_EDITABLE_DIR,
    SELF_PROTECTED_DIR,
    SELF_BACKUP_DIR,
    DOCUMENTARY_DIR,
    API_KEYS_DIR,
    LOGS_DIR,
    SECRETS_DIR,
    RUNTIME_DIR,
    STATE_DIR,
]


def ensure_directories() -> None:
    """Create every directory the organism relies on. Idempotent."""
    for directory in REQUIRED_DIRS:
        directory.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_branch() -> str:
    """Return the current checked-out branch, falling back to a safe default."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        branch = (result.stdout or "").strip()
        if branch and branch != "HEAD":
            return branch
    except Exception:
        pass
    return os.environ.get("GITHUB_REF_NAME", "main")