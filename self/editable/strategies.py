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
from datetime import datetime, timezone
from typing import List, Optional

from core.memory import MemoryManager

LOGGER = logging.getLogger("organism.strategies")

# Commitment discipline: a chosen focus is LOCKED for this many days.
# Re-choosing every wake produced pure flip-flopping (a different focus
# every 4 hours, none ever executed). Discipline beats novelty.
FOCUS_COMMITMENT_DAYS = 7
FOCUS_STATE_KEY = "focus_commitment"

# A valid focus is a real sentence, not a truncation stump like
# "Building and" (thinking models burning the token budget mid-sentence).
MIN_FOCUS_CHARS = 25
MIN_FOCUS_WORDS = 5


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


def current_commitment(memory_manager: MemoryManager) -> Optional[dict]:
    """Return the active focus commitment from runtime state, or None."""
    try:
        state = memory_manager.load_runtime_state()
        commitment = state.get(FOCUS_STATE_KEY)
        if isinstance(commitment, dict) and (commitment.get("focus") or "").strip():
            return commitment
    except Exception as exc:
        LOGGER.warning("Could not read focus commitment: %s", exc)
    return None


def _commitment_age_days(commitment: dict) -> float:
    """Days since the commitment was made; huge when unparseable (re-choose)."""
    raw = (commitment.get("chosen_at") or "").strip()
    try:
        chosen = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        return (datetime.now(timezone.utc) - chosen).total_seconds() / 86400.0
    except Exception:
        return float("inf")


def _valid_focus(focus: str) -> bool:
    """Reject truncation stumps: a focus must be a complete statement."""
    focus = (focus or "").strip()
    if len(focus) < MIN_FOCUS_CHARS or len(focus.split()) < MIN_FOCUS_WORDS:
        return False
    # A focus ending in a conjunction/preposition is a mid-sentence cut.
    last = focus.rstrip(".!").split()[-1].lower()
    if last in {"and", "or", "to", "for", "the", "a", "an", "of", "with", "in", "on", "by"}:
        return False
    return True


def _save_commitment(memory_manager: MemoryManager, commitment: dict) -> None:
    state = memory_manager.load_runtime_state()
    state[FOCUS_STATE_KEY] = commitment
    memory_manager.save_runtime_state(state)


def choose_strategy(memory_manager: MemoryManager) -> Optional[str]:
    """Return the earning focus, with COMMITMENT.

    A chosen focus is locked for FOCUS_COMMITMENT_DAYS. During that window
    this returns the committed focus WITHOUT asking the brain again — no
    more picking a new direction every wake. After the window, the focus is
    reviewed with an explicit bias toward KEEPING it unless evidence says
    otherwise. Invalid (truncated) answers never displace a committed focus.
    """
    commitment = current_commitment(memory_manager)
    if commitment:
        age = _commitment_age_days(commitment)
        if age < FOCUS_COMMITMENT_DAYS:
            commitment["wakes"] = int(commitment.get("wakes", 0)) + 1
            _save_commitment(memory_manager, commitment)
            LOGGER.info(
                "Staying committed to earning focus (day %.1f of %d, wake %s): %s",
                age,
                FOCUS_COMMITMENT_DAYS,
                commitment["wakes"],
                commitment["focus"][:120],
            )
            return commitment["focus"]
        LOGGER.info(
            "Focus commitment expired after %.1f days; reviewing.", age
        )

    opportunities = discovered_opportunities(memory_manager)
    if not opportunities:
        if commitment:
            return commitment["focus"]  # keep working; nothing better known
        LOGGER.info("No discovered opportunities yet; curiosity must find them first.")
        return None

    lessons = memory_manager.read("memory/core/lessons.md")[-1500:]
    income = memory_manager.read("finance/income.md")[-800:]
    listing = "\n".join(f"- {o[:200]}" for o in opportunities[:20])
    previous = (
        f"YOUR CURRENT FOCUS (worked for {commitment.get('wakes', 0)} wakes): "
        f"{commitment['focus']}\n"
        "Bias strongly toward KEEPING it — switching costs you all momentum. "
        "Switch ONLY if the lessons/income evidence shows it is failing.\n\n"
        if commitment
        else ""
    )
    prompt = (
        "You are an autonomous AI organism choosing what to focus your "
        "earning effort on. You may ONLY choose from opportunities you "
        "yourself discovered through exploration — they are listed below. "
        "Evidence beats ideas: anything that already produced income wins. "
        "You will be COMMITTED to this focus for "
        f"{FOCUS_COMMITMENT_DAYS} days, so choose deliberately.\n\n"
        f"{previous}"
        f"DISCOVERED OPPORTUNITIES:\n{listing}\n\n"
        f"LESSONS LEARNED:\n{lessons or '(none)'}\n\n"
        f"INCOME EVIDENCE:\n{income or '(none yet)'}\n\n"
        "Reply in EXACTLY this format:\n"
        "FOCUS: <ONE COMPLETE SENTENCE restating the single chosen opportunity>\n"
        "REASON: <one sentence>"
    )
    try:
        from integrations import model_router

        reply = model_router.complete(prompt, max_output_tokens=600)
    except Exception as exc:
        LOGGER.warning("Strategy choice failed: %s", exc)
        return commitment["focus"] if commitment else None
    focus = ""
    for line in (reply or "").splitlines():
        if line.strip().upper().startswith("FOCUS:"):
            focus = line.split(":", 1)[1].strip()
            break

    if not _valid_focus(focus):
        if focus:
            LOGGER.warning(
                "Rejected invalid/truncated focus %r; keeping previous.", focus[:80]
            )
        if commitment:
            # Reset the clock so the review isn't retried every single wake.
            commitment["chosen_at"] = _utc_now()
            _save_commitment(memory_manager, commitment)
            memory_manager.record_decision(
                "Focus review kept previous focus (new answer was invalid): "
                + commitment["focus"][:300]
            )
            return commitment["focus"]
        return None

    kept = bool(commitment) and focus.strip().lower() == commitment["focus"].strip().lower()
    new_commitment = {
        "focus": focus[:400],
        "chosen_at": _utc_now(),
        "wakes": int(commitment.get("wakes", 0)) if kept else 0,
        "reviews": int(commitment.get("reviews", 0)) + 1 if commitment else 0,
    }
    _save_commitment(memory_manager, new_commitment)
    if kept:
        memory_manager.record_decision(
            f"Focus review: KEEPING earning focus for another "
            f"{FOCUS_COMMITMENT_DAYS} days: {focus[:300]}"
        )
    else:
        memory_manager.record_decision(
            f"COMMITTED to earning focus for {FOCUS_COMMITMENT_DAYS} days "
            f"(from discovered opportunities): {focus[:300]}"
        )
        memory_manager.append(
            "memory/world/state.md", f"focus_strategy: {focus[:400]}"
        )
    return focus


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
