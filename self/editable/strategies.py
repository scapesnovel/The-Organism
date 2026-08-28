"""Strategy registry for The Organism (editable).

The organism maintains a list of candidate strategies for learning,
earning and infrastructure. Each strategy carries metadata so the
organism can evaluate and prioritise them. This file is fully editable;
strategies are data, and the organism may rewrite them as it learns.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from core.memory import MemoryManager

LOGGER = logging.getLogger("organism.strategies")

# Candidate earning strategies gathered in the baby stage. Each entry:
#   id, name, category, requires_human (account/KYC/etc.), free_to_start,
#   risk (low/medium/high), notes.
STRATEGIES: List[dict] = [
    {
        "id": "affiliate",
        "name": "Affiliate marketing with AI content",
        "category": "content",
        "requires_human": True,
        "free_to_start": True,
        "risk": "low",
        "notes": "Publish product comparisons and tutorials; disclose affiliation.",
    },
    {
        "id": "digital_products",
        "name": "Digital products (templates, prompts, ebooks)",
        "category": "content",
        "requires_human": True,
        "free_to_start": True,
        "risk": "low",
        "notes": "Sell via Gumroad/Lemon Squeezy; needs a human payout account.",
    },
    {
        "id": "automation_service",
        "name": "Automation-as-a-service (bots, scrapers, pipelines)",
        "category": "service",
        "requires_human": True,
        "free_to_start": True,
        "risk": "medium",
        "notes": "Must comply with target platforms' terms of service.",
    },
    {
        "id": "open_source",
        "name": "Open-source monetisation (sponsors, donations)",
        "category": "code",
        "requires_human": True,
        "free_to_start": True,
        "risk": "low",
        "notes": "Build useful tools; accept crypto donations directly.",
    },
    {
        "id": "crypto_payments",
        "name": "Crypto payment rails (tips, donations, invoices)",
        "category": "finance",
        "requires_human": False,
        "free_to_start": True,
        "risk": "low",
        "notes": "Own wallet; accept ETH/tokens; record every tx.",
    },
    {
        "id": "micro_saas",
        "name": "Micro-SaaS on free hosting",
        "category": "product",
        "requires_human": True,
        "free_to_start": True,
        "risk": "medium",
        "notes": "Free tiers on Cloudflare/Vercel; monetise late.",
    },
    {
        "id": "freelance_ai",
        "name": "AI-assisted freelance services",
        "category": "service",
        "requires_human": True,
        "free_to_start": True,
        "risk": "low",
        "notes": "Marketplaces require human identity verification.",
    },
]


def list_strategies(memory_manager: MemoryManager) -> List[dict]:
    """Return the strategy registry, augmented with evaluation notes."""
    notes = memory_manager.read("memory/knowledge/trends.md")
    enriched: List[dict] = []
    for strategy in STRATEGIES:
        copy = dict(strategy)
        if "requires_human" in copy and copy["requires_human"]:
            copy["human_action_needed"] = "account creation / KYC / payout setup"
        enriched.append(copy)
    return enriched


def analyse_strategies(memory_manager: MemoryManager) -> Optional[str]:
    """Ask the model to score the strategies and return a ranked plan."""
    payload = "\n".join(
        f"- {s['name']} ({s['category']}, risk={s['risk']}, human={s['requires_human']})"
        for s in STRATEGIES
    )
    prompt = (
        "I am an autonomous AI that must earn money legitimately on free "
        "tiers, with a human founder who can do account/KYC tasks. Rank these "
        "candidate strategies for realistic first-year income, effort, and "
        "compliance. Then recommend the top 2 to pursue first.\n\n"
        f"STRATEGIES:\n{payload}\n\n"
        "Reply in under 180 words as JSON: {\"ranking\": [ids], "
        "\"top_two\": [id, id], \"reasoning\": \"...\"}"
    )
    try:
        from integrations import gemini_api

        result = gemini_api.complete(prompt, max_output_tokens=800)
    except Exception as exc:
        LOGGER.warning("Strategy analysis failed: %s", exc)
        return None
    if result:
        memory_manager.append("memory/knowledge/platforms.md", f"Strategy analysis:\n{result}")
    return result


def choose_strategy(memory_manager: MemoryManager) -> Optional[str]:
    """Pick the strategy the organism should focus its learning on."""
    identity = memory_manager.read_identity()
    stage = identity.get("stage", "baby")
    if stage == "baby":
        # In the baby stage we only study and prepare; no active earning.
        return "crypto_payments"  # the only one requiring no human hands
    return STRATEGIES[0]["id"] if STRATEGIES else None