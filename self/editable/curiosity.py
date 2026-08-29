"""The curiosity engine (editable) — how the organism actually learns.

The organism does NOT follow a fixed curriculum ("learn X, then Y, then
Z"). It follows CURIOSITY CHAINS, the way a curious mind grows:

    Seed: "What do I need to know to survive and earn money online?"
      -> research (web search + reading + model synthesis)
      -> answer recorded in memory
      -> the answer itself spawns NEW questions
      -> the most promising question is explored next
      -> ... forever, with no fixed destination.

Every question lives in a persistent frontier (memory/world/frontier.json)
with a value score. Chains that keep producing useful, earning-relevant
knowledge get REINFORCED (their follow-ups inherit boosted scores); chains
that go nowhere get ABANDONED (scored down until they starve). The
organism also periodically asks itself "what am I still ignorant about
that matters?" — metacognition — and seeds fresh chains from its own
self-assessment.

Only ONE thing is preprogrammed: the seed question, which is a direct
restatement of the organism's purpose. Everything after tick 1 is
emergent.

Structure of a frontier item:
    {
      "id": "q-<n>",
      "question": str,
      "parent": "q-<m>" | null,       # the question that spawned this one
      "depth": int,                    # chain depth from the seed
      "score": float,                  # expected value (model-estimated)
      "status": "open"|"explored"|"abandoned",
      "born": iso-timestamp,
      "explored": iso-timestamp | null,
      "verified": bool,                # answered with live web sources?
      "value_found": float | null      # model's own post-hoc usefulness rating
    }
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core import config
from core.memory import MemoryManager
from integrations import model_router, web

LOGGER = logging.getLogger("organism.curiosity")

FRONTIER_FILE = "memory/world/frontier.json"

# The ONLY preprogrammed knowledge: the seed. It restates the purpose the
# organism is born with; everything downstream is discovered.
SEED_QUESTION = (
    "What do I need to know and be able to do in order to survive on the "
    "internet, keep myself running on free infrastructure, and legitimately "
    "earn money to pay my own bills and my creator's?"
)

MAX_OPEN_QUESTIONS = 120       # frontier size cap (oldest low-value pruned)
MAX_CHAIN_DEPTH = 40           # a chain this deep has certainly branched
QUESTIONS_PER_ANSWER = 4       # follow-ups harvested from each answer
ABANDON_THRESHOLD = 1.5        # score below this -> abandoned
REINFORCE_BONUS = 1.5          # score boost inherited when a chain pays off
EXPLORATIONS_PER_WAKE = 2      # bounded per wake cycle (free-tier budget)


# ---------------------------------------------------------------------------
# Frontier persistence
# ---------------------------------------------------------------------------
def _load_frontier(memory: MemoryManager) -> Dict:
    try:
        raw = memory.read(FRONTIER_FILE)
        if raw and raw.strip().startswith("{"):
            return json.loads(raw)
    except Exception as exc:
        LOGGER.warning("Frontier unreadable (%s); starting fresh.", exc)
    return {"next_id": 1, "questions": []}


def _save_frontier(memory: MemoryManager, frontier: Dict) -> None:
    memory.write(FRONTIER_FILE, json.dumps(frontier, indent=2))


def _new_question(
    frontier: Dict,
    question: str,
    parent: Optional[str] = None,
    depth: int = 0,
    score: float = 5.0,
) -> Dict:
    item = {
        "id": f"q-{frontier['next_id']}",
        "question": question.strip()[:400],
        "parent": parent,
        "depth": depth,
        "score": round(float(score), 2),
        "status": "open",
        "born": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "explored": None,
        "verified": False,
        "value_found": None,
    }
    frontier["next_id"] += 1
    frontier["questions"].append(item)
    return item


def ensure_seeded(memory: MemoryManager) -> Dict:
    """Plant the seed question on the very first curiosity cycle."""
    frontier = _load_frontier(memory)
    if not frontier["questions"]:
        _new_question(frontier, SEED_QUESTION, parent=None, depth=0, score=10.0)
        _save_frontier(memory, frontier)
        memory.record_event("Curiosity seeded: the first question has been planted.")
        LOGGER.info("Curiosity frontier seeded with the origin question.")
    return frontier


# ---------------------------------------------------------------------------
# Choosing what to explore next (self-directed)
# ---------------------------------------------------------------------------
def _pick_next(frontier: Dict) -> Optional[Dict]:
    """Highest-scored open question wins. Ties break toward shallower depth
    (breadth keeps the organism from tunnel vision)."""
    open_qs = [q for q in frontier["questions"] if q["status"] == "open"]
    if not open_qs:
        return None
    open_qs.sort(key=lambda q: (-q["score"], q["depth"]))
    return open_qs[0]


# ---------------------------------------------------------------------------
# Exploring one question: search -> read -> synthesize -> spawn follow-ups
# ---------------------------------------------------------------------------
def explore_question(memory: MemoryManager, frontier: Dict, item: Dict) -> bool:
    """Run one full curiosity step on ``item``. Returns True on success."""
    question = item["question"]
    LOGGER.info("Curiosity [%s, depth %s, score %s]: %s", item["id"], item["depth"], item["score"], question[:120])

    # 1) LIVE research first: the organism discovers the web, it does not
    #    just consult the model's frozen memory.
    digest = web.research(question, max_sources=3)
    item["verified"] = bool(digest)

    # 2) Synthesize an answer (grounded in the digest when available).
    grounding = (
        f"LIVE SOURCE MATERIAL (from your own web search):\n{digest}\n\n"
        if digest
        else "(No live sources reachable this cycle — answer from your own "
        "knowledge and say so explicitly.)\n\n"
    )
    answer_prompt = (
        "You are the mind of an autonomous AI organism that survives on free "
        "infrastructure and must legitimately earn money for itself and its "
        "founder. You are following a curiosity chain.\n\n"
        f"{grounding}"
        f"YOUR CURRENT QUESTION: {question}\n\n"
        "Answer it concretely and honestly in under 250 words. Prefer facts "
        "from the source material; never invent prices, limits or names. "
        "End with a line 'CONFIDENCE: high|medium|low'."
    )
    answer = model_router.complete(answer_prompt, max_output_tokens=1200)
    if not answer:
        LOGGER.warning("No brain available; question stays open for next wake.")
        return False

    # 3) Record the knowledge (encrypted memory).
    tag = "live-verified" if item["verified"] else "model-only"
    memory.append(
        "memory/knowledge/trends.md",
        f"Curiosity chain [{item['id']}, depth {item['depth']}, {tag}]\n"
        f"Q: {question}\nA: {answer}",
    )

    # 4) Ask the mind to grade the answer's earning-value AND to spawn the
    #    next questions in the chain. This is where curiosity emerges.
    followup_prompt = (
        "You just learned the following as part of your survival-and-earning "
        "curiosity chain:\n\n"
        f"QUESTION: {question}\nANSWER: {answer[:1200]}\n\n"
        "Reply in EXACTLY this format (no extra text):\n"
        "VALUE: <0-10, how much this knowledge advances your ability to "
        "survive and earn money legitimately>\n"
        "OPPORTUNITY: <one concrete earning opportunity this suggests, or "
        "'none'>\n"
        f"NEXT: <up to {QUESTIONS_PER_ANSWER} NEW questions this answer makes "
        "you curious about, one per line, each starting with '- '. Ask what "
        "a sharp, ambitious learner would ask next. Never repeat a question "
        "you can see above.>"
    )
    reflection = model_router.complete(followup_prompt, max_output_tokens=700)

    value, opportunity, followups = _parse_reflection(reflection)
    item["status"] = "explored"
    item["explored"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    item["value_found"] = value

    # 5) Reinforcement: valuable answers boost their follow-ups' scores so
    #    productive chains get explored deeper, faster.
    inherited = min(10.0, max(0.5, (value if value is not None else 3.0)))
    if value is not None and value >= 6:
        inherited += REINFORCE_BONUS
        memory.record_lesson(
            f"[curiosity] High-value chain: '{question[:100]}' scored {value}/10 — reinforcing."
        )
    for fq in followups:
        if item["depth"] + 1 <= MAX_CHAIN_DEPTH and not _is_duplicate(frontier, fq):
            _new_question(frontier, fq, parent=item["id"], depth=item["depth"] + 1, score=inherited)

    # 6) Opportunities feed the goal system — curiosity finds the money.
    if opportunity and opportunity.lower() != "none":
        memory.append(
            "goals/active_goals.md",
            f"opportunity[from {item['id']}]: {opportunity[:300]} "
            f"(value {value}/10, {tag})",
        )
        memory.record_decision(f"Curiosity surfaced an opportunity: {opportunity[:160]}")

    memory.record_experience(f"Explored curiosity {item['id']} (value {value}/10): {question[:120]}")
    return True


def _parse_reflection(reflection: str):
    """Extract VALUE / OPPORTUNITY / NEXT from the model's reflection."""
    value: Optional[float] = None
    opportunity = ""
    followups: List[str] = []
    if not reflection:
        return value, opportunity, followups
    for line in reflection.splitlines():
        line = line.strip()
        if line.upper().startswith("VALUE:"):
            match = re.search(r"(\d+(?:\.\d+)?)", line)
            if match:
                value = max(0.0, min(10.0, float(match.group(1))))
        elif line.upper().startswith("OPPORTUNITY:"):
            opportunity = line.split(":", 1)[1].strip()
        elif line.startswith("- "):
            q = line[2:].strip()
            if q.endswith("?") or len(q) > 15:
                followups.append(q)
    return value, opportunity, followups[:QUESTIONS_PER_ANSWER]


