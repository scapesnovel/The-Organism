"""Hierarchical, encrypted memory access for The Organism (protected core).

Memory layout:

* ``memory/core``        — main brain memory (identity, experiences,
                           lessons, decisions). Only the main organism
                           touches these files. Helpers never do.
* ``memory/knowledge``   — facts and observations about the world.
* ``memory/skills``      — descriptions of what the organism knows how to do.
* ``memory/world``       — current state of the environment.
* ``helpers/<name>/``    — helper memory, one file per helper. The main
                           organism may read these but must not overwrite
                           them (except when resetting/terminating a helper).
* ``documentary``        — founder-facing documentation (milestones,
                           failures, evolution, quotes, timeline).
* ``finance``            — financial records.
* ``goals``              — goal tracking.
* ``api_keys``           — API key inventory (encrypted).

Sensitive files are encrypted at rest with the organism's public key. The
documentary, finance, goals and api_keys trees are encrypted as well, per
the founder's requirement that *all* sensitive data be encrypted at rest.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import config, loyalty
from .encryption import EncryptionManager, EncryptionError

LOGGER = logging.getLogger("organism.memory")

# Trees that are encrypted at rest by default.
_ENCRYPTED_TREES: List[str] = [
    "memory/core",
    "memory/knowledge",
    "memory/skills",
    "memory/world",
    "helpers",
    "finance",
    "goals",
    "api_keys",
    "documentary",
]

# Trees that are plain text by default (logs, reports drafts, runtime state).
_PLAINTEXT_TREES: List[str] = ["logs", "runtime", "reports", "self"]

# Known documentary files, so the organism can append to them naturally.
_DOCUMENTARY_FILES: List[str] = [
    "milestones.md",
    "failures.md",
    "evolution.md",
    "earnings.md",
    "quotes.md",
    "timeline.md",
]


class MemoryManager:
    """Provides encrypted read/write access to the organism's memory tree.

    ``encryption`` must be an EncryptionManager whose organism key is
    already imported. When encryption is unavailable the manager degrades
    to plaintext and warns loudly, so the organism still wakes and can
    report the missing secret.
    """

    def __init__(
        self,
        encryption: Optional[EncryptionManager] = None,
        repo_root: Optional[Path] = None,
    ) -> None:
        self.encryption = encryption
        self.repo_root = repo_root or config.REPO_ROOT
        # Fall back to plaintext unless usable key material is actually
        # loaded. An EncryptionManager with no organism key (first run, or
        # missing ORGANISM_PRIVATE_KEY secret) would otherwise crash on
        # every encrypted write.
        self.plaintext_fallback = encryption is None or not getattr(
            encryption, "has_organism_key", lambda: False
        )()
        if self.plaintext_fallback:
            LOGGER.warning(
                "Encryption unavailable; memory will degrade to plaintext until "
                "the organism's PGP key is configured."
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _path(self, rel: str) -> Path:
        """Resolve a repository-relative path safely (no traversal)."""
        root = self.repo_root.resolve()
        candidate = (root / rel).resolve()
        # Portable containment check (Python 3.8 compatible).
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ValueError(f"Path escapes repository root: {rel}") from None
        return candidate

    @staticmethod
    def _is_encrypted(rel: str) -> bool:
        rel = rel.replace("\\", "/").lstrip("/")
        for tree in _ENCRYPTED_TREES:
            if rel == tree or rel.startswith(tree + "/"):
                return True
        return False

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _plain_file_stamp() -> str:
        return f"\n<!-- last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} -->\n"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def read(self, rel: str) -> str:
        """Read a memory file, decrypting when the tree is encrypted."""
        path = self._path(rel)
        if not path.exists():
            return ""
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            return ""
        if self._is_encrypted(rel) and not self.plaintext_fallback:
            # Files written before encryption was configured are plaintext;
            # only wrapped payloads need decryption.
            if "-----BEGIN ORGANISM ENCRYPTED DATA-----" not in content:
                return content
            try:
                return self.encryption.decrypt_file(path)
            except EncryptionError as exc:
                LOGGER.error("Could not decrypt %s: %s", rel, exc)
                return ""
        return content

    def write(self, rel: str, content: str) -> None:
        """Write a memory file, encrypting when the tree is encrypted."""
        path = self._path(rel)
        if self._is_encrypted(rel) and not self.plaintext_fallback:
            self.encryption.encrypt_file(path, content)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def append(self, rel: str, entry: str) -> None:
        """Append an entry with a timestamp to a memory file."""
        existing = self.read(rel)
        stamp = self._timestamp()
        updated = f"{existing.rstrip()}\n\n[{stamp}] {entry.strip()}\n" if existing else f"[{stamp}] {entry.strip()}\n"
        self.write(rel, updated)

    def append_plaintext(self, rel: str, entry: str) -> None:
        """Append a timestamped entry to a plain-text documentary/log file.

        Used for the timeline and logs where encryption would make the
        chronological record unreadable for the founder.
        """
        path = self._path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = self._timestamp()
        if path.exists():
            existing = path.read_text(encoding="utf-8")
        else:
            existing = ""
        line = f"\n### {stamp}\n{entry.strip()}\n"
        path.write_text(existing.rstrip() + line, encoding="utf-8")

    def ensure_initialized(self) -> None:
        """Seed the memory tree with initial files if they are missing."""
        seeds = [
            "memory/core/identity.md",
            "memory/core/experiences.md",
            "memory/core/lessons.md",
            "memory/core/decisions.md",
            "memory/knowledge/trends.md",
            "memory/knowledge/platforms.md",
            "memory/knowledge/ai_models.md",
            "memory/skills/skills.md",
            "memory/world/state.md",
            "finance/balance.md",
            "finance/income.md",
            "finance/expenses.md",
            "finance/owed_to_creator.md",
            "goals/active_goals.md",
            "goals/completed.md",
            "goals/abandoned.md",
            "goals/long_term.md",
            "api_keys/inventory.md",
            "api_keys/priority.md",
            "memory/world/to_explore.md",
        ]
        for rel in seeds:
            if not self._path(rel).exists():
                self.write(rel, f"# {rel}\n\n(awaiting first entry)\n")
        for name in _DOCUMENTARY_FILES:
            rel = f"documentary/{name}"
            if not self._path(rel).exists():
                self._path(rel).parent.mkdir(parents=True, exist_ok=True)
                self._path(rel).write_text(
                    f"# {name}\n\n_This record begins at birth._\n", encoding="utf-8"
                )

    # ------------------------------------------------------------------
    # Documentation & world-state helpers
    # ------------------------------------------------------------------
    def record_event(self, event: str) -> None:
        """Append a timestamped event to the plain-text timeline."""
        self.append_plaintext("documentary/timeline.md", event)

    def record_lesson(self, lesson: str) -> None:
        """Append a lesson to the encrypted lessons memory."""
        self.append("memory/core/lessons.md", lesson)

    def record_experience(self, experience: str) -> None:
        """Append an experience to the encrypted experiences memory."""
        self.append("memory/core/experiences.md", experience)

    def record_decision(self, decision: str) -> None:
        """Append a decision to the encrypted decisions memory."""
        self.append("memory/core/decisions.md", decision)

    def update_world_state(self, key: str, value: str) -> None:
        """Upsert a key/value pair in the encrypted world state."""
        rel = "memory/world/state.md"
        content = self.read(rel)
        if not content or content.strip().startswith("(awaiting"):
            content = "# World state\n"
        lines = [line for line in content.splitlines() if not line.startswith(key + ":")]
        lines.append(f"{key}: {value}")
        lines.append(self._plain_file_stamp())
        self.write(rel, "\n".join(lines) + "\n")

    def read_identity(self) -> dict:
        """Parse the identity file into a dictionary."""
        content = self.read("memory/core/identity.md")
        result: dict = {}
        for line in content.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip().lower()] = value.strip()
        return result

    # ------------------------------------------------------------------
    # JSON runtime state (plaintext, non-sensitive)
    # ------------------------------------------------------------------
    def load_runtime_state(self) -> dict:
        """Load the runtime state JSON (tolerant of absence/corruption)."""
        path = self._path(config.RUNTIME_STATE_FILE)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_runtime_state(self, state: dict) -> None:
        """Save the runtime state JSON (best-effort, non-fatal)."""
        try:
            path = self._path(config.RUNTIME_STATE_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
            )
        except Exception as exc:
            LOGGER.error("Could not save runtime state: %s", exc)

    # ------------------------------------------------------------------
    # Helper memory access (guarded)
    # ------------------------------------------------------------------
    def helper_memory_path(self, helper_name: str) -> Path:
        """Return the memory file path for a helper."""
        return self._path(f"helpers/{helper_name}/memory.md")

    def read_helper_memory(self, helper_name: str) -> str:
        """Read a helper's memory file (read-only for the main organism)."""
        return self.read(f"helpers/{helper_name}/memory.md")

    def write_helper_memory(self, helper_name: str, content: str) -> None:
        """Write a helper's memory file (reset/termination only)."""
        self.write(f"helpers/{helper_name}/memory.md", content)

    def guard_helper_write(self, rel: str) -> None:
        """Refuse writes into the main brain memory by any helper context."""
        rel = rel.replace("\\", "/").lstrip("/")
        if rel.startswith("memory/core/") or rel.startswith("core/"):
            raise PermissionError(
                f"Helpers must never write to main brain memory: {rel}"
            )