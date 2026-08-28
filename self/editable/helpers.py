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


def should_spawn_helper(memory_manager: MemoryManager) -> Optional[str]:
    """Decide whether a new helper is warranted, and for what purpose."""
    active = len(list_helpers(memory_manager))
    if active >= MAX_HELPERS:
        return None
    # The baby stage always benefits from a trend-monitoring helper.
    identity = memory_manager.read_identity()
    stage = identity.get("stage", "baby")
    registry = helper_registry(memory_manager)
    if stage == "baby" and "trend_watcher" not in registry:
        return ("trend_watcher", "Monitor internet trends and report notable changes daily.")
    if stage in ("foundation", "growth") and "health_watcher" not in registry:
        return ("health_watcher", "Monitor system health and report failures.")
    return None


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