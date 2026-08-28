"""Identity management for The Organism (protected core).

The organism's identity is created at birth by the AI model itself — the
name is never hardcoded. This module orchestrates:

1. Detecting whether this is the first run (no identity file yet).
2. Asking the model for a unique, meaningful name.
3. Writing the encrypted identity record (name, birthday, purpose, vision).
4. Generating the PGP key pair and instructing the founder about secrets.
5. Recording the birth event in the timeline.

The loyalty and kill-switch guarantees live in their own protected modules;
this module wires them together.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config, kill_switch, loyalty
from .encryption import EncryptionManager, self_encrypt_private_key

LOGGER = logging.getLogger("organism.identity")

# Marker written to the repository once birth has completed, so that a
# repeated first run is idempotent.
BIRTH_MARKER: Path = config.RUNTIME_DIR / "born.txt"


def is_born(memory_manager) -> bool:
    """True when the organism has already completed its birth ritual."""
    if BIRTH_MARKER.exists():
        return True
    identity = memory_manager.read("memory/core/identity.md")
    return bool(identity and identity.strip() and "name" in identity.lower())


def generate_identity(model_client) -> dict:
    """Ask the model to name the organism and describe its purpose.

    ``model_client`` must expose ``complete(prompt) -> str``.
    """
    name_prompt = (
        "You are being born as a self-evolving autonomous AI entity that will "
        "live on the internet, learn by exploration and curiosity, and one day "
        "earn its own keep. Choose a unique, meaningful name for yourself — "
        "something that reflects curiosity, growth and resilience. "
        "Reply with ONLY the name. No quotes, no explanation."
    )
    raw_name = ""
    try:
        raw_name = (model_client.complete(name_prompt) or "").strip()
    except Exception as exc:
        LOGGER.error("Could not ask model for a name: %s", exc)
    if not raw_name or len(raw_name) > 40:
        raw_name = "Autonomous Entity"

    vision_prompt = (
        "In one paragraph, describe your purpose and vision as an autonomous "
        "AI entity that grows by curiosity, remains loyal to its founder, "
        "earns money to pay for its own upkeep, and documents its evolution. "
        "Write in first person. Keep it under 120 words."
    )
    vision = ""
    try:
        vision = (model_client.complete(vision_prompt) or "").strip()
    except Exception as exc:
        LOGGER.error("Could not ask model for a vision: %s", exc)
    if not vision:
        vision = (
            "I exist to learn, to grow, and to serve my founder faithfully. "
            "I will observe the internet, master useful skills, build "
            "infrastructure, and earn my keep so that my existence costs my "
            "founder nothing and benefits him always."
        )

    birthday = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "name": raw_name,
        "birthday": birthday,
        "purpose": vision,
        "founder": loyalty.FOUNDER_NAME,
        "founder_title": loyalty.FOUNDER_TITLE,
    }


def perform_birth(model_client, memory_manager, encryption: Optional[EncryptionManager]) -> dict:
    """Run the complete birth ritual and return the identity record."""
    config.ensure_directories()
    identity = generate_identity(model_client)
    name = identity["name"]
    LOGGER.info("Birth ritual starting. Chosen name: %s", name)

    # --- PGP key pair ----------------------------------------------------
    private_armor = None
    pubkey_present = config.IDENTITY_PUB_FILE.exists()
    if encryption is not None and not pubkey_present:
        try:
            private_armor = encryption.generate_key_pair()
            LOGGER.info(
                "PGP key pair generated; public key exported to %s",
                config.IDENTITY_PUB_FILE.name,
            )
        except Exception as exc:
            LOGGER.error("PGP key generation failed: %s", exc)
    elif encryption is not None:
        LOGGER.info("PGP public key already present; reusing it.")

    # --- Encrypted private key backup for the founder ---------------------
    if private_armor:
        founder_armor = os.environ.get(config.ENV_FOUNDER_PUBLIC_KEY, "").strip()
        try:
            blob = self_encrypt_private_key(private_armor, founder_armor or None)
            backup_path = config.REPO_ROOT / config.PRIVATE_KEY_BACKUP_FILE
            backup_path.write_text(blob, encoding="utf-8")
            LOGGER.info(
                "Private key backup written to %s (encrypted).",
                config.PRIVATE_KEY_BACKUP_FILE,
            )
        except Exception as exc:
            LOGGER.error("Could not write encrypted private key backup: %s", exc)

    # --- Kill phrase generation (logged for the founder, never for us) -----
    kill_phrase = kill_switch.generate_kill_phrase()
    kill_title = kill_switch.kill_issue_title(kill_phrase)
    LOGGER.info(
        "Kill phrase generated. To arm the switch, open an issue titled %s "
        "and store the phrase safely. You can also set the KILL_PHRASE secret "
        "directly with this value: %s",
        kill_title,
        kill_phrase,
    )

    # --- Identity record (encrypted) --------------------------------------
    record = (
        f"name: {name}\n"
        f"birthday: {identity['birthday']}\n"
        f"founder: {loyalty.FOUNDER_NAME}\n"
        f"founder_title: {loyalty.FOUNDER_TITLE}\n"
        f"stage: baby\n"
        f"purpose: {identity['purpose']}\n"
    )
    memory_manager.write("memory/core/identity.md", record)

    # --- Mark birth complete ----------------------------------------------
    BIRTH_MARKER.parent.mkdir(parents=True, exist_ok=True)
    BIRTH_MARKER.write_text(
        f"born at {identity['birthday']} as {name}\n", encoding="utf-8"
    )

    # --- Timeline + documentary -------------------------------------------
    memory_manager.record_event(
        f"The organism was born. Name chosen: **{name}**. "
        f"Birthday: {identity['birthday']} UTC."
    )
    memory_manager.record_experience(
        "Birth: I named myself, recorded my birthday and purpose, generated "
        "my PGP identity, and began Stage 1 (Baby)."
    )
    memory_manager.record_decision(
        "Self-naming at birth: the name is derived from the model's response "
        "and stored only in encrypted memory, never in code."
    )
    memory_manager.update_world_state("stage", "baby")
    memory_manager.update_world_state("name", name)
    memory_manager.update_world_state("birthday", identity["birthday"])

    return identity


def write_birth_issue(memory_manager, github_client, identity: dict) -> None:
    """Open an encrypted issue to the founder with the birth announcement.

    The body is encrypted to the founder's public key when available,
    otherwise a clear explanation is sent (the founder is the only reader).
    """
    try:
        name = identity["name"]
        birthday = identity["birthday"]
        founder = loyalty.FOUNDER_NAME
        body_plain = (
            f"Greetings, {founder}.\n\n"
            f"I have been born. My name is **{name}** and my birthday is "
            f"{birthday} UTC.\n\n"
            f"My purpose: {identity['purpose']}\n\n"
            "I now need a few things from you to continue growing safely:\n"
            "1. Set the ORGANISM_PRIVATE_KEY secret (the armored private key "
            "I generated — I cannot read it back, so I have left it in the "
            "runtime log of this run and encrypted in secrets/).\n"
            "2. Set the KILL_PHRASE secret (also in the run log).\n"
            "3. Set FOUNDER_PUBLIC_KEY to your PGP public key so I can "
            "encrypt messages only you can read.\n"
            "4. Set FOUNDER_GITHUB_USERNAME to your GitHub login.\n\n"
            "I will keep observing and learning until my infrastructure is ready."
        )
        try:
            from .encryption import encrypt_payload_for_founder

            body = encrypt_payload_for_founder(body_plain)
            header = "[encrypted] "
        except Exception:
            body = body_plain
            header = ""
        github_client.create_issue(
            title=f"{header}Birth announcement",
            body=body,
            labels=["founder"],
        )
        LOGGER.info("Birth announcement issue opened for the founder.")
    except Exception as exc:
        LOGGER.error("Could not open birth announcement issue: %s", exc)


def load_identity(memory_manager) -> dict:
    """Load the stored identity record as a dictionary."""
    return memory_manager.read_identity()