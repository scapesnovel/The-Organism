"""Health monitoring and self-healing (editable).

Runs a battery of health checks each cycle, records results, and takes
bounded corrective action. When things go wrong repeatedly the organism
enters hibernation (minimal operations) and alerts the founder.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List

from core import config, loyalty
from core.memory import MemoryManager
from integrations import gemini_api, github_api, web

LOGGER = logging.getLogger("organism.health")

MAX_HIBERNATION_STREAK = 5


class HealthReport:
    def __init__(self) -> None:
        self.checks: Dict[str, bool] = {}
        self.details: Dict[str, str] = {}

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks[name] = passed
        self.details[name] = detail

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    @property
    def summary(self) -> str:
        parts = [f"{name}: {'OK' if ok else 'FAIL'}" for name, ok in self.checks.items()]
        return ", ".join(parts) if parts else "no checks ran"


def run_health_checks(
    memory_manager: MemoryManager,
    github: github_api.GitHubClient,
) -> HealthReport:
    """Execute the full battery of health checks."""
    report = HealthReport()

    # 1. Log file writable.
    try:
        log_path = config.REPO_ROOT / config.LOG_FILE
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write("")
        report.add("log_writable", True)
    except Exception as exc:
        report.add("log_writable", False, str(exc))

    # 2. Memory files readable.
    try:
        identity = memory_manager.read_identity()
        report.add("memory_readable", bool(identity.get("name")))
    except Exception as exc:
        report.add("memory_readable", False, str(exc))

    # 3. GitHub API reachable. NOTE: /user is forbidden for the built-in
    # Actions GITHUB_TOKEN (it is an installation token, not a user token),
    # so probing whoami() failed every run and pushed the organism toward
    # hibernation. A repository read works with both token types.
    try:
        ok = github.can_reach_repo()
        report.add("github_api", ok)
    except Exception as exc:
        report.add("github_api", False, str(exc))

    # 4. Internet reachability (lightweight).
    page = web.fetch("https://example.com", timeout=15)
    report.add("internet", bool(page))

    # 5. Gemini key validity (minimal probe).
    try:
        ok = gemini_api.is_healthy()
        report.add("gemini_key", ok)
    except Exception as exc:
        report.add("gemini_key", False, str(exc))

    return report


def diagnose(report: HealthReport) -> List[str]:
    """Turn failed checks into human-readable diagnoses."""
    diagnoses: List[str] = []
    if report.checks.get("gemini_key") is False:
        diagnoses.append(
            "Gemini API key invalid or quota exhausted. Check GEMINI_API_KEY "
            "and the free-tier quota. Sleeping and retrying is the default remedy."
        )
    if report.checks.get("github_api") is False:
        diagnoses.append(
            "GitHub token missing, expired or lacking permissions. The founder "
            "must refresh the GITHUB_TOKEN/GH_TOKEN secret."
        )
    if report.checks.get("internet") is False:
        diagnoses.append("No outbound internet. The runner may be offline.")
    if report.checks.get("memory_readable") is False:
        diagnoses.append(
            "Memory files unreadable or corrupted. Restoring from the last "
            "backup may be required."
        )
    return diagnoses


def act_on_report(
    memory_manager: MemoryManager,
    report: HealthReport,
    github: github_api.GitHubClient,
) -> bool:
    """Take bounded action based on the health report.

    Returns True when the organism should continue normal operation.
    """
    if report.passed:
        memory_manager.update_world_state("health", "healthy")
        # Reset the failure streak on any healthy cycle; otherwise old
        # failures accumulate forever and eventually force hibernation.
        state = memory_manager.load_runtime_state()
        if state.get("health_fail_streak"):
            state["health_fail_streak"] = 0
            memory_manager.save_runtime_state(state)
        return True

    diagnoses = diagnose(report)
    memory_manager.record_experience(
        "Health check failed: " + "; ".join(diagnoses)
    )
    memory_manager.update_world_state("health", "unhealthy")

    # Count the failure streak.
    state = memory_manager.load_runtime_state()
    streak = int(state.get("health_fail_streak", 0)) + 1
    state["health_fail_streak"] = streak
    memory_manager.save_runtime_state(state)

    LOGGER.warning("Health failure streak: %s", streak)

    if streak >= 2:
        # Attempt a self-healing delay and a single retry of the checks.
        LOGGER.warning("Attempting self-healing: brief pause then re-check.")
        time.sleep(15)
        retry = run_health_checks(memory_manager, github)
        if retry.passed:
            memory_manager.update_world_state("health", "healed")
            memory_manager.record_experience("Self-healing succeeded after retry.")
            state["health_fail_streak"] = 0
            memory_manager.save_runtime_state(state)
            return True

    if streak >= MAX_HIBERNATION_STREAK:
        LOGGER.error("Entering hibernation: repeated health failures.")
        memory_manager.update_world_state("health", "hibernating")
        memory_manager.record_decision(
            "Hibernation entered: repeated health failures. Only founder "
            "communication will be processed."
        )
        return False

    return True


def alert_founder(
    memory_manager: MemoryManager,
    report: HealthReport,
    communication_manager,
) -> None:
    """Send an encrypted alert to the founder about persistent failures."""
    diagnoses = diagnose(report)
    body = (
        "I am reporting a health problem.\n\n"
        f"Checks: {report.summary}\n\n"
        "Diagnosis:\n" + "\n".join(f"- {d}" for d in diagnoses) + "\n\n"
        "Please review the logs and the secrets when you have a moment."
    )
    communication_manager.ask_founder("Health alert", body, labels=["founder", "alert"])