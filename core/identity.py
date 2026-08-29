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


class BirthDeferred(RuntimeError):
    """Raised when the birth ritual cannot complete meaningfully right now.

    The founder's rule is absolute: the organism's name and purpose come
    from a model, NEVER from a hardcoded fallback. When the brain is
    unreachable (outage, quota, timeout) the birth is deferred to a later
    wake — the organism refuses to be born broken.
    """


def _atomic_handover(text: str) -> None:
    """Emit the one-time handover block so no other output can cut into it.

    The founder's first live birth showed logger output (stderr) landing in
    the MIDDLE of the printed key block, because stdout and stderr are
    separate buffered streams that GitHub Actions interleaves by arrival
    time. Flush all logging handlers and both streams first, then write the
    whole block with one os.write syscall to the stdout file descriptor —
    a single write cannot be split by Python-level buffering.
    """
    import sys

    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass
    try:
        sys.stderr.flush()
        sys.stdout.flush()
    except Exception:
        pass
    data = text.encode("utf-8", errors="replace")
    try:
        os.write(sys.stdout.fileno(), data)
    except (OSError, ValueError, AttributeError):
        # Non-file stdout (tests, embedded interpreters): plain print.
        print(text, flush=True)


def generate_identity(model_client) -> dict:
    """Ask the model to name the organism and describe its purpose.

    ``model_client`` must expose ``complete(prompt) -> str``.
    Raises :class:`BirthDeferred` when the model cannot provide a name —
    a birth without a self-chosen identity must never happen.
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
        raise BirthDeferred(
            "The model could not provide a name (outage or quota). Birth is "
            "deferred to a later wake cycle — the organism is never born "
            "with a hardcoded fallback identity."
        )

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
        raise BirthDeferred(
            "The model named the organism but could not describe a purpose "
            "(outage or quota mid-birth). Birth is deferred so the identity "
            "is never half-formed or hardcoded."
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
    # --- Kill phrase generation (disclosed in the handover, never logged) --
    kill_phrase = kill_switch.generate_kill_phrase()
    kill_title = kill_switch.kill_issue_title(kill_phrase)

    # The founder must be able to capture the private key and kill phrase
    # exactly once. GitHub cannot let the workflow read secrets back, so
    # this single controlled disclosure in the FIRST run's log is the only
    # handover channel. It is intentionally NOT passed through the redactor
    # needle list (the secrets do not exist yet).
    #
    # ATOMIC WRITE, STDOUT ONLY: the founder's first live birth proved that
    # print() (stdout) and logging (stderr) interleave unpredictably in the
    # Actions log — a logger line landed INSIDE the armored key block and
    # nearly corrupted the one-time handover. So: flush every log handler
    # first, then emit the WHOLE handover (key + kill phrase together) as a
    # single os.write to stdout that cannot be split by buffering.
    handover_parts = []
    if private_armor:
        handover_parts.append(
            "SECRET 1 — ORGANISM_PRIVATE_KEY (copy the whole armored block, "
            "BEGIN and END lines included):\n"
            f"{private_armor}"
        )
    handover_parts.append(
        "SECRET 2 — KILL_PHRASE (store this value in the KILL_PHRASE "
        f"secret):\n{kill_phrase}\n"
        f"(To kill later, open an issue titled: {kill_title})"
    )
    handover = (
        "\n=== ONE-TIME KEY HANDOVER (save the secrets below, then delete "
        "this workflow run's logs) ===\n"
        + "\n\n".join(handover_parts)
        + "\n=== END ONE-TIME KEY HANDOVER ===\n"
    )
    _atomic_handover(handover)

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