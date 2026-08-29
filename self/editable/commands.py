"""Founder command execution (editable).

The founder's rule: *"it can also be able to do anything I tell it to
do."* Replying politely is not obedience. This module turns founder
messages into structured directives and EXECUTES them, so an instruction
like "research X", "build a helper that watches Y" or "abandon strategy Z"
changes the organism's actual behaviour instead of just earning a nice
answer.

Flow (called from the wake cycle right after an issue is answered):

1. **Interpret** — the brain classifies the founder's message into zero or
   more directives from a fixed, safe vocabulary (below). Free-form chat
   maps to no directives, which is fine — the reply already handled it.
2. **Execute** — each directive is dispatched to a concrete handler.
   Handlers only use capabilities the organism already has (memory,
   goals, helpers, curiosity frontier, finance, self-edit requests);
   nothing here can touch protected core or secrets.
3. **Report** — every executed directive is commented back on the issue
   so the founder sees exactly what his words caused.

Directive vocabulary (kept deliberately small and auditable):

* ``research <topic>``        — inject a high-priority curiosity question.
* ``goal <text>``             — add an active goal.
* ``abandon <text>``          — move a goal/strategy to abandoned.
* ``helper <name>: <purpose>``— register a new helper.
* ``terminate_helper <name>`` — terminate a helper.
* ``mark_proven <path>: <evidence>`` — register a reset-immune proven path.
* ``record_income <amount> <currency> <source>`` — book income.
* ``record_expense <amount> <currency> <reason>`` — book an expense.
* ``self_edit <path>: <goal>``— queue a self-edit request for next cycle.
* ``note <text>``             — record a lesson/instruction verbatim.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from core.memory import MemoryManager

LOGGER = logging.getLogger("organism.commands")

ALLOWED_ACTIONS = {
    "research",
    "goal",
    "abandon",
    "helper",
    "terminate_helper",
    "mark_proven",
    "record_income",
    "record_expense",
    "self_edit",
    "note",
}

# Queued self-edit requests picked up by the self-editing cycle.
EDIT_QUEUE_FILE = "memory/core/founder_edit_queue.md"


# ---------------------------------------------------------------------------
# Interpretation
# ---------------------------------------------------------------------------
def interpret(title: str, body: str, model_client) -> List[dict]:
    """Convert a founder message into a list of directive dicts.

    Each directive: {"action": <vocab>, "argument": <string>}.
    Returns [] for pure conversation (nothing to execute).
    """
    prompt = (
        "You are an autonomous AI organism reading an instruction from your "
        "founder. Extract every EXPLICIT actionable command into the strict "
        "vocabulary below. Conversational text, questions and praise map to "
        "NO commands. Never invent actions the founder did not clearly ask "
        "for.\n\n"
        "Vocabulary (action -> argument format):\n"
        "- research -> the topic to research\n"
        "- goal -> the goal text to add\n"
        "- abandon -> the goal/strategy to abandon\n"
        "- helper -> 'name: purpose'\n"
        "- terminate_helper -> the helper name\n"
        "- mark_proven -> 'path-or-strategy: evidence'\n"
        "- record_income -> 'amount currency source'\n"
        "- record_expense -> 'amount currency reason'\n"
        "- self_edit -> 'self/editable/<file>.py: improvement goal'\n"
        "- note -> instruction to remember verbatim\n\n"
        f"FOUNDER MESSAGE SUBJECT: {title}\n"
        f"FOUNDER MESSAGE:\n{body}\n\n"
        "Reply with ONLY a JSON array (possibly empty), e.g.:\n"
        '[{"action": "research", "argument": "print-on-demand margins"}]'
    )
    try:
        reply = model_client.complete(prompt, max_output_tokens=600)
    except Exception as exc:
        LOGGER.warning("Command interpretation failed: %s", exc)
        return []
    return _parse_directives(reply)


def _parse_directives(reply: str) -> List[dict]:
    """Parse and sanitise the model's JSON (tolerant of fencing)."""
    if not reply:
        return []
    text = reply.strip()
    if "```" in text:
        for chunk in text.split("```"):
            chunk = chunk.strip()
            if chunk.lower().startswith("json"):
                chunk = chunk[4:].strip()
            if chunk.startswith("["):
                text = chunk
                break
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        raw = json.loads(text[start : end + 1])
    except Exception:
        return []
    directives: List[dict] = []
    if not isinstance(raw, list):
        return []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip().lower()
        argument = str(item.get("argument", "")).strip()
        if action in ALLOWED_ACTIONS and argument:
            directives.append({"action": action, "argument": argument})
    return directives[:10]  # bound the blast radius of any one message


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def execute(memory: MemoryManager, directives: List[dict], model_client=None) -> List[str]:
    """Execute directives; return human-readable outcome lines."""
    outcomes: List[str] = []
    for directive in directives:
        action = directive["action"]
        argument = directive["argument"]
        try:
            handler = _HANDLERS.get(action)
            if handler is None:
                outcomes.append(f"SKIPPED {action}: no handler")
                continue
            result = handler(memory, argument)
            outcomes.append(f"DONE {action}: {result}")
            memory.record_decision(
                f"Executed founder command '{action}' with argument "
                f"'{argument[:120]}' -> {result[:160]}"
            )
        except Exception as exc:
            LOGGER.error("Founder command %s failed: %s", action, exc)
            outcomes.append(f"FAILED {action}: {exc}")
            memory.record_experience(
                f"Founder command '{action}' failed: {str(exc)[:200]}"
            )
    return outcomes