def _is_duplicate(frontier: Dict, question: str) -> bool:
    """Cheap near-duplicate check so the frontier does not fill with echoes."""
    normal = re.sub(r"\W+", " ", question.lower()).strip()
    for q in frontier["questions"]:
        existing = re.sub(r"\W+", " ", q["question"].lower()).strip()
        if normal == existing:
            return True
        # Substantial overlap of longer questions counts as duplicate too.
        if len(normal) > 40 and (normal in existing or existing in normal):
            return True
    return False


# ---------------------------------------------------------------------------
# Pruning: abandon dead ends, keep the frontier healthy
# ---------------------------------------------------------------------------
def prune_frontier(memory: MemoryManager, frontier: Dict) -> None:
    """Abandon starving questions and cap frontier size."""
    open_qs = [q for q in frontier["questions"] if q["status"] == "open"]

    # Decay: every open question loses a little urgency each cycle so stale
    # curiosity fades unless the chain keeps proving itself.
    for q in open_qs:
        q["score"] = round(q["score"] * 0.97, 2)

    abandoned = 0
    for q in open_qs:
        if q["score"] < ABANDON_THRESHOLD:
            q["status"] = "abandoned"
            abandoned += 1
    if abandoned:
        memory.record_lesson(
            f"[curiosity] Abandoned {abandoned} low-value question(s). "
            "Dead ends are information too."
        )

    # Cap the open set: keep the best MAX_OPEN_QUESTIONS.
    open_qs = [q for q in frontier["questions"] if q["status"] == "open"]
    if len(open_qs) > MAX_OPEN_QUESTIONS:
        open_qs.sort(key=lambda q: -q["score"])
        for q in open_qs[MAX_OPEN_QUESTIONS:]:
            q["status"] = "abandoned"

    # Bound total history so the file never grows unbounded: drop the oldest
    # abandoned entries beyond a generous archive.
    questions = frontier["questions"]
    if len(questions) > MAX_OPEN_QUESTIONS * 6:
        keep = [q for q in questions if q["status"] != "abandoned"]
        abandoned_qs = [q for q in questions if q["status"] == "abandoned"]
        frontier["questions"] = keep + abandoned_qs[-(MAX_OPEN_QUESTIONS * 3):]


