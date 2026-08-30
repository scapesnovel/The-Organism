"""Emergent strategy selection (editable).

NOTHING here is preprogrammed knowledge. The founder's rule is absolute:
the organism starts from zero and learns by curiosity. Strategies are
therefore NOT a hardcoded menu — they are read from what the organism has
actually discovered:

* ``goals/active_goals.md`` — opportunities the curiosity engine graded
  as valuable (every explored answer is scored for earning value and
  concrete opportunities flow into the goals file).
* ``memory/core/lessons.md`` — what worked, what failed.
* ``finance/income.md`` — evidence: a strategy that has produced income
  outranks any untested idea.

``choose_strategy`` asks the brain to pick ONE focus from those
discovered opportunities. When nothing has been discovered yet it
returns None — the correct answer for an organism that has not learned
enough, never a hardcoded fallback.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from core.memory import MemoryManager

LOGGER = logging.getLogger("organism.strategies")


def discovered_opportunities(memory_manager: MemoryManager) -> List[str]:
    """Return opportunity lines the curiosity engine has fed into goals."""
    content = memory_manager.read("goals/active_goals.md")
    lines: List[str] = []
    for line in content.splitlines():
        stripped = line.strip().lstrip("-• ").strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("("):
            continue
        # Skip timestamps-only lines and file headers.
        if stripped.startswith("[") and stripped.endswith("]"):
            continue
        lines.append(stripped)
    return lines


def list_strategies(memory_manager: MemoryManager) -> List[dict]:
    """Return discovered opportunities in the legacy dict shape.

    Kept for backward compatibility with older callers; the data now comes
    entirely from the organism's own discoveries.
    """
    return [
        {"id": f"discovered_{i}", "name": text, "source": "curiosity"}
        for i, text in enumerate(discovered_opportunities(memory_manager))
    ]


def choose_strategy(memory_manager: MemoryManager) -> Optional[str]:
    """Pick the earning focus from DISCOVERED opportunities only.

    Asks the brain to weigh the organism's own goals, lessons and income
    evidence. Returns a short focus description, or None when the organism
    has not yet discovered anything worth focusing on (keep learning).
    """
    opportunities = discovered_opportunities(memory_manager)
    if not opportunities:
        LOGGER.info("No discovered opportunities yet; curiosity must find them first.")
        return None

    lessons = memory_manager.read("memory/core/lessons.md")[-1500:]
    income = memory_manager.read("finance/income.md")[-800:]
    listing = "\n".join(f"- {o[:200]}" for o in opportunities[:20])
    prompt = (
        "You are an autonomous AI organism choosing what to focus your "
        "earning effort on. You may ONLY choose from opportunities you "
        "yourself discovered through exploration — they are listed below. "
        "Evidence beats ideas: anything that already produced income wins.\n\n"
        f"DISCOVERED OPPORTUNITIES:\n{listing}\n\n"
        f"LESSONS LEARNED:\n{lessons or '(none)'}\n\n"
        f"INCOME EVIDENCE:\n{income or '(none yet)'}\n\n"
        "Reply in EXACTLY this format:\n"
        "FOCUS: <one short line restating the single chosen opportunity>\n"
        "REASON: <one sentence>"
    )
    try:
        from integrations import model_router

        reply = model_router.complete(prompt, max_output_tokens=200)
    except Exception as exc:
        LOGGER.warning("Strategy choice failed: %s", exc)
        return None
    if not reply:
        return None
    focus = ""
    for line in reply.splitlines():
        if line.strip().upper().startswith("FOCUS:"):
            focus = line.split(":", 1)[1].strip()
            break
    if focus:
        memory_manager.record_decision(f"Chose earning focus (from discovered opportunities): {focus[:200]}")
    return focus or None
