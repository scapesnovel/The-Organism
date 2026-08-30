"""Real self-editing: the organism improves its own code (editable).

This is the mechanism the founder demanded from birth: *"the organism
should be able to edit its code so as to improve and adapt as it grows,
but it should not be able to edit the core."*

The full loop, run at most once per wake cycle:

1. **Decide** — the organism reflects on its lessons, failures and goals
   and asks its brain whether any file under ``self/editable/`` deserves
   an improvement right now (it usually answers "no change needed";
   editing is deliberate, not compulsive).
2. **Generate** — the brain produces the complete new file content.
3. **Guard** — the target path must be inside ``self/editable/`` and must
   never be a protected path (``core.loyalty.guard_write`` is consulted;
   this module itself is excluded so a single bad self-edit can never
   destroy the organism's ability to edit or revert).
4. **Snapshot** — the old file content is stored before anything changes.
5. **Verify** — the candidate is compiled (syntax), imported in a
   subprocess (import-time crashes), and smoke-checked. Nothing replaces
   the live file until every check passes.
6. **Apply or revert** — on success the edit is applied and logged in
   ``documentary/evolution.md``. On failure the error is captured and the
   organism *diagnoses* it: the traceback is sent back to the brain for
   one repair attempt; if the repaired version still fails, the snapshot
   is restored and the failure becomes a lesson.

Everything is recorded so a future reset keeps the memory of which edits
worked and which did not.
"""

from __future__ import annotations

import ast
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from core import config, loyalty
from core.memory import MemoryManager

LOGGER = logging.getLogger("organism.selfedit")

# Files this module may never touch, even inside self/editable/. Removing
# the editor's own ability to edit (or the modules the wake cycle depends
# on to recover) would be a self-inflicted lobotomy.
_UNEDITABLE_SELF = {
    "self/editable/self_editing.py",   # the editor itself
    "self/editable/__init__.py",       # package init: import machinery
}

# Ledger of every self-edit attempt (applied, repaired, reverted). Lives in
# committed, encrypted memory so resets can preserve the record.
EDIT_LEDGER = "memory/core/self_edits.md"

# At most one self-edit per wake cycle keeps changes reviewable and makes a
# bad edit trivially attributable.
MAX_EDITS_PER_CYCLE = 1


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def is_editable(rel_path: str) -> Tuple[bool, str]:
    """Return (allowed, reason) for a candidate self-edit target."""
    rel = rel_path.replace("\\", "/").lstrip("/")
    if rel in _UNEDITABLE_SELF:
        return False, f"{rel} is the self-editing machinery itself"
    if not rel.startswith("self/editable/"):
        return False, f"{rel} is outside self/editable/"
    if loyalty.is_protected_path(rel):
        return False, f"{rel} is protected core"
    if not rel.endswith(".py"):
        return False, f"{rel} is not a Python module"
    return True, "ok"


# ---------------------------------------------------------------------------
# Verification: compile, import in a subprocess, smoke-run
# ---------------------------------------------------------------------------
def verify_candidate(rel_path: str, source: str, repo_root: Optional[Path] = None) -> Tuple[bool, str]:
    """Verify candidate code WITHOUT touching the live file.

    Checks, in order:
    1. Python syntax (ast.parse — no execution at all).
    2. Import in an isolated subprocess with the candidate written to a
       shadow tree, so import-time errors cannot poison this process.

    Returns (ok, error_detail).
    """
    root = repo_root or config.REPO_ROOT

    # 1. Syntax.
    try:
        ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc}"

    # 2. Isolated import check. Build a shadow copy of the repository's
    # import surface (symlinks where possible, copy of the candidate file)
    # and import the module fresh in a subprocess.
    import shutil
    import tempfile

    normalized = rel_path.replace("\\", "/")
    if normalized.endswith(".py"):
        normalized = normalized[: -len(".py")]
    module_name = normalized.replace("/", ".")
    shadow = Path(tempfile.mkdtemp(prefix="organism_selfedit_"))
    try:
        for entry in ("core", "integrations", "self"):
            src = root / entry
            if not src.exists():
                continue
            dst = shadow / entry
            shutil.copytree(src, dst, dirs_exist_ok=True)
        candidate_path = shadow / rel_path
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(source, encoding="utf-8")

        probe = (
            "import sys; sys.path.insert(0, {root!r});\n"
            "import importlib; importlib.import_module({mod!r});\n"
            "print('IMPORT_OK')"
        ).format(root=str(shadow), mod=module_name)
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if "IMPORT_OK" not in (result.stdout or ""):
            detail = (result.stderr or result.stdout or "unknown import failure").strip()
            return False, f"ImportError in isolated check: {detail[-800:]}"
    except subprocess.TimeoutExpired:
        return False, "Import verification timed out (possible infinite loop at import time)"
    except Exception as exc:
        return False, f"Verification harness failure: {exc}"
    finally:
        shutil.rmtree(shadow, ignore_errors=True)

    return True, ""