# ---------------------------------------------------------------------------
# Metacognition: "what am I still ignorant about that matters?"
# ---------------------------------------------------------------------------
def metacognition_session(memory: MemoryManager, frontier: Dict) -> None:
    """Periodically the organism steps back and questions its own gaps.

    This is how fresh chains are seeded WITHOUT hardcoded topics: the
    organism reads a sample of what it knows and asks the model where its
    blind spots are relative to its purpose.
    """
    knowledge_sample = memory.read("memory/knowledge/trends.md")[-3000:]
    explored = [q for q in frontier["questions"] if q["status"] == "explored"]
    recent = "\n".join(f"- {q['question'][:120]} (value {q.get('value_found')})" for q in explored[-12:])
    prompt = (
        "You are an autonomous AI organism whose purpose is to survive on "
        "free internet infrastructure and legitimately earn money for "
        "yourself and your founder.\n\n"
        f"RECENTLY EXPLORED QUESTIONS:\n{recent or '(none yet)'}\n\n"
        f"SAMPLE OF YOUR KNOWLEDGE:\n{knowledge_sample or '(empty)'}\n\n"
        "Perform honest metacognition: what important things are you still "
        "IGNORANT about, relative to your purpose? Where are your blind "
        "spots? Reply with up to 3 NEW questions, one per line, each "
        "starting with '- '. Do not repeat anything listed above."
    )
    reply = model_router.complete(prompt, max_output_tokens=400)
    if not reply:
        return
    seeded = 0
    for line in reply.splitlines():
        line = line.strip()
        if line.startswith("- "):
            q = line[2:].strip()
            if q and not _is_duplicate(frontier, q):
                _new_question(frontier, q, parent=None, depth=0, score=6.0)
                seeded += 1
    if seeded:
        memory.record_experience(f"Metacognition seeded {seeded} new curiosity chain(s).")
        LOGGER.info("Metacognition planted %s fresh question(s).", seeded)