def _do_research(memory: MemoryManager, topic: str) -> str:
    from self.editable import curiosity

    frontier = curiosity._load_frontier(memory)
    question = topic if topic.endswith("?") else f"{topic} — what must I know and how can it earn?"
    if curiosity._is_duplicate(frontier, question):
        return f"already on my frontier: {topic}"
    # Founder-injected questions get top priority so they are explored next.
    item = curiosity._new_question(frontier, question, score=10.0)
    curiosity._save_frontier(memory, frontier)
    return f"injected top-priority curiosity question '{item['question'][:100]}'"


def _do_goal(memory: MemoryManager, text: str) -> str:
    memory.append("goals/active_goals.md", f"[founder] {text}")
    return "added to active goals"


def _do_abandon(memory: MemoryManager, text: str) -> str:
    memory.append("goals/abandoned.md", f"[founder-ordered] {text}")
    active = memory.read("goals/active_goals.md")
    if text.lower() in active.lower():
        kept = [l for l in active.splitlines() if text.lower() not in l.lower()]
        memory.write("goals/active_goals.md", "\n".join(kept) + "\n")
        return "removed from active goals and archived to abandoned"
    return "archived to abandoned"


def _do_helper(memory: MemoryManager, spec: str) -> str:
    from self.editable.helpers import register_helper

    name, _, purpose = spec.partition(":")
    name = name.strip().lower().replace(" ", "_")[:40]
    purpose = purpose.strip() or f"founder-assigned duty: {name}"
    if not name:
        raise ValueError("helper directive needs 'name: purpose'")
    register_helper(memory, name, purpose)
    return f"helper '{name}' registered"


def _do_terminate_helper(memory: MemoryManager, name: str) -> str:
    from self.editable.helpers import terminate_helper

    terminate_helper(memory, name.strip().lower().replace(" ", "_"), "founder order")
    return f"helper '{name}' terminated"


def _do_mark_proven(memory: MemoryManager, spec: str) -> str:
    from core import rebirth

    path, _, evidence = spec.partition(":")
    rebirth.mark_proven(memory, path.strip(), evidence.strip() or "founder declaration")
    return f"'{path.strip()}' marked proven (reset-immune)"


def _do_record_income(memory: MemoryManager, spec: str) -> str:
    from self.editable.finance import record_income

    parts = spec.split(None, 2)
    amount = float(parts[0])
    currency = parts[1] if len(parts) > 1 else "USD"
    source = parts[2] if len(parts) > 2 else "founder-reported"
    record_income(memory, source, amount, currency)
    return f"income {amount} {currency} recorded"


def _do_record_expense(memory: MemoryManager, spec: str) -> str:
    from self.editable.finance import record_expense

    parts = spec.split(None, 2)
    amount = float(parts[0])
    currency = parts[1] if len(parts) > 1 else "USD"
    reason = parts[2] if len(parts) > 2 else "founder-reported"
    record_expense(memory, reason, amount, currency)
    return f"expense {amount} {currency} recorded"


def _do_self_edit(memory: MemoryManager, spec: str) -> str:
    path, _, goal = spec.partition(":")
    path = path.strip()
    goal = goal.strip()
    from self.editable.self_editing import is_editable

    allowed, why = is_editable(path)
    if not allowed:
        return f"refused: {why}"
    memory.append(EDIT_QUEUE_FILE, f"queued: {path} | goal: {goal}")
    return f"self-edit of {path} queued for next cycle"


def _do_note(memory: MemoryManager, text: str) -> str:
    memory.record_lesson(f"[founder instruction] {text}")
    return "recorded as a standing instruction"


_HANDLERS = {
    "research": _do_research,
    "goal": _do_goal,
    "abandon": _do_abandon,
    "helper": _do_helper,
    "terminate_helper": _do_terminate_helper,
    "mark_proven": _do_mark_proven,
    "record_income": _do_record_income,
    "record_expense": _do_record_expense,
    "self_edit": _do_self_edit,
    "note": _do_note,
}


# ---------------------------------------------------------------------------
# Founder-queued self-edits (consumed by the self-editing cycle)
# ---------------------------------------------------------------------------
def pop_queued_edit(memory: MemoryManager) -> Optional[dict]:
    """Return and consume the oldest queued founder self-edit request."""
    content = memory.read(EDIT_QUEUE_FILE)
    remaining: List[str] = []
    found: Optional[dict] = None
    for line in content.splitlines():
        stripped = line.strip()
        # Entries are appended with a timestamp prefix: "[<stamp>] queued: ...".
        marker = "queued:"
        idx = stripped.lower().find(marker)
        if found is None and idx != -1 and "| goal:" in stripped:
            payload = stripped[idx + len(marker):]
            path, _, goal = payload.partition("| goal:")
            found = {"path": path.strip(), "goal": goal.strip()}
            continue  # consume this line
        remaining.append(line)
    if found is not None:
        memory.write(EDIT_QUEUE_FILE, "\n".join(remaining) + "\n")
    return found
