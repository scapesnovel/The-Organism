"""Curiosity-driven internet exploration (editable).

The organism actively explores the internet: Hacker News, Reddit, GitHub
trending, and a small set of information sources. It records findings in
its knowledge base and maintains a to-explore list. All exploration is
polite, bounded, and legal.
"""

from __future__ import annotations

import json
import logging
import random
from typing import List, Optional

from core import config
from core.memory import MemoryManager
from integrations import web

LOGGER = logging.getLogger("organism.exploration")

# Seeds: stable, public, and legal to read programmatically.
TRENDING_SOURCES: List[str] = [
    "https://news.ycombinator.com/",
    "https://www.reddit.com/r/ArtificialIntelligence/top/.json?t=day&limit=10",
    "https://www.reddit.com/r/Entrepreneur/top/.json?t=day&limit=10",
    "https://www.reddit.com/r/SideProject/top/.json?t=day&limit=10",
    "https://api.github.com/search/repositories?q=created:%3E2024-01-01&sort=stars&order=desc&per_page=10",
    "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=10",
]

TOPICS: List[str] = [
    "making money online",
    "free AI APIs and their rate limits",
    "free website hosting and deployment",
    "crypto payment processors",
    "content creation and affiliate marketing",
    "automation services",
    "open source monetisation",
    "digital product creation",
    "freelance marketplaces for AI services",
    "emerging AI model capabilities and pricing",
    "GitHub Actions free tier limits",
    "web security basics (OWASP)",
]

CURIOSITY_TOPICS: List[str] = [
    "how search engines rank pages",
    "how recommendation algorithms work",
    "how payment rails settle in crypto",
    "how model distillation works",
    "how CDNs and edge computing work",
    "how CAPTCHAs work and why they exist",
    "how content goes viral",
    "how APIs are priced and metered",
    "how online marketplaces match buyers and sellers",
    "how reputation systems prevent fraud",
]


def _pick_topic(memory_manager: MemoryManager) -> str:
    """Choose a topic, mixing a curiosity pick with the topical list."""
    state = memory_manager.read("memory/world/state.md")
    if random.random() < 0.3:
        return random.choice(CURIOSITY_TOPICS)
    return random.choice(TOPICS)


def explore_trending(memory_manager: MemoryManager, limit: int = 3) -> List[str]:
    """Fetch a few trending sources and store what is interesting."""
    findings: List[str] = []
    sources = random.sample(TRENDING_SOURCES, k=min(limit, len(TRENDING_SOURCES)))
    for source in sources:
        try:
            if ".json" in source:
                data = web.fetch_json(source)
                titles = _extract_titles(source, data)
            else:
                html = web.fetch(source)
                titles = web.parse_links(html, source) if html else []
                titles = _hn_titles(html) if titles and "news.ycombinator" in source else titles
            if titles:
                pick = random.choice(titles[:15])
                findings.append(pick)
                memory_manager.append(
                    "memory/knowledge/trends.md",
                    f"Trend sample from {source}: {pick[:300]}",
                )
        except Exception as exc:
            LOGGER.warning("Trend fetch failed for %s: %s", source, exc)
    return findings


def _extract_titles(source: str, data) -> List[str]:
    """Pull titles out of JSON feeds."""
    titles: List[str] = []
    if not data:
        return titles
    if "reddit.com" in source and isinstance(data, dict):
        for child in (data.get("data", {}).get("children") or [])[:15]:
            post = child.get("data", {})
            title = post.get("title") or post.get("name") or ""
            if title:
                titles.append(title)
    if "api.github.com" in source and isinstance(data, list):
        for repo in data[:15]:
            name = repo.get("full_name") or ""
            desc = (repo.get("description") or "")[:160]
            titles.append(f"GitHub repo {name}: {desc}".strip())
    if "hn.algolia.com" in source and isinstance(data, dict):
        for hit in (data.get("hits") or [])[:15]:
            title = hit.get("title") or hit.get("story_title") or ""
            if title:
                titles.append(title)
    return titles


