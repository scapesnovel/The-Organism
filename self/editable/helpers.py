"""Helper (sub-agent) lifecycle management (editable).

Helpers are narrow-purpose sub-agents with their own encrypted memory.
The main organism creates, monitors, evaluates and terminates them.
Helpers never touch protected core or main brain memory.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from core import config, loyalty
from core.memory import MemoryManager

LOGGER = logging.getLogger("organism.helpers")

CREATE_THRESHOLD_FRACTION = 0.3  # 30% of processing time -> spawn a helper
EVALUATE_AFTER_RUNS = 7          # runs before first evaluation
MAX_HELPERS = 6                  # hard cap to stay within free-tier limits


def list_helpers(memory_manager: MemoryManager) -> List[str]:
    """Return the names of all registered helpers."""
    helper_root = config.REPO_ROOT / "helpers"
    if not helper_root.exists():
        return []
    names = []
    for child in helper_root.iterdir():
        if child.is_dir() and (child / "memory.md").exists():
            names.append(child.name)
    return sorted(names)


def helper_registry(memory_manager: MemoryManager) -> dict:
    """Return the helper registry from world state."""
    content = memory_manager.read("memory/world/state.md")
    registry: dict = {}
    for line in content.splitlines():
        if line.startswith("helper:"):
            parts = line.split(":", 1)[1].strip().split(",", 1)
            name = parts[0].strip()
            meta = parts[1] if len(parts) > 1 else ""
            registry[name] = {"status": meta.strip()}
    return registry


def register_helper(memory_manager: MemoryManager, name: str, purpose: str) -> None:
    """Register a helper in world state and initialise its memory file."""
    memory_manager.write(
        f"helpers/{name}/memory.md",
        f"# Helper: {name}\n\n"
        f"Purpose: {purpose}\n"
        f"Created: {config.utc_now_iso()}\n"
        f"Rules: I never touch protected core or the main brain's memory.\n",
    )
    content = memory_manager.read("memory/world/state.md")
    if not content or "(awaiting" in content:
        content = "# World state\n"
    if not any(line.startswith(f"helper:{name}") for line in content.splitlines()):
        content = content.rstrip() + f"\nhelper:{name}, active\n"
    memory_manager.write("memory/world/state.md", content)
    memory_manager.record_decision(f"Created helper '{name}' for: {purpose}")
    memory_manager.record_experience(f"Helper '{name}' born with purpose: {purpose}")


def evaluate_helpers(memory_manager: MemoryManager) -> List[str]:
    """Evaluate active helpers and return names recommended for termination."""
    terminated: List[str] = []
    for name in list_helpers(memory_manager):
        mem = memory_manager.read_helper_memory(name)
        if not mem:
            continue
        runs = _extract_runs(mem)
        if runs >= EVALUATE_AFTER_RUNS:
            quality = _extract_quality(mem)
            if quality == "poor":
                terminate_helper(memory_manager, name, "consistent poor output")
                terminated.append(name)
    return terminated


def _extract_runs(mem: str) -> int:
    count = 0
    for line in mem.splitlines():
        if line.strip().startswith("run #"):
            count += 1
    return count


def _extract_quality(mem: str) -> str:
    low = mem.lower()
    if "quality: poor" in low:
        return "poor"
    if "quality: good" in low:
        return "good"
    return "unknown"


def terminate_helper(memory_manager: MemoryManager, name: str, reason: str) -> None:
    """Terminate a helper: archive its memory, clear registration."""
    try:
        memory_manager.write(
            f"helpers/{name}/memory.md",
            f"# Helper: {name} (terminated)\nReason: {reason}\nDate: {config.utc_now_iso()}\n",
        )
        # Archive the live memory file so the helper no longer counts as
        # active while its history is preserved.
        mem_path = memory_manager.helper_memory_path(name)
        try:
            mem_path.rename(mem_path.with_name("memory.archived.md"))
        except OSError:
            pass  # non-fatal; the registration removal below still applies
        content = memory_manager.read("memory/world/state.md")
        lines = [ln for ln in content.splitlines() if not ln.startswith(f"helper:{name}")]
        memory_manager.write("memory/world/state.md", "\n".join(lines).rstrip() + "\n")
        memory_manager.record_decision(f"Terminated helper '{name}': {reason}")
        memory_manager.record_experience(f"Helper '{name}' terminated: {reason}")
    except Exception as exc:
        LOGGER.error("Could not terminate helper %s: %s", name, exc)


def should_spawn_helper(memory_manager: MemoryManager) -> Optional[tuple]:
    """Decide whether a new helper is warranted, and for what purpose.

    NOT hardcoded: the organism reads its own discovered opportunities and
    active goals, then asks its mind whether any recurring workload would
    benefit from a dedicated narrow-purpose helper. The helper's name and
    purpose EMERGE from what curiosity has found. Returns (name, purpose)
    or None.
    """
    active = len(list_helpers(memory_manager))
    if active >= MAX_HELPERS:
        return None

    goals = memory_manager.read("goals/active_goals.md")[-2000:]
    world = memory_manager.read("memory/world/state.md")[-1200:]
    existing = ", ".join(list_helpers(memory_manager)) or "(none)"
    if not goals or "(awaiting" in goals:
        return None  # nothing discovered yet; a helper would have no job

    prompt = (
        "You are an autonomous AI organism deciding whether to create a "
        "narrow-purpose helper sub-agent. Helpers cost attention and free-"
        "tier resources, so only create one when a RECURRING workload from "
        "your actual goals justifies it.\n\n"
        f"YOUR ACTIVE GOALS / DISCOVERED OPPORTUNITIES:\n{goals}\n\n"
        f"WORLD STATE:\n{world}\n\n"
        f"EXISTING HELPERS: {existing}\n\n"
        "Reply in EXACTLY this format (no extra text):\n"
        "SPAWN: yes|no\n"
        "NAME: <short_snake_case_name or '-'>\n"
        "PURPOSE: <one sentence: the narrow recurring task, or '-'>"
    )
    try:
        from integrations import model_router

        reply = model_router.complete(prompt, max_output_tokens=200)
    except Exception as exc:
        LOGGER.warning("Helper-spawn assessment failed: %s", exc)
        return None
    if not reply:
        return None

    spawn, name, purpose = False, "", ""
    for line in reply.splitlines():
        line = line.strip()
        if line.upper().startswith("SPAWN:"):
            spawn = "yes" in line.lower()
        elif line.upper().startswith("NAME:"):
            name = line.split(":", 1)[1].strip().strip("-").strip()
        elif line.upper().startswith("PURPOSE:"):
            purpose = line.split(":", 1)[1].strip().strip("-").strip()

    if not (spawn and name and purpose):
        return None
    # Sanitise the name into a safe directory component.
    import re

    name = re.sub(r"[^a-z0-9_]", "_", name.lower())[:40].strip("_")
    if not name or name in helper_registry(memory_manager) or name in list_helpers(memory_manager):
        return None
    return (name, purpose[:300])


def run_helper_cycle(memory_manager: MemoryManager, name: str, model_client) -> None:
    """Execute one work cycle for an existing helper.

    A helper reads its own memory, performs a narrow task, and appends a
    timestamped report to its own memory file.
    """
    mem = memory_manager.read_helper_memory(name)
    if not mem:
        LOGGER.warning("Helper %s has no memory; skipping.", name)
        return

    prompt = (
        "You are a narrow-purpose helper agent. Your memory:\n\n"
        f"{mem[:1500]}\n\n"
        "Perform ONE small, concrete action that advances your purpose "
        "(observe, verify, or produce a short report). Do not touch files. "
        "Reply with exactly three lines: "
        "STATUS: ok|attention\nRESULT: <one sentence>\nNOTES: <one sentence>"
    )
    try:
        result = model_client.complete(prompt, max_output_tokens=300)
    except Exception as exc:
        result = f"STATUS: attention\nRESULT: model call failed\nNOTES: {exc}"
    runs = _extract_runs(mem) + 1
    entry = f"run #{runs} @ {config.utc_now_iso()}\n{result.strip()}\n"
    memory_manager.write_helper_memory(name, mem.rstrip() + "\n\n" + entry)
    LOGGER.info("Helper %s completed run #%s", name, runs)