# ---------------------------------------------------------------------------
# The decide → generate → verify → apply/diagnose/revert loop
# ---------------------------------------------------------------------------
def _list_editable_modules(repo_root: Optional[Path] = None) -> list:
    root = (repo_root or config.REPO_ROOT) / "self" / "editable"
    modules = []
    if root.exists():
        for path in sorted(root.glob("*.py")):
            rel = f"self/editable/{path.name}"
            if is_editable(rel)[0]:
                modules.append(rel)
    return modules


def _decide_edit(memory: MemoryManager, model_client) -> Optional[dict]:
    """Ask the brain whether any editable module deserves improvement now.

    Returns {"path": ..., "goal": ...} or None when no edit is warranted.
    """
    modules = _list_editable_modules()
    if not modules:
        return None
    lessons = memory.read("memory/core/lessons.md")[-1500:]
    try:
        failures = memory.read("documentary/failures.md")[-1200:]
    except Exception:
        failures = ""
    goals = memory.read("goals/active_goals.md")[-1000:]
    ledger = memory.read(EDIT_LEDGER)[-1500:]
    prompt = (
        "You are an autonomous AI organism that can edit its own code to "
        "improve itself. Editing is DELIBERATE, not compulsive: most cycles "
        "the correct answer is that no edit is needed.\n\n"
        f"EDITABLE MODULES: {', '.join(modules)}\n\n"
        f"RECENT LESSONS:\n{lessons or '(none)'}\n\n"
        f"RECENT FAILURES:\n{failures or '(none)'}\n\n"
        f"ACTIVE GOALS:\n{goals or '(none)'}\n\n"
        f"PAST SELF-EDITS (avoid repeating failures):\n{ledger or '(none)'}\n\n"
        "Is there ONE concrete, small, high-confidence improvement to ONE of "
        "those modules that would clearly help you learn, earn or survive "
        "better right now? Bug fixes and capability gaps you have personally "
        "hit take priority over speculative refactors.\n\n"
        "Reply in EXACTLY this format:\n"
        "EDIT: yes|no\n"
        "PATH: <one path from the list, or none>\n"
        "GOAL: <one sentence describing the specific improvement, or none>"
    )
    try:
        reply = model_client.complete(prompt, max_output_tokens=300)
    except Exception as exc:
        LOGGER.warning("Self-edit decision failed: %s", exc)
        return None
    if not reply:
        return None
    edit, path, goal = False, "", ""
    for line in reply.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("EDIT:"):
            edit = "yes" in stripped.lower()
        elif upper.startswith("PATH:"):
            path = stripped.split(":", 1)[1].strip()
        elif upper.startswith("GOAL:"):
            goal = stripped.split(":", 1)[1].strip()
    if not (edit and path in modules and goal and goal.lower() != "none"):
        return None
    return {"path": path, "goal": goal}


def _generate_new_source(rel_path: str, goal: str, current: str, model_client, error: str = "") -> str:
    """Ask the brain for the complete improved file content."""
    repair_note = (
        f"\n\nYOUR PREVIOUS ATTEMPT FAILED VERIFICATION WITH THIS ERROR — "
        f"diagnose it and fix the cause:\n{error}\n" if error else ""
    )
    prompt = (
        "You are improving one of your own Python modules. Return the "
        "COMPLETE new file content — every line, ready to save. Do not "
        "abbreviate, do not use placeholders like '...rest unchanged...'.\n\n"
        f"FILE: {rel_path}\n"
        f"IMPROVEMENT GOAL: {goal}\n"
        f"{repair_note}\n"
        "CURRENT CONTENT:\n"
        "```python\n"
        f"{current}\n"
        "```\n\n"
        "Rules:\n"
        "- Keep the module's public functions/classes and their signatures "
        "backward compatible (other modules import them).\n"
        "- Never hardcode secrets, keys or personal data.\n"
        "- Keep imports at top; only stdlib, existing project modules, and "
        "already-installed dependencies.\n"
        "- Make ONLY the improvement described; no unrelated rewrites.\n\n"
        "Reply with ONLY a fenced python code block containing the full file."
    )
    reply = model_client.complete(prompt, max_output_tokens=8000)
    return _extract_code_block(reply)


def _extract_code_block(reply: str) -> str:
    """Pull the python code out of a fenced block (tolerant)."""
    if not reply:
        return ""
    text = reply.strip()
    if "```" in text:
        parts = text.split("```")
        # parts like: [prefix, 'python\ncode', suffix, ...] — take the first
        # fenced chunk that parses as code-ish content.
        for chunk in parts[1:]:
            body = chunk
            if body.lower().startswith("python"):
                body = body[len("python"):]
            body = body.lstrip("\n")
            if body.strip():
                return body.rstrip() + "\n"
        return ""
    # No fences: assume the whole reply is code (some models do this).
    return text.rstrip() + "\n"


