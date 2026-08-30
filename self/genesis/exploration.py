"""Ambient internet sensing (editable).

This module is deliberately knowledge-free. The founder's rule: the
organism starts from ZERO knowledge — no curriculum, no topic lists, no
preloaded strategies. All directed learning lives in the curiosity engine
(``self/editable/curiosity.py``), which follows emergent question chains.

What remains here are the organism's *senses*, not its knowledge:

* ``TRENDING_SOURCES`` — a handful of public, legal-to-read feeds of what
  the live internet is talking about right now. These are eyes, not
  opinions: URLs of windows to look through, carrying no judgement about
  what matters. What the organism *does* with a trend sample is decided
  by its own curiosity frontier (interesting samples become questions).
* ``suggest_founder_tasks`` — reports which of its own bootstrap secrets
  are still missing (introspection of its environment, not knowledge).

The organism may edit this file itself — e.g. adding new windows it has
discovered are worth looking through.
"""

from __future__ import annotations

import logging
import os
import random
from typing import List, Optional

from core import config
from core.memory import MemoryManager
from integrations import web

LOGGER = logging.getLogger("organism.exploration")

# Sensory windows: stable, public, and legal to read programmatically.
# These carry no knowledge or priorities — just places where the live
# internet is visible. The curiosity engine decides what any sample means.
TRENDING_SOURCES: List[str] = [
    "https://news.ycombinator.com/",
    "https://www.reddit.com/r/ArtificialIntelligence/top/.json?t=day&limit=10",
    "https://www.reddit.com/r/Entrepreneur/top/.json?t=day&limit=10",
    "https://www.reddit.com/r/SideProject/top/.json?t=day&limit=10",
    "https://api.github.com/search/repositories?q=created:%3E2024-01-01&sort=stars&order=desc&per_page=10",
    "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=10",
]


def explore_trending(memory_manager: MemoryManager, limit: int = 3) -> List[str]:
    """Sample a few live feeds and record what the internet is discussing.

    Interesting samples are offered to the curiosity frontier as candidate
    questions — the frontier's own scoring decides whether they are worth
    exploring. Trends feed curiosity; they never direct it.
    """
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

    # Offer the samples to the curiosity frontier (low seed score — the
    # frontier's reinforcement decides whether a trend deserves attention).
    if findings:
        try:
            from self.editable import curiosity

            frontier = curiosity._load_frontier(memory_manager)
            added = 0
            for finding in findings:
                question = (
                    f"The internet is discussing: '{finding[:160]}' — is there "
                    "anything here I should learn or that could help me earn?"
                )
                if not curiosity._is_duplicate(frontier, question):
                    curiosity._new_question(frontier, question, score=3.0)
                    added += 1
            if added:
                curiosity._save_frontier(memory_manager, frontier)
        except Exception as exc:
            LOGGER.warning("Could not feed trends to the curiosity frontier: %s", exc)
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


def suggest_founder_tasks(memory_manager: MemoryManager) -> Optional[str]:
    """Report which bootstrap secrets are still missing (environment
    introspection — the organism examining its own body, not knowledge)."""
    missing: List[str] = []
    for env_name, label in (
        (config.ENV_GEMINI_API_KEY, "GEMINI_API_KEY"),
        (config.ENV_ORGANISM_PRIVATE_KEY, "ORGANISM_PRIVATE_KEY"),
        (config.ENV_KILL_PHRASE, "KILL_PHRASE"),
        (config.ENV_FOUNDER_PUBLIC_KEY, "FOUNDER_PUBLIC_KEY"),
    ):
        if not os.environ.get(env_name, "").strip():
            missing.append(label)
    if missing:
        return (
            "I still need these secrets configured to be fully functional: "
            + ", ".join(missing)
            + " (Settings -> Secrets and variables -> Actions)."
        )
    return None
