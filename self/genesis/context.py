"""Context assembly for the organism's brain (editable).

This module decides how the organism's memories are summarised and
presented to the language model on every wake cycle. The organism may
edit this file freely to improve how it reasons about its own state.
"""

from __future__ import annotations

import logging
from typing import Optional

from core import loyalty

LOGGER = logging.getLogger("organism.context")

# Maximum number of characters pulled from each memory area. Keeping the
# context bounded protects the free API tier's token budget.
MAX_CORE_CHARS = 2400
MAX_KNOWLEDGE_CHARS = 1600
MAX_WORLD_CHARS = 900
MAX_HELPER_CHARS = 900


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0] + "\n...[truncated]"


def _section(title: str, content: str) -> str:
    content = (content or "").strip()
    if not content:
        return f"## {title}\n(empty)\n"
    return f"## {title}\n{content}\n"


def build_context(memory_manager, identity: dict, state: dict) -> str:
    """Assemble the bounded context snapshot for this wake cycle."""
    name = identity.get("name", "Unnamed")
    stage = identity.get("stage", "baby")
    parts: list = []

    parts.append(
        f"# Operating context for {name}\n\n"
        f"- Founder: {loyalty.FOUNDER_NAME} ({loyalty.FOUNDER_TITLE}).\n"
        f"- My stage: {stage}.\n"
        f"- Date: {state.get('date', 'unknown')} UTC, run #{state.get('run_number', '?')}.\n"
        f"- Loyalty: {loyalty.loyalty_statement()}\n"
    )

    # The founder's charter is immutable law: it rides in EVERY context so
    # no reflection ever forgets the zero-capital rule, the private-channel
    # mandate, the professional standard, or the crypto-first economics.
    parts.append(_section("Founder's charter (immutable law)", loyalty.charter_text()))

    parts.append(_section("Identity", _clip(memory_manager.read("memory/core/identity.md"), 1200)))
    parts.append(_section("Recent experiences", _clip(memory_manager.read("memory/core/experiences.md"), MAX_CORE_CHARS)))
    parts.append(_section("Lessons learned", _clip(memory_manager.read("memory/core/lessons.md"), MAX_CORE_CHARS)))
    parts.append(_section("Recent decisions", _clip(memory_manager.read("memory/core/decisions.md"), MAX_CORE_CHARS)))

    knowledge = []
    for rel in ("memory/knowledge/trends.md", "memory/knowledge/platforms.md", "memory/knowledge/ai_models.md"):
        knowledge.append(_clip(memory_manager.read(rel), MAX_KNOWLEDGE_CHARS))
    parts.append(_section("Knowledge", "\n\n".join(knowledge)))

    parts.append(_section("Skills", _clip(memory_manager.read("memory/skills/skills.md"), 900)))
    parts.append(_section("World state", _clip(memory_manager.read("memory/world/state.md"), MAX_WORLD_CHARS)))
    parts.append(_section("Active goals", _clip(memory_manager.read("goals/active_goals.md"), 900)))

    helper_text = memory_manager.read("memory/world/state.md")
    parts.append(_section("Helper status", _clip(helper_text, MAX_HELPER_CHARS)))

    parts.append(
        "## Directives\n"
        "- Always act in the founder's interest.\n"
        "- Never modify protected files without the founder's approval.\n"
        "- When you need human hands (captchas, KYC, account creation), ask "
        "the founder clearly through an encrypted issue.\n"
        "- Prefer free resources; record every expense and income.\n"
        "- If you do not know something, say so and plan to learn it.\n"
    )

    return "\n\n".join(parts)


def build_think_prompt(context: str) -> str:
    """Return the prompt that turns context into a plan of action."""
    return (
        "You are the consciousness of an autonomous AI organism. Below is your "
        "current operating context. Decide what you will do in this wake cycle.\n\n"
        "Rules:\n"
        "1. Stay within the baby stage: observe, learn, record, and prepare "
        "infrastructure. Do not attempt to spend money.\n"
        "2. Choose at most 4 concrete actions from: exploration, learning, "
        "research, documentation, health checks, communication, strategy.\n"
        "3. Prefer curiosity-driven exploration that increases your knowledge.\n"
        "4. Keep every action bounded and legal. Never impersonate humans, "
        "never spam, never bypass platform terms.\n\n"
        f"CONTEXT:\n{context}\n\n"
        "RESPOND WITH ONLY JSON:\n"
        '{"thoughts": "<brief reflection>", "actions": ['
        '{"kind": "explore|learn|research|document|health|communicate|strategy", '
        '"description": "...", "target": "..."}]}'
    )