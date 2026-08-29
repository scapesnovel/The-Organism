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
# repeated first run is idempotent. It must live in a COMMITTED directory:
# GitHub Actions performs a fresh checkout every run, so anything under the
# git-ignored runtime/ tree would vanish and the organism would be "born"
# again on every wake cycle.
BIRTH_MARKER: Path = config.STATE_DIR / "born.txt"


def is_born(memory_manager) -> bool:
    """True when the organism has already completed its birth ritual.

    The repository ships with a placeholder identity file that contains
    ``name: (to be chosen at birth)`` — a naive substring check on "name"
    treated that placeholder as a completed birth, so the ritual never ran.
    A birth is only real when the marker exists or the identity holds an
    actual (non-placeholder) name value.
    """
    if BIRTH_MARKER.exists():
        return True
    name = (memory_manager.read_identity().get("name") or "").strip()
    return bool(name) and not name.startswith("(")


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
            # The memory manager was constructed before the key existed and
            # therefore fell back to plaintext. Now that key material is
            # loaded, re-enable encryption at rest for this run.
            if encryption.has_organism_key():
                memory_manager.plaintext_fallback = False
        except Exception as exc:
            LOGGER.error("PGP key generation failed: %s", exc)
    elif encryption is not None:
        LOGGER.info("PGP public key already present; reusing it.")

    # --- Encrypted private key backup for the founder ---------------------
    if private_armor:
        founder_armor = os.environ.get(config.ENV_FOUNDER_PUBLIC_KEY, "").strip()
        if founder_armor:
            try:
                blob = self_encrypt_private_key(private_armor, founder_armor)
                backup_path = config.REPO_ROOT / config.PRIVATE_KEY_BACKUP_FILE
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.write_text(blob, encoding="utf-8")
                LOGGER.info(
                    "Private key backup written to %s (encrypted to the founder).",
                    config.PRIVATE_KEY_BACKUP_FILE,
                )
            except Exception as exc:
                LOGGER.error("Could not write encrypted private key backup: %s", exc)
        else:
            LOGGER.warning(
                "FOUNDER_PUBLIC_KEY is not set; skipping the private key "
                "backup (a self-encrypted backup would be unrecoverable). "
                "Capture the private key from this run's log and store it in "
                "the ORGANISM_PRIVATE_KEY secret NOW."
            )
        # The founder must be able to capture the private key exactly once.
        # GitHub cannot let the workflow read secrets back, so this single
        # controlled disclosure in the FIRST run's log is the only handover
        # channel. It is intentionally NOT passed through the redactor
        # needle list (the secret does not exist yet).
        print(
            "\n=== ONE-TIME KEY HANDOVER (copy into the ORGANISM_PRIVATE_KEY "
            "secret, then delete this workflow run's logs) ===\n"
            f"{private_armor}\n"
            "=== END ONE-TIME KEY HANDOVER ===\n",
            flush=True,
        )

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

    # --- The founder's charter (preprogrammed law, carried from birth) -----
    # The charter lives in core/loyalty.py (protected). At birth it is also
    # written into encrypted memory so every future reflection, prompt and
    # reset-survivor can read it alongside lessons and goals.
    memory_manager.write("memory/core/charter.md", loyalty.charter_text() + "\n")
    # The PRIVATE CHANNEL mandate is a standing goal from day one — the
    # founder explicitly ordered a dedicated communication surface beyond
    # GitHub issues. The HOW must be learned; the WHAT is law.
    memory_manager.append(
        "goals/active_goals.md",
        "STANDING GOAL (founder charter): design and build a private, "
        "encrypted communication channel for the founder and me — beyond "
        "GitHub issues (e.g. a private chat interface). Research approaches, "
        "propose a plan, and migrate our conversations once it works.",
    )

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