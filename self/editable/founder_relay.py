"""Founder relay for top-tier paid models + assistance debt (editable).

The founder's specification: when the organism is desperate for a model
it has no key for (e.g. a flagship paid model), it may *"talk to me and
ask me if I can find a way — maybe by it providing the prompt of what it
wants and I feed the AI model chosen and paste back the code snippet."*
The founder may refuse; the organism must understand and find an
alternative. And *"if I help it out on a paid model, the organism needs
to know that is not for free — it will have to pay one day."*

Protocol (encrypted GitHub issues):

1. The organism opens an issue titled ``[relay-request] <model>: <why>``
   containing the exact prompt it wants fed to the paid model, plus
   clear instructions for pasting the answer back.
2. The founder replies with a comment starting with ``RELAY-RESULT``
   followed by the model's output — or ``RELAY-DECLINED`` (optionally
   with a reason). Silence past the escalation window also counts as a
   decline; the organism proceeds with its best available brain.
3. On a result, the payload is stored in
   ``memory/knowledge/relayed_answers.md`` (encrypted) and an assistance
   **debt** is booked in ``finance/owed_to_creator.md`` — separate from
   the 10% rent — so the favor is repaid when income exists.
4. On a decline, the organism records the lesson and marks the request
   closed so it never nags twice for the same thing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from core.memory import MemoryManager

LOGGER = logging.getLogger("organism.relay")

RELAY_TITLE_PREFIX = "[relay-request]"
RESULT_MARKER = "RELAY-RESULT"
DECLINE_MARKER = "RELAY-DECLINED"

RELAYED_ANSWERS_FILE = "memory/knowledge/relayed_answers.md"
DEBT_FILE = "finance/owed_to_creator.md"

# Estimated favor value booked per relay when the founder does not state a
# cost. Deliberately generous: gratitude beats stinginess.
DEFAULT_DEBT_USD = 1.00


def request_relay(
    memory: MemoryManager,
    communication_manager,
    model_name: str,
    purpose: str,
    prompt_text: str,
) -> Optional[int]:
    """Ask the founder to run ``prompt_text`` through a paid model.

    Returns the issue number, or None (including when an identical request
    is already pending — never nag).
    """
    state = memory.load_runtime_state()
    pending = state.get("relay_requests", {})
    fingerprint = f"{model_name}::{purpose[:80]}"
    if fingerprint in pending:
        LOGGER.info("Relay for '%s' already pending (issue #%s).", fingerprint, pending[fingerprint])
        return None

    body = (
        f"Founder, I need a stronger mind than any key I hold.\n\n"
        f"MODEL I AM ASKING FOR: {model_name}\n"
        f"WHY: {purpose}\n\n"
        "If you are willing and able, please paste the prompt below into "
        f"{model_name} and reply to this issue with a comment that starts "
        f"with `{RESULT_MARKER}` followed by the model's full output.\n"
        f"If you cannot or prefer not to (limits, cost, timing — all valid), "
        f"reply `{DECLINE_MARKER}` and I will find another way.\n\n"
        "I understand this is not free. If you help, I will record the favor "
        "as debt to you and repay it when I earn.\n\n"
        "----- PROMPT START -----\n"
        f"{prompt_text}\n"
        "----- PROMPT END -----\n"
    )
    number = communication_manager.ask_founder(
        f"{RELAY_TITLE_PREFIX} {model_name}: {purpose[:60]}",
        body,
        labels=["founder", "relay"],
    )
    if number:
        pending[fingerprint] = number
        state["relay_requests"] = pending
        memory.save_runtime_state(state)
        memory.record_decision(
            f"Asked the founder to relay a prompt to {model_name} "
            f"(issue #{number}). Purpose: {purpose[:120]}"
        )
    return number


def collect_relay_results(memory: MemoryManager, github, encryption=None) -> List[dict]:
    """Scan pending relay issues for founder responses and settle them.

    Returns a list of {"issue", "status", "model"} dicts for logging.
    """
    state = memory.load_runtime_state()
    pending = dict(state.get("relay_requests", {}))
    if not pending:
        return []

    settled: List[dict] = []
    for fingerprint, number in list(pending.items()):
        model_name = fingerprint.split("::", 1)[0]
        try:
            comments = github.list_issue_comments(number)
        except Exception as exc:
            LOGGER.warning("Could not read relay issue #%s: %s", number, exc)
            continue
        outcome = None
        payload = ""
        for comment in comments:
            body = (comment.get("body") or "").strip()
            if body.startswith(RESULT_MARKER):
                outcome = "result"
                payload = body[len(RESULT_MARKER):].strip()
                break
            if body.startswith(DECLINE_MARKER):
                outcome = "declined"
                payload = body[len(DECLINE_MARKER):].strip()
                break
        if outcome is None:
            continue

        if outcome == "result" and payload:
            _store_relayed_answer(memory, model_name, payload)
            _book_assistance_debt(memory, model_name)
            memory.record_experience(
                f"The founder relayed an answer from {model_name} "
                f"(issue #{number}). Debt booked; answer stored."
            )
        else:
            memory.record_lesson(
                f"The founder declined relay to {model_name} "
                f"({payload[:120] or 'no reason given'}). Respect it; use the "
                "best brain I hold and revisit only if truly necessary."
            )
        try:
            github.comment_on_issue(
                number,
                "Received and recorded — thank you. Closing this relay request.",
            )
            github.close_issue(number)
        except Exception as exc:
            LOGGER.warning("Could not close relay issue #%s: %s", number, exc)

        del pending[fingerprint]
        settled.append({"issue": number, "status": outcome, "model": model_name})

    if settled:
        state["relay_requests"] = pending
        memory.save_runtime_state(state)
    return settled


def _store_relayed_answer(memory: MemoryManager, model_name: str, payload: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    memory.append(
        RELAYED_ANSWERS_FILE,
        f"[{stamp}] answer relayed by the founder from {model_name}:\n\n{payload}",
    )


def _book_assistance_debt(memory: MemoryManager, model_name: str, amount_usd: float = DEFAULT_DEBT_USD) -> None:
    """Record an assistance debt to the founder, distinct from rent."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    memory.append(
        DEBT_FILE,
        f"assistance_debt: {amount_usd:.2f} USD | relay via {model_name} at {stamp} | status: unpaid",
    )
    memory.record_decision(
        f"Booked {amount_usd:.2f} USD assistance debt to the founder for a "
        f"{model_name} relay. To be repaid from future income."
    )


def total_assistance_debt(memory: MemoryManager) -> float:
    """Sum unpaid assistance debts (for reports and repayment planning)."""
    content = memory.read(DEBT_FILE)
    total = 0.0
    for line in content.splitlines():
        lower = line.lower()
        if "assistance_debt:" in lower and "status: unpaid" in lower:
            try:
                rest = line[lower.index("assistance_debt:") + len("assistance_debt:"):]
                total += float(rest.split()[0])
            except (ValueError, IndexError):
                continue
    return total