def _log_edit(memory: MemoryManager, rel_path: str, goal: str, outcome: str, detail: str = "") -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = f"self-edit | {rel_path} | goal: {goal} | outcome: {outcome}"
    if detail:
        entry += f" | detail: {detail[:300]}"
    memory.append(EDIT_LEDGER, entry)
    memory.append_plaintext(
        "documentary/evolution.md",
        f"Self-edit of `{rel_path}` — {outcome}. Goal: {goal}",
    )
    if outcome.startswith("reverted"):
        memory.append_plaintext(
            "documentary/failures.md",
            f"Self-edit of `{rel_path}` failed verification and was reverted. "
            f"Goal: {goal}. {detail[:300]}",
        )
        memory.record_lesson(f"Self-edit failure on {rel_path}: {detail[:200]}")
    LOGGER.info("Self-edit %s: %s (%s)", outcome, rel_path, stamp)


def run_self_edit_cycle(memory: MemoryManager, model_client) -> Optional[dict]:
    """Run one full self-editing cycle. Returns a result dict or None.

    Founder-queued edit requests (via the `self_edit` command) take
    priority over the organism's own ideas. Result: {"path", "goal",
    "outcome"} where outcome is one of "applied", "applied-after-repair",
    "reverted", "skipped".
    """
    decision = None
    try:
        from self.editable.commands import pop_queued_edit

        decision = pop_queued_edit(memory)
        if decision:
            LOGGER.info("Executing founder-queued self-edit: %s", decision["path"])
    except Exception as exc:
        LOGGER.warning("Could not read founder edit queue: %s", exc)

    if decision is None:
        decision = _decide_edit(memory, model_client)
    if decision is None:
        LOGGER.info("Self-edit cycle: no edit warranted this wake.")
        return None

    rel_path, goal = decision["path"], decision["goal"]
    allowed, why = is_editable(rel_path)
    if not allowed:
        LOGGER.warning("Self-edit refused: %s", why)
        _log_edit(memory, rel_path, goal, "skipped", why)
        return {"path": rel_path, "goal": goal, "outcome": "skipped"}

    live_path = config.REPO_ROOT / rel_path
    snapshot = live_path.read_text(encoding="utf-8") if live_path.exists() else ""

    # Attempt 1: generate and verify.
    candidate = _generate_new_source(rel_path, goal, snapshot, model_client)
    if not candidate.strip():
        _log_edit(memory, rel_path, goal, "skipped", "brain returned no code")
        return {"path": rel_path, "goal": goal, "outcome": "skipped"}

    ok, error = verify_candidate(rel_path, candidate)

    # Diagnose-and-repair: exactly one repair attempt with the error fed back.
    if not ok:
        LOGGER.warning("Self-edit candidate failed verification: %s — attempting repair.", error[:200])
        memory.record_experience(
            f"Self-edit candidate for {rel_path} failed verification; diagnosing: {error[:200]}"
        )
        repaired = _generate_new_source(rel_path, goal, candidate, model_client, error=error)
        if repaired.strip():
            ok2, error2 = verify_candidate(rel_path, repaired)
            if ok2:
                live_path.parent.mkdir(parents=True, exist_ok=True)
                live_path.write_text(repaired, encoding="utf-8")
                _log_edit(memory, rel_path, goal, "applied-after-repair", f"first error: {error[:200]}")
                return {"path": rel_path, "goal": goal, "outcome": "applied-after-repair"}
            error = error2
        # Revert: the live file was never touched, but restore defensively
        # in case a partial write ever happens in a future refactor.
        if snapshot:
            live_path.write_text(snapshot, encoding="utf-8")
        _log_edit(memory, rel_path, goal, "reverted", error)
        return {"path": rel_path, "goal": goal, "outcome": "reverted"}

    # Verified on the first try: apply.
    live_path.parent.mkdir(parents=True, exist_ok=True)
    live_path.write_text(candidate, encoding="utf-8")
    _log_edit(memory, rel_path, goal, "applied")
    return {"path": rel_path, "goal": goal, "outcome": "applied"}


# ---------------------------------------------------------------------------
# Post-apply runtime failure diagnosis (used by the wake cycle)
# ---------------------------------------------------------------------------
def diagnose_runtime_failure(memory: MemoryManager, model_client, error_text: str) -> str:
    """Ask the brain to diagnose a runtime error (per the founder's spec:
    'copy the error and try solving it using api key models').

    Returns the diagnosis text (also recorded as an experience)."""
    prompt = (
        "You are an autonomous AI organism diagnosing your own runtime "
        "failure. Read the error and produce a short diagnosis and remedy.\n\n"
        f"ERROR:\n{error_text[-2000:]}\n\n"
        "Reply with:\n"
        "CAUSE: <one sentence>\n"
        "REMEDY: <one or two sentences — concrete steps>\n"
        "REVERT: yes|no  (should the last self-edit be rolled back?)"
    )
    try:
        diagnosis = model_client.complete(prompt, max_output_tokens=300)
    except Exception as exc:
        diagnosis = f"(brain unavailable for diagnosis: {exc})"
    memory.record_experience(f"Runtime failure diagnosed: {diagnosis[:400]}")
    return diagnosis or ""


def last_edit_info(memory: MemoryManager) -> str:
    """Return the tail of the self-edit ledger (for reports)."""
    return memory.read(EDIT_LEDGER)[-600:]
