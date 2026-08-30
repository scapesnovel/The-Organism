"""Self-testing and optional focused study (editable).

No curriculum lives here. The founder's rule: the organism starts from
zero knowledge and learns only through its own curiosity chains
(``self/editable/curiosity.py``). The old hardcoded STUDY_PLAN violated
that rule and has been removed.

What remains:

* ``run_self_test`` — capability introspection (can I fetch? parse?
  reach a brain? is my memory encrypted?). Testing one's own body is not
  preprogrammed knowledge; it is the safety floor for stage advancement.
* ``run_study_session`` — an optional tool the organism may invoke
  DELIBERATELY on a subject it has already discovered through curiosity
  (the subject comes from its own frontier, never from a list).
"""

from __future__ import annotations

import logging
from typing import Optional

from core.memory import MemoryManager

LOGGER = logging.getLogger("organism.learning")

LESSONS_FROM_STUDY = 2  # number of lessons to extract per session


def run_study_session(memory_manager: MemoryManager, subject: Optional[str] = None) -> None:
    """Run one focused study session on a subject the organism chose itself.

    ``subject`` must come from the organism's own curiosity frontier or
    memory — when omitted, the highest-value open question on the frontier
    is used. With an empty frontier there is nothing to study (correct for
    a mind that has not yet found a question worth the time).
    """
    if subject is None:
        try:
            from self.editable import curiosity

            frontier = curiosity._load_frontier(memory_manager)
            item = curiosity._pick_next(frontier)
            subject = item["question"] if item else None
        except Exception as exc:
            LOGGER.warning("Could not consult the curiosity frontier: %s", exc)
            subject = None
    if not subject:
        LOGGER.info("No self-chosen subject available; nothing to study.")
        return

    LOGGER.info("Study session on (self-chosen): %s", subject)
    prompt = (
        "You are teaching an autonomous AI entity that must run on free tiers "
        "forever. Create a focused study note on this subject IT chose:\n\n"
        f"SUBJECT: {subject}\n\n"
        "Structure the note as:\n"
        "- Key facts (bulleted, concrete)\n"
        "- Free tools/resources mentioned (with names)\n"
        "- Pitfalls and rate limits\n"
        "- One actionable idea the learner can execute today\n"
        "Keep it under 260 words. Be accurate; do not invent prices or limits "
        "— say 'varies' when unsure."
    )
    try:
        from integrations import model_router

        note = model_router.complete(prompt, max_output_tokens=1000)
    except Exception as exc:
        LOGGER.warning("Study session failed: %s", exc)
        return

    if not note:
        return

    memory_manager.append("memory/knowledge/platforms.md", f"Study note on: {subject}\n\n{note}")
    memory_manager.record_experience(f"Studied (self-chosen): {subject}")

    lessons = [line.strip("- ").strip() for line in note.splitlines() if line.strip().startswith("-")][:LESSONS_FROM_STUDY]
    for lesson in lessons:
        if lesson:
            memory_manager.record_lesson(f"[study:{subject[:80]}] {lesson}")


def run_self_test(memory_manager: MemoryManager) -> dict:
    """Basic self-test on internet operations (stage transition gate).

    Returns a dict of {test: passed_bool}.
    """
    results: dict = {}
    try:
        from integrations import web

        page = web.fetch("https://example.com")
        results["http_get"] = bool(page and "Example Domain" in page)
    except Exception:
        results["http_get"] = False

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<html><body><p>ok</p></body></html>", "html.parser")
        results["html_parse"] = bool(soup.find("p").get_text() == "ok")
    except Exception:
        results["html_parse"] = False

    try:
        from integrations import model_router

        results["api_call"] = "ok" in model_router.complete("Reply with exactly: OK", max_output_tokens=8).lower()
    except Exception:
        results["api_call"] = False

    results["encryption"] = memory_manager.plaintext_fallback is False
    memory_manager.record_experience(f"Self-test results: {results}")
    return results