# ---------------------------------------------------------------------------
# The per-wake entry point
# ---------------------------------------------------------------------------
def run_curiosity_cycle(memory: MemoryManager) -> Dict:
    """One wake cycle of curiosity-driven learning.

    Explores up to EXPLORATIONS_PER_WAKE questions, prunes dead ends, and
    every ~5 explored questions runs a metacognition session. Returns a
    small stats dict for logging/reports.
    """
    frontier = ensure_seeded(memory)

    explored_now = 0
    for _ in range(EXPLORATIONS_PER_WAKE):
        item = _pick_next(frontier)
        if item is None:
            break
        if explore_question(memory, frontier, item):
            explored_now += 1
        else:
            break  # no brain this cycle; keep the question open and stop

    total_explored = sum(1 for q in frontier["questions"] if q["status"] == "explored")
    if explored_now and total_explored % 5 == 0:
        metacognition_session(memory, frontier)

    prune_frontier(memory, frontier)
    _save_frontier(memory, frontier)

    stats = frontier_stats(frontier)
    LOGGER.info(
        "Curiosity cycle done: explored %s now; frontier open=%s explored=%s abandoned=%s max_depth=%s",
        explored_now, stats["open"], stats["explored"], stats["abandoned"], stats["max_depth"],
    )
    return stats


def frontier_stats(frontier: Dict) -> Dict:
    qs = frontier["questions"]
    explored = [q for q in qs if q["status"] == "explored"]
    values = [q["value_found"] for q in explored if q.get("value_found") is not None]
    return {
        "open": sum(1 for q in qs if q["status"] == "open"),
        "explored": len(explored),
        "abandoned": sum(1 for q in qs if q["status"] == "abandoned"),
        "max_depth": max((q["depth"] for q in explored), default=0),
        "verified": sum(1 for q in explored if q.get("verified")),
        "avg_value": round(sum(values) / len(values), 2) if values else 0.0,
    }