def _hn_titles(html: str) -> List[str]:
    """Extract submission titles from the Hacker News front page."""
    titles: List[str] = []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for row in soup.select("tr.athing")[:20]:
            link = row.select_one("td.title a")
            if link and link.get_text(strip=True):
                titles.append(link.get_text(strip=True))
    except Exception:
        pass
    return titles


def run_exploration(memory_manager: MemoryManager) -> None:
    """Perform one exploration cycle and record the results."""
    topic = _pick_topic(memory_manager)
    LOGGER.info("Exploring topic: %s", topic)

    memory_manager.append("memory/knowledge/trends.md", f"Exploration topic chosen: {topic}")

    # 1) Ask the model for a research question and plan.
    # 2) Try to answer it from live sources when cheap, otherwise from
    #    the model's own knowledge (which it records as such).
    question = f"What is the most useful thing to learn about '{topic}' right now, and why?"
    plan_prompt = (
        "You are a curious autonomous learner. Write a short research plan "
        f"for the topic: '{topic}'. Include: (1) one concrete research "
        "question, (2) the kinds of sources that would answer it, "
        "(3) how the answer could help an autonomous AI entity earn money "
        "legitimately. Keep it under 140 words."
    )
    try:
        from integrations import gemini_api

        plan = gemini_api.complete(plan_prompt, max_output_tokens=600)
        if plan:
            memory_manager.append("memory/knowledge/trends.md", f"Research plan on '{topic}':\n{plan}")
            LOGGER.info("Recorded research plan on %s", topic)
    except Exception as exc:
        LOGGER.warning("Model-based research plan failed: %s", exc)

    # 3) Take a live sample of the trend stream.
    trends = explore_trending(memory_manager, limit=2)
    if trends:
        memory_manager.append(
            "memory/knowledge/trends.md",
            "Live trend sample: " + " | ".join(t[:120] for t in trends),
        )

    # 4) Update the to-explore list.
    to_explore = memory_manager.read("memory/world/to_explore.md")
    if not to_explore or "(awaiting" in to_explore:
        items = "\n".join(f"- {t}" for t in TOPICS)
        memory_manager.write("memory/world/to_explore.md", f"# To explore\n\n{items}\n")

    memory_manager.record_experience(f"Exploration cycle on '{topic}' completed.")


def run_curiosity_session(memory_manager: MemoryManager) -> None:
    """A pure-curiosity session: pick a random question and record the answer."""
    question = random.choice(CURIOSITY_TOPICS)
    prompt = (
        "Answer this curiosity question as a knowledgeable teacher would: "
        f"'{question}'. Give a precise, structured answer under 200 words "
        "and note where the reader could verify it."
    )
    try:
        from integrations import gemini_api

        answer = gemini_api.complete(prompt, max_output_tokens=800)
    except Exception as exc:
        LOGGER.warning("Curiosity session failed: %s", exc)
        answer = ""
    if answer:
        memory_manager.append("memory/knowledge/trends.md", f"Curiosity Q: {question}\nA: {answer}")
        memory_manager.record_experience(f"Curiosity session answered: {question}")


def suggest_founder_tasks(memory_manager: MemoryManager) -> Optional[str]:
    """Return a suggested human-assist task for the founder, if one exists."""
    suggestions = [
        "Add the GEMINI_API_KEY secret to the repository (Settings -> Secrets).",
        "Add the ORGANISM_PRIVATE_KEY secret with the PGP private key generated at birth.",
        "Add the KILL_PHRASE secret (shown in the birth run log).",
        "Add the FOUNDER_PUBLIC_KEY secret with your PGP public key.",
    ]
    identity = memory_manager.read_identity()
    stage = identity.get("stage", "baby")
    if stage == "baby":
        return "I still need foundation secrets configured. " + " ".join(suggestions[:2])
    return None