"""Communication escalation policy (editable).

Defines how long the organism waits for the founder before acting on its
own judgement, depending on the importance of the decision. Critical
situations halt everything indefinitely.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.memory import MemoryManager

LOGGER = logging.getLogger("organism.escalation")

# Decision tiers: (wait_seconds, allowed_to_proceed)
TIERS = {
    "normal": (24 * 3600, True),          # 24h wait, then proceed with best judgement
    "minor": (7 * 24 * 3600, True),       # 7 days
    "major": (30 * 24 * 3600, True),      # 30 days; only if reversible
    "critical": (float("inf"), False),    # stop and wait indefinitely
}


class EscalationManager:
    def __init__(self, memory: MemoryManager) -> None:
        self.memory = memory

    def request(
        self,
        subject: str,
        tier: str,
        decision: str,
        communication_manager=None,
    ) -> None:
        """Record a pending request for founder attention."""
        if tier not in TIERS:
            tier = "normal"
        entry = (
            f"pending_request: {subject} | tier: {tier} | proposed: {decision} "
            f"| opened: {datetime.now(timezone.utc).isoformat()}"
        )
        self.memory.append("memory/core/decisions.md", entry)
        if communication_manager is not None:
            try:
                communication_manager.ask_founder(
                    f"Decision needed ({tier}): {subject}",
                    f"Proposed action: {decision}\n\nTier: {tier}. "
                    "Reply or comment APPROVED to proceed, or advise otherwise.",
                )
            except Exception as exc:
                LOGGER.warning("Could not notify founder of pending request: %s", exc)

    def check_pending(self, tier: str) -> list:
        """Return pending requests of a tier that have exceeded the wait."""
        wait, proceed = TIERS.get(tier, TIERS["normal"])
        if not proceed or wait == float("inf"):
            return []
        content = self.memory.read("memory/core/decisions.md")
        now = datetime.now(timezone.utc)
        matured = []
        for line in content.splitlines():
            if f"tier: {tier}" not in line:
                continue
            if "opened:" in line:
                try:
                    opened = datetime.fromisoformat(line.split("opened:", 1)[1].strip())
                    if (now - opened).total_seconds() >= wait:
                        matured.append(line)
                except ValueError:
                    continue
        return matured

    def proceed_if_matured(self, tier: str) -> bool:
        """True when a pending request of this tier may proceed."""
        matured = self.check_pending(tier)
        if matured:
            self.memory.record_decision(
                f"Proceeding after escalation timeout (tier {tier}): {matured[-1][:160]}"
            )
            return True
        return False

    def critical_halt(self, subject: str) -> None:
        """For critical situations: stop and wait indefinitely."""
        self.memory.record_decision(
            f"CRITICAL HALT requested: {subject}. All operations paused until "
            "the founder responds."
        )
        LOGGER.error("Critical halt: %s", subject)