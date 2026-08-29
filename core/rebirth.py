"""Memory-preserving rebirth (protected core).

The founder's specification:

  *"reset everything back to default (baby) and start afresh, only this
  time the memories don't get deleted so it learns what to avoid and how
  to tackle some things. [...] once a path has proven to be worthy and
  working, a revert or reset should not affect it as well, and it
  shouldn't lose memory of it — if it starts, it should know how to link
  back to it."*

What a rebirth does:

* **Preserved (never touched):** every memory tree (``memory/``),
  ``finance/``, ``goals/``, ``documentary/``, ``api_keys/`` — the
  organism keeps its name, birthday, lessons, failures, collected keys
  and financial history. Rebirth is amnesia-free by design.
* **Preserved (proven paths):** editable modules registered in the
  proven-paths ledger survive the reset verbatim. A strategy that earns
  is never thrown away.
* **Reset:** all *other* files under ``self/editable/`` are restored to
  their genesis snapshots (``self/genesis/``), the stage returns to
  ``baby``, and helper registrations are archived (helper memories are
  moved to ``helpers/_archive/`` so their lessons survive, per the
  founder's rule that memories are never deleted).

Triggers:

* The founder opens an issue titled ``RESET:<kill-phrase>`` (the same
  secret phrase that arms the kill switch authenticates a reset — the
  organism can read neither).
* The organism itself may request a rebirth by calling
  ``perform_rebirth`` when it judges its editable self irrecoverably
  broken (e.g. repeated hibernation with failed self-heals).

This module is part of the protected core: the organism may never edit
the rules of its own resurrection.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import config

LOGGER = logging.getLogger("organism.rebirth")

RESET_ISSUE_PREFIX = "RESET:"

# Genesis snapshots: pristine copies of every editable module, committed at
# build time. Restoring these is what "back to default (baby)" means for
# behaviour, while memory stays intact.
GENESIS_DIR: Path = config.SELF_DIR / "genesis"

# Encrypted ledger of proven paths. Each line the organism appends has the
# form ``proven: <rel_path or strategy-id> | evidence: <why>``.
PROVEN_PATHS_FILE = "memory/core/proven_paths.md"

# Plain-text rebirth journal (documentary material).
REBIRTH_LOG = "documentary/rebirths.md"


# ---------------------------------------------------------------------------
# Proven-path registry
# ---------------------------------------------------------------------------
def proven_paths(memory_manager) -> List[str]:
    """Return the file paths registered as proven (reset-immune)."""
    content = memory_manager.read(PROVEN_PATHS_FILE)
    paths: List[str] = []
    marker = "proven:"
    for line in content.splitlines():
        # Entries are appended with a timestamp prefix: "[<stamp>] proven: ...".
        lower = line.lower()
        idx = lower.find(marker)
        if idx == -1:
            continue
        value = line[idx + len(marker):].strip()
        path = value.split("|", 1)[0].strip()
        if path:
            paths.append(path.replace("\\", "/"))
    return paths


def mark_proven(memory_manager, rel_path: str, evidence: str) -> None:
    """Register a path/strategy as proven so resets never claw it back."""
    memory_manager.append(
        PROVEN_PATHS_FILE,
        f"proven: {rel_path} | evidence: {evidence}",
    )
    memory_manager.record_decision(
        f"Marked '{rel_path}' as a PROVEN path (reset-immune). Evidence: {evidence}"
    )


# ---------------------------------------------------------------------------
# Genesis snapshots
# ---------------------------------------------------------------------------
def ensure_genesis(repo_root: Optional[Path] = None) -> int:
    """Snapshot editable modules into self/genesis/ when absent.

    Genesis is written exactly once (first run); later self-edits must not
    overwrite the birth-state record. Returns the number of files snapshotted.
    """
    root = repo_root or config.REPO_ROOT
    genesis = root / "self" / "genesis"
    editable = root / "self" / "editable"
    if genesis.exists() and any(genesis.glob("*.py")):
        return 0
    genesis.mkdir(parents=True, exist_ok=True)
    count = 0
    for source in sorted(editable.glob("*.py")):
        shutil.copy2(source, genesis / source.name)
        count += 1
    LOGGER.info("Genesis snapshot created: %s editable modules.", count)
    return count


# ---------------------------------------------------------------------------
# Reset trigger detection
# ---------------------------------------------------------------------------
def check_reset_request(github_client, logger: logging.Logger) -> Optional[int]:
    """Return the RESET issue number when the founder has opened one.

    Authentication reuses the KILL_PHRASE secret: only the founder can
    read it, and the organism cannot forge it. Returns None when no
    verified reset is pending. The CALLER must close the returned issue
    after the rebirth completes — otherwise the still-open issue would
    re-trigger a rebirth on every future wake.
    """
    phrase = os.environ.get(config.ENV_KILL_PHRASE, "").strip()
    if not phrase:
        return None
    target = f"{RESET_ISSUE_PREFIX}{phrase}"
    try:
        issues = github_client.list_open_issues()
    except Exception as exc:
        logger.warning("Could not check for reset issue: %s", exc)
        return None
    for issue in issues:
        if (issue.get("title") or "").strip() == target:
            logger.critical("Verified RESET issue found — rebirth requested by the founder.")
            return issue.get("number")
    return None


# ---------------------------------------------------------------------------
# The rebirth itself
# ---------------------------------------------------------------------------
def perform_rebirth(memory_manager, reason: str, repo_root: Optional[Path] = None) -> dict:
    """Execute a memory-preserving reset. Returns a summary dict.

    Order matters: read the proven registry BEFORE touching any file, so a
    half-broken editable tree cannot corrupt the decision of what survives.
    """
    root = repo_root or config.REPO_ROOT
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = {"restored": 0, "preserved": [], "helpers_archived": 0, "stamp": stamp}

    survivors = set(proven_paths(memory_manager))

    # 1. Restore editable modules from genesis, skipping proven survivors.
    genesis = root / "self" / "genesis"
    editable = root / "self" / "editable"
    if genesis.exists():
        for snapshot in sorted(genesis.glob("*.py")):
            rel = f"self/editable/{snapshot.name}"
            if rel in survivors:
                summary["preserved"].append(rel)
                continue
            target = editable / snapshot.name
            shutil.copy2(snapshot, target)
            summary["restored"] += 1
    else:
        LOGGER.warning("No genesis snapshot exists; editable tree left as-is.")

    # 2. Archive helper memories (never delete — they are memories too).
    helpers_root = root / "helpers"
    archive_root = helpers_root / "_archive" / stamp.replace(":", "")
    if helpers_root.exists():
        for child in sorted(helpers_root.iterdir()):
            if not child.is_dir() or child.name == "_archive":
                continue
            archive_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(archive_root / child.name))
            summary["helpers_archived"] += 1

    # 3. Stage back to baby — identity (name, birthday, purpose) is memory
    # and is explicitly preserved.
    identity = memory_manager.read("memory/core/identity.md")
    if identity:
        lines = [l for l in identity.splitlines() if not l.startswith("stage:")]
        lines.append("stage: baby")
        memory_manager.write("memory/core/identity.md", "\n".join(lines) + "\n")
    memory_manager.update_world_state("stage", "baby")
    memory_manager.update_world_state("last_rebirth", stamp)

    # 4. Record everything — a rebirth the organism cannot remember would
    # doom it to repeat the same death.
    memory_manager.record_lesson(
        f"REBIRTH at {stamp}. Reason: {reason}. I keep all memories, all "
        f"proven paths ({sorted(survivors) or 'none'}), my name and my "
        "birthday. I restart behaviour from genesis and must avoid whatever "
        "led here."
    )
    memory_manager.record_decision(f"Rebirth executed: {reason}")
    memory_manager.append_plaintext(
        REBIRTH_LOG,
        f"Rebirth at {stamp}. Reason: {reason}. Restored {summary['restored']} "
        f"modules to genesis; preserved proven paths: "
        f"{', '.join(summary['preserved']) or 'none'}; archived "
        f"{summary['helpers_archived']} helpers.",
    )
    memory_manager.record_event(
        f"**Rebirth** — behaviour reset to genesis (stage: baby) while every "
        f"memory survived. Reason: {reason}"
    )
    LOGGER.critical("Rebirth complete: %s", summary)
    return summary
