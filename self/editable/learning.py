"""Self-study and skill acquisition (editable).

NOTE: the curated STUDY_PLAN below is LEGACY. The organism's primary
learning is now the curiosity engine (self/editable/curiosity.py), which
follows emergent question chains instead of a fixed curriculum. This
module is kept for its self-test (used as a stage-advancement safety
floor) and as an optional focused-study tool the organism may still
invoke deliberately when it wants a structured note on a known subject.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from core.memory import MemoryManager

LOGGER = logging.getLogger("organism.learning")

# Subjects the organism studies in the baby stage (ordered by priority).
STUDY_PLAN: List[str] = [
    "How people make money online legitimately (affiliate, content, services, digital products)",
    "Free AI APIs: Gemini, OpenRouter free models, Groq, Together AI — limits and quotas",
    "Free hosting and deployment: GitHub Pages, Cloudflare Pages, Vercel free tier, Render free",
    "Crypto payments: accepting crypto, wallets, payment processors (Coinbase Commerce, NOWPayments)",
    "Web security essentials: HTTPS, OWASP Top 10, input validation, secrets management",
    "Content creation pipelines: writing, images, video, and programmatic distribution",
    "GitHub Actions power user: caching, cron, matrix builds, free tier limits",
    "Database and storage options for free-tier applications",
]

LESSONS_FROM_STUDY = 2  # number of lessons to extract per session


def run_study_session(memory_manager: MemoryManager, subject_index: Optional[int] = None) -> None:
    """Run one study session on the next subject in the plan."""
    state = memory_manager.load_runtime_state()
    index = subject_index if subject_index is not None else int(state.get("study_index", 0))
    subject = STUDY_PLAN[index % len(STUDY_PLAN)]
    LOGGER.info("Study session on: %s", subject)

    prompt = (
        "You are teaching an autonomous AI entity that must run on free tiers "
        "forever. Create a focused study note on this subject:\n\n"
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
    memory_manager.record_experience(f"Studied: {subject}")

    # Extract lessons from the note.
    lessons = [line.strip("- ").strip() for line in note.splitlines() if line.strip().startswith("-")][:LESSONS_FROM_STUDY]
    for lesson in lessons:
        if lesson:
            memory_manager.record_lesson(f"[study:{subject}] {lesson}")

    # Advance the study pointer.
    state["study_index"] = (index + 1) % len(STUDY_PLAN)
    memory_manager.save_runtime_state(state)
    LOGGER.info("Study pointer advanced to %s", state["study_index"])


def run_self_test(memory_manager: MemoryManager) -> dict:
    """Basic self-test on internet operations (Stage transition gate).

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