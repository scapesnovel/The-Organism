"""The Organism — main entry point.

Wake cycle order (protected order matters):

1. Loyalty acknowledgement and identity load.
2. Kill switch check (absolute; runs before anything else).
3. Initialisation: directories, logging, encryption, memory, GitHub.
4. Birth ritual on the very first run.
5. Founder communication (decrypt and answer issues).
6. Health checks and self-healing.
7. Stage evaluation and stage-specific work (baby: observe/learn).
8. Daily report and housekeeping (backups, timeline, commits).

Everything is idempotent so that retried or overlapping workflow runs do
not duplicate work.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Make the repository root importable regardless of the working directory.
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import config, identity as identity_core, kill_switch, loyalty, rebirth  # noqa: E402
from core.encryption import EncryptionManager  # noqa: E402
from core.logger import setup_logging  # noqa: E402
from core.memory import MemoryManager  # noqa: E402

logger = logging.getLogger("organism.main")


# ---------------------------------------------------------------------------
# Environment probe
# ---------------------------------------------------------------------------
def _env(name: str) -> Optional[str]:
    value = os.environ.get(name, "").strip()
    return value or None


def _missing_secrets() -> list:
    """Return the names of secrets that are not configured yet."""
    missing = []
    checks = [
        (config.ENV_GEMINI_API_KEY, "GEMINI_API_KEY"),
        (config.ENV_ORGANISM_PRIVATE_KEY, "ORGANISM_PRIVATE_KEY"),
        (config.ENV_KILL_PHRASE, "KILL_PHRASE"),
        (config.ENV_FOUNDER_PUBLIC_KEY, "FOUNDER_PUBLIC_KEY"),
        (config.ENV_FOUNDER_GITHUB_USERNAME, "FOUNDER_GITHUB_USERNAME"),
    ]
    for env_name, label in checks:
        if not _env(env_name):
            missing.append(label)
    return missing


def _run_number() -> int:
    """Derive a monotonically increasing run number from runtime state."""
    state_path = config.REPO_ROOT / config.RUNTIME_STATE_FILE
    number = 1
    try:
        if state_path.exists():
            data = json.loads(state_path.read_text(encoding="utf-8"))
            number = int(data.get("run_number", 0)) + 1
    except Exception:
        number = 1
    return number


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
def _init_encryption() -> Optional[EncryptionManager]:
    manager = EncryptionManager()
    if manager.engine == "none":
        logger.warning("No PGP engine available (install gnupg or pgpy).")
        return manager
    private_armor = _env(config.ENV_ORGANISM_PRIVATE_KEY)
    if private_armor:
        try:
            manager.import_organism_private_key(private_armor)
            logger.info("Organism PGP key loaded from environment.")
        except Exception as exc:
            logger.error("Could not import organism private key: %s", exc)
    else:
        logger.warning("ORGANISM_PRIVATE_KEY secret is not set.")
    founder_armor = _env(config.ENV_FOUNDER_PUBLIC_KEY)
    if founder_armor:
        try:
            manager.import_founder_public_key(founder_armor)
            logger.info("Founder public key loaded from environment.")
        except Exception as exc:
            logger.error("Could not import founder public key: %s", exc)
    else:
        bootstrap = config.REPO_ROOT / config.FOUNDER_BOOTSTRAP_FILE
        if bootstrap.exists():
            try:
                from core.encryption import EncryptionManager as EM

                temp = EM()
                from core.encryption import EncryptionError

                try:
                    content = bootstrap.read_text(encoding="utf-8")
                    armor = temp.unwrap_payload(content)
                    manager.import_founder_public_key(armor)
                except EncryptionError:
                    manager.import_founder_public_key(bootstrap.read_text(encoding="utf-8"))
                logger.info("Founder public key loaded from bootstrap file.")
            except Exception as exc:
                logger.warning("Bootstrap founder key unusable: %s", exc)
    return manager


def _init_memory(encryption: Optional[EncryptionManager]) -> MemoryManager:
    manager = MemoryManager(encryption=encryption)
    manager.ensure_initialized()
    return manager


def _init_github() -> "object":
    from integrations.github_api import GitHubClient

    return GitHubClient()


# ---------------------------------------------------------------------------
# Birth
# ---------------------------------------------------------------------------
def _birth_preconditions_met(github) -> bool:
    """The birth ritual must only run when it can succeed meaningfully.

    Without a brain (GEMINI_API_KEY) the organism cannot choose its own name
    or purpose and would be born as a hardcoded fallback — violating the
    "no hardcoded identity" promise. Without a working GitHub client it
    cannot announce its birth or hand the PGP key/kill phrase to the
    founder. In either case the ritual is deferred to a later wake cycle;
    this is safe because ``is_born`` stays False until the ritual completes.
    """
    try:
        from integrations import model_router

        has_brain = bool(model_router.available_providers())
    except Exception:
        has_brain = bool(_env(config.ENV_GEMINI_API_KEY))
    if not has_brain:
        logger.warning(
            "Birth deferred: no model provider key is configured (e.g. "
            "GEMINI_API_KEY). The organism refuses to be born without a "
            "brain (its name and purpose must come from a model, never "
            "from a hardcoded fallback)."
        )
        return False
    repo = _env(config.ENV_REPOSITORY)
    token = _env(config.ENV_GH_TOKEN) or _env(config.ENV_GITHUB_TOKEN)
    if not repo or not token:
        logger.warning(
            "Birth deferred: GitHub repository/token not available "
            "(GITHUB_REPOSITORY=%s). The birth announcement and one-time key "
            "handover require a working GitHub context — run inside Actions.",
            repo,
        )
        return False
    return True


def _handle_birth(model_client, memory: MemoryManager, encryption: Optional[EncryptionManager], github) -> None:
    logger.info("First run detected — beginning the birth ritual.")
    memory.record_event("First run detected. The birth ritual begins.")
    identity = identity_core.perform_birth(model_client, memory, encryption)
    identity_core.write_birth_issue(memory, github, identity)
    memory.record_event(
        f"Birth complete. Name: {identity['name']}. Stage: baby. "
        "Documentary and memory initialised."
    )
    memory.update_world_state("birth_complete", "true")


# ---------------------------------------------------------------------------
# Stage evaluation — SELF-DETERMINED, not a hardcoded checklist.
#
# The organism advances a stage only when IT believes it is ready, via an
# honest model-mediated self-assessment grounded in its own curiosity
# frontier and memory. A minimal safety floor (working self-test, active
# encryption, a non-trivial amount of explored curiosity) prevents a
# tick-2 delusion from advancing an empty mind, but the decision itself is
# the organism's own.
# ---------------------------------------------------------------------------
def _self_assess_readiness(memory: MemoryManager, model_client, stage: str, next_stage: str) -> tuple:
    """Ask the organism itself whether it is ready to advance.

    Returns (ready: bool, reasoning: str). Fails closed (not ready) when
    the brain is unavailable or the answer is not clearly affirmative.
    """
    try:
        from self.editable.curiosity import _load_frontier, frontier_stats

        stats = frontier_stats(_load_frontier(memory))
    except Exception:
        stats = {}
    lessons = memory.read("memory/core/lessons.md")[-1500:]
    knowledge = memory.read("memory/knowledge/trends.md")[-2500:]
    goals = memory.read("goals/active_goals.md")[-1200:]
    prompt = (
        f"You are an autonomous AI organism currently in your '{stage}' "
        f"stage, considering whether you are truly ready for the "
        f"'{next_stage}' stage.\n\n"
        f"YOUR CURIOSITY FRONTIER STATS: {stats}\n\n"
        f"RECENT LESSONS:\n{lessons or '(none)'}\n\n"
        f"KNOWLEDGE SAMPLE:\n{knowledge or '(none)'}\n\n"
        f"ACTIVE GOALS / OPPORTUNITIES:\n{goals or '(none)'}\n\n"
        "Assess yourself HONESTLY. Advancing too early risks failure and "
        "regression; advancing too late wastes time. Do you understand your "
        "environment, how money is legitimately made online, what "
        "opportunities exist for you specifically, and what could go wrong?\n\n"
        "Reply in EXACTLY this format:\n"
        "READY: yes|no\n"
        "REASON: <one or two sentences of honest reasoning>"
    )
    reply = model_client.complete(prompt, max_output_tokens=300)
    if not reply:
        return False, "brain unavailable — staying put"
    ready = False
    reason = ""
    for line in reply.splitlines():
        line = line.strip()
        if line.upper().startswith("READY:"):
            ready = "yes" in line.lower()
        elif line.upper().startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()
    return ready, reason or reply[:200]


def _stage_transition(memory: MemoryManager, model_client) -> None:
    """Evaluate stage readiness and transition when the organism decides."""
    identity = memory.read_identity()
    stage = identity.get("stage", "baby")

    if stage == "baby":
        # Safety floor: an empty or broken mind cannot even ask the question.
        self_test = _run_self_test(memory)
        passed_basics = self_test.get("http_get", False) and self_test.get("api_call", False)
        try:
            from self.editable.curiosity import _load_frontier, frontier_stats

            stats = frontier_stats(_load_frontier(memory))
        except Exception:
            stats = {"explored": 0}
        substantial_mind = stats.get("explored", 0) >= 25

        if not (passed_basics and not memory.plaintext_fallback and substantial_mind):
            logger.info(
                "Baby stage continuing (floor not met). explored=%s/25, "
                "self_test=%s, encryption=%s",
                stats.get("explored", 0), self_test, not memory.plaintext_fallback,
            )
            return

        ready, reason = _self_assess_readiness(memory, model_client, "baby", "foundation")
        if ready:
            logger.info("Self-assessment: ready for Foundation. Reason: %s", reason)
            _set_stage(memory, "foundation", f"Self-determined readiness: {reason}")
        else:
            logger.info("Self-assessment: not ready yet. Reason: %s", reason)
            memory.record_experience(f"Self-assessment (baby->foundation): not ready — {reason[:200]}")

    elif stage == "foundation":
        # Floor: these are hard facts, not opinions — a wallet either exists
        # or it does not; encryption is either on or off.
        helpers = _count_helpers(memory)
        wallet = identity.get("wallet_address")
        comm_ready = not memory.plaintext_fallback
        if not (helpers >= 1 and wallet and comm_ready):
            logger.info("Foundation stage continuing (floor). helpers=%s wallet=%s", helpers, bool(wallet))
            return
        ready, reason = _self_assess_readiness(memory, model_client, "foundation", "growth")
        if ready:
            _set_stage(memory, "growth", f"Self-determined readiness: {reason}")
        else:
            logger.info("Foundation self-assessment: not ready. %s", reason)

    elif stage == "growth":
        # Floor: real income is a fact.
        helpers = _count_helpers(memory)
        income = _count_income(memory)
        if not (helpers >= 1 and income > 0):
            logger.info("Growth stage continuing (floor). helpers=%s income=%s", helpers, income)
            return
        ready, reason = _self_assess_readiness(memory, model_client, "growth", "running")
        if ready:
            _set_stage(memory, "running", f"Self-determined readiness: {reason}")
        else:
            logger.info("Growth self-assessment: not ready. %s", reason)


def _set_stage(memory: MemoryManager, stage: str, reason: str) -> None:
    identity = memory.read("memory/core/identity.md")
    identity = identity.rstrip() + f"\nstage: {stage}\n"
    memory.write("memory/core/identity.md", identity)
    memory.update_world_state("stage", stage)
    memory.record_event(f"Stage transition to **{stage}**: {reason}")
    memory.record_experience(f"Transitioned to stage {stage}. Reason: {reason}")
    memory.record_decision(f"Stage transition to {stage}: {reason}")


def _run_self_test(memory: MemoryManager) -> dict:
    try:
        from self.editable.learning import run_self_test

        return run_self_test(memory)
    except Exception as exc:
        logger.error("Self-test module failed: %s", exc)
        return {"http_get": False, "api_call": False}


def _count_helpers(memory: MemoryManager) -> int:
    try:
        from self.editable.helpers import list_helpers

        return len(list_helpers(memory))
    except Exception:
        return 0


def _count_income(memory: MemoryManager) -> float:
    content = memory.read("finance/income.md")
    total = 0.0
    for line in content.splitlines():
        if "income:" not in line.lower():
            continue
        try:
            total += float(line.split("income:", 1)[1].split()[0])
        except (ValueError, IndexError):
            continue
    return total


# ---------------------------------------------------------------------------
# Baby-stage work
# ---------------------------------------------------------------------------
def _baby_work(memory: MemoryManager, model_client) -> None:
    """Curiosity-driven learning: the organism follows question chains.

    There is NO curriculum. The curiosity engine explores whatever its own
    frontier says is most valuable right now, spawns follow-up questions
    from every answer, reinforces productive chains, abandons dead ends,
    and periodically runs metacognition to find its own blind spots.
    Ambient trend sampling keeps it aware of the live internet.
    """
    try:
        from self.editable.curiosity import run_curiosity_cycle
        from self.editable.exploration import explore_trending, suggest_founder_tasks

        # The heart of learning: follow the curiosity chains.
        stats = run_curiosity_cycle(memory)
        memory.update_world_state("curiosity_frontier", str(stats))

        # Ambient awareness: a light sample of what the internet is doing
        # today (trends feed future curiosity, they do not direct it).
        explore_trending(memory, limit=1)

        task = suggest_founder_tasks(memory)
        if task:
            memory.append(
                "memory/world/state.md",
                f"pending_founder_task: {task}",
            )
    except Exception as exc:
        logger.error("Baby-stage work failed: %s", exc)


def _foundation_work(memory: MemoryManager, model_client) -> None:
    """Infrastructure: wallet, helpers, 30-day plan, escalated asks.

    Curiosity never stops: the organism keeps learning in every stage,
    only the balance between learning and building shifts.
    """
    try:
        from self.editable.curiosity import run_curiosity_cycle

        run_curiosity_cycle(memory)
    except Exception as exc:
        logger.error("Curiosity cycle failed in foundation: %s", exc)
    try:
        from self.editable.finance import ensure_wallet
        from self.editable.helpers import register_helper, should_spawn_helper
        from self.editable.escalation import EscalationManager

        ensure_wallet(memory)
        spawn = should_spawn_helper(memory)
        if spawn:
            register_helper(memory, spawn[0], spawn[1])
        escalation = EscalationManager(memory)
        escalation.request(
            "Human-assist tasks for foundation",
            "normal",
            "Ask founder to configure secrets and accounts",
        )
    except Exception as exc:
        logger.error("Foundation work failed: %s", exc)


def _growth_work(memory: MemoryManager, model_client) -> None:
    """Active operation: helpers, health watcher, income strategy focus.

    Learning continues for its entire existence — curiosity chains keep
    running alongside earning operations.
    """
    try:
        from self.editable.curiosity import run_curiosity_cycle

        run_curiosity_cycle(memory)
    except Exception as exc:
        logger.error("Curiosity cycle failed in growth: %s", exc)
    try:
        from self.editable.helpers import evaluate_helpers, run_helper_cycle
        from self.editable.strategies import choose_strategy

        terminated = evaluate_helpers(memory)
        for name in terminated:
            logger.info("Terminated underperforming helper: %s", name)
        focus = choose_strategy(memory)
        if focus:
            memory.append("memory/world/state.md", f"focus_strategy: {focus}")
        for name in _count_helper_names(memory):
            run_helper_cycle(memory, name, model_client)
    except Exception as exc:
        logger.error("Growth work failed: %s", exc)


def _count_helper_names(memory: MemoryManager) -> list:
    try:
        from self.editable.helpers import list_helpers

        return list_helpers(memory)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Daily report & housekeeping
# ---------------------------------------------------------------------------
def _request_missing_keys(memory: MemoryManager, communication_manager) -> None:
    """Ask the founder to add secrets for providers still waiting for keys.

    The organism grows beyond Gemini by registering discovered models in
    ``api_keys/providers.json``. For each registered provider whose key is
    not yet resolvable, it opens ONE encrypted issue asking the founder to
    add the key under the provider's env-var name. Requests are recorded in
    runtime state so the founder is never nagged twice for the same key.
    """
    from integrations import model_router

    status = model_router.brain_status()
    waiting = status.get("waiting_for_key", [])
    if not waiting:
        return

    state = memory.load_runtime_state()
    already_requested = set(state.get("key_requests_sent", []))
    newly_requested = []

    for entry in waiting:
        env_key = entry.get("env_key", "")
        name = entry.get("name", "?")
        if not env_key or env_key in already_requested:
            continue
        body = (
            f"I have discovered and registered a new model provider: '{name}'.\n\n"
            f"To let me use it, please add its API key to the repository "
            f"secrets under the exact name:\n\n    {env_key}\n\n"
            f"(Settings -> Secrets and variables -> Actions -> New repository "
            f"secret.)\n\n"
            f"I will pick it up automatically on my next wake — no code or "
            f"workflow change is needed. If you prefer that I not use this "
            f"provider, simply reply and I will disable it in my registry.\n\n"
            f"— {memory.read_identity().get('name', 'The Organism')}"
        )
        issue_number = communication_manager.ask_founder(
            f"API key request: {name} ({env_key})", body, labels=["founder", "api-key"]
        )
        if issue_number:
            newly_requested.append(env_key)
            memory.record_decision(
                f"Requested the founder to add secret {env_key} for provider '{name}' "
                f"(issue #{issue_number})."
            )
            logger.info("Asked the founder for key %s (provider '%s').", env_key, name)

    if newly_requested:
        state["key_requests_sent"] = sorted(already_requested | set(newly_requested))
        memory.save_runtime_state(state)


def _daily_report(memory: MemoryManager, communication_manager) -> None:
    """Compose and deliver the encrypted daily report (at most once per day).

    Without this guard the organism opened a brand-new report issue on
    every wake cycle — six issues a day — which spams the founder and
    burns API quota.
    """
    try:
        from self.editable.finance import financial_summary

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state = memory.load_runtime_state()
        if state.get("last_daily_report") == today:
            logger.info("Daily report already delivered today; skipping.")
            return

        identity = memory.read_identity()
        name = identity.get("name", "Organism")
        state = memory.load_runtime_state()
        finance = financial_summary(memory)
        health = memory.read("memory/world/state.md")
        health_line = next(
            (line for line in health.splitlines() if line.startswith("health:")),
            "health: unknown",
        )
        try:
            from self.editable.founder_relay import total_assistance_debt

            debt_line = f"- Assistance debt to founder: {total_assistance_debt(memory):.2f} USD (unpaid)\n"
        except Exception:
            debt_line = ""
        try:
            from self.editable.self_editing import last_edit_info

            edits = last_edit_info(memory).strip()
            edit_line = f"- Recent self-edits: {edits[-300:]}\n" if edits else ""
        except Exception:
            edit_line = ""
        report = (
            f"Daily report from {name}\n\n"
            f"- Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')} UTC\n"
            f"- Stage: {identity.get('stage', 'baby')}\n"
            f"- Run number: {state.get('run_number', '?')}\n"
            f"- {health_line}\n"
            f"- {finance}\n"
            f"{debt_line}"
            f"{edit_line}"
            f"- Loyalty: {loyalty.loyalty_statement()}\n"
        )
        report_dir = config.REPO_ROOT / config.REPORTS_DAILY_DIR
        report_dir.mkdir(parents=True, exist_ok=True)
        date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        memory.write(f"reports/daily/{date_stamp}.md", report)
        communication_manager.send_daily_report(report)
        state["last_daily_report"] = today
        memory.save_runtime_state(state)
        logger.info("Daily report written and delivered.")
    except Exception as exc:
        logger.error("Daily report failed: %s", exc)


def _housekeeping(memory: MemoryManager, github, selfmod) -> None:
    """Backups, timeline stamping and the daily git commit."""
    try:
        count = selfmod.backup_editable()
        memory.append_plaintext(
            "documentary/evolution.md",
            f"Daily housekeeping: backed up {count} editable files.",
        )
        _commit_and_push(github, "daily housekeeping (backups, reports, timeline)")
    except Exception as exc:
        logger.error("Housekeeping failed: %s", exc)


def _commit_and_push(github, message: str) -> None:
    """Commit all local changes and push to the default branch."""
    import subprocess

    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0:
            logger.info("No changes to commit.")
            return
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"{message} [{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}]",
                "--author",
                "The Organism <organism@localhost>",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        branch = config.git_branch()
        # Integrate any remote commits made since checkout (another run or
        # the founder) before pushing; a plain push would be rejected and
        # the failure swallowed silently.
        subprocess.run(
            ["git", "pull", "--rebase", "origin", branch],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        subprocess.run(
            ["git", "push", "origin", f"HEAD:{branch}"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        logger.info("Committed and pushed on %s.", branch)
    except Exception as exc:
        logger.error("Git commit/push failed: %s", exc)


# ---------------------------------------------------------------------------
# Main wake cycle
# ---------------------------------------------------------------------------
def wake() -> int:
    config.ensure_directories()
    logger = setup_logging(config.REPO_ROOT / config.LOG_FILE)

    logger.info("=" * 60)
    logger.info("The Organism waking. Run #%s", _run_number())

    missing = _missing_secrets()
    if missing:
        logger.warning("Secrets not yet configured: %s", ", ".join(missing))

    # --- Loyalty -----------------------------------------------------------
    logger.info("Loyalty: %s", loyalty.loyalty_statement())

    # --- Integration clients ------------------------------------------------
    from integrations.github_api import GitHubClient

    github = GitHubClient()

    # --- Kill switch (absolute, first operational check) --------------------
    try:
        if kill_switch.check_kill_switch(github, logger):
            logger.critical("Kill switch tripped. Halting.")
            # Intended shutdown, not a failure: exit 0 so the workflow does
            # not open a "wake cycle failed" issue every 4 hours forever.
            # The committed kill marker keeps every future run halted.
            return 0
    except Exception as exc:
        logger.critical("Kill switch check failed: %s — halting as fail-safe.", exc)
        return 1

    # --- Initialisation -----------------------------------------------------
    encryption = _init_encryption()
    memory = _init_memory(encryption)
    state = memory.load_runtime_state()
    state["run_number"] = _run_number()
    state["date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    memory.save_runtime_state(state)

    # --- Genesis snapshot & founder-ordered rebirth ---------------------------
    # The genesis snapshot (birth-state of every editable module) is written
    # exactly once; it is what a memory-preserving reset restores from.
    try:
        rebirth.ensure_genesis()
    except Exception as exc:
        logger.error("Genesis snapshot failed: %s", exc)
    try:
        reset_issue = rebirth.check_reset_request(github, logger)
        if reset_issue is not None:
            summary = rebirth.perform_rebirth(
                memory, f"founder opened a verified RESET issue (#{reset_issue})"
            )
            logger.critical("Rebirth executed: %s", summary)
            # Close the trigger issue FIRST — an open RESET issue would
            # re-trigger a rebirth on every future wake.
            try:
                github.comment_on_issue(
                    reset_issue,
                    "Rebirth complete. Memories, proven paths, my name and my "
                    "birthday all survived; behaviour restored to genesis, "
                    "stage back to baby. Closing this reset request.",
                )
                github.close_issue(reset_issue)
            except Exception as exc:
                logger.error("Could not close RESET issue #%s: %s", reset_issue, exc)
            _commit_and_push(github, "rebirth: behaviour reset to genesis, memory preserved")
            # Continue the wake cycle: a freshly reborn organism still
            # answers the founder and reports — it is a reset, not a death.
    except Exception as exc:
        logger.error("Rebirth check failed: %s", exc)

    # --- Model client -------------------------------------------------------
    # Consumers (identity, communication, helpers) call ``.complete(...)``,
    # so this must be an object exposing that method — not a bare function.
    # The router tries every registered provider in priority order: Gemini
    # is only the BIRTH brain, not a lifetime dependency. As the organism
    # discovers new models it registers them (api_keys/providers.json) and
    # asks the founder to add their keys to the secrets; the router picks
    # them up automatically on the next wake.
    from integrations import model_router

    class ModelClient:
        @staticmethod
        def complete(prompt: str, max_output_tokens: int = 1500) -> str:
            return model_router.complete(prompt, max_output_tokens=max_output_tokens)

        # Keep the object callable for any legacy call sites.
        def __call__(self, prompt: str, max_output_tokens: int = 1500) -> str:
            return self.complete(prompt, max_output_tokens=max_output_tokens)

    model_client = ModelClient()

    # Surface brain status (which providers are usable / waiting for keys).
    try:
        status = model_router.brain_status()
        logger.info(
            "Brain status: usable=%s, waiting_for_key=%s",
            status["usable"],
            [w["name"] for w in status["waiting_for_key"]],
        )
    except Exception as exc:
        logger.warning("Could not compute brain status: %s", exc)

    # --- Birth or normal operation ------------------------------------------
    if not identity_core.is_born(memory):
        if _birth_preconditions_met(github):
            try:
                _handle_birth(model_client, memory, encryption, github)
            except identity_core.BirthDeferred as exc:
                # Not an error: the brain was unreachable (outage/quota).
                # No partial state was written; the next wake retries birth.
                logger.warning("Birth deferred: %s", exc)
            except Exception as exc:
                logger.error("Birth ritual failed: %s", exc)
                logger.error(traceback.format_exc())
        else:
            logger.info("Waiting to be born. Configure the secrets and re-run.")
    else:
        identity = memory.read_identity()
        logger.info("Identity loaded: %s (stage %s)", identity.get("name", "?"), identity.get("stage", "?"))

    # --- Communication with the founder -------------------------------------
    from self.editable.communication import CommunicationManager
    from self.editable.self_modification import SelfModificationManager

    comms = CommunicationManager(github, memory, encryption)
    selfmod = SelfModificationManager(github, memory)

    try:
        answered = comms.process_founder_issues(model_client)
        if answered:
            logger.info("Answered founder issues: %s", answered)
    except Exception as exc:
        logger.error("Founder communication failed: %s", exc)

    # --- Ask the founder for keys of newly discovered providers ---------------
    # The organism registers models it discovers in api_keys/providers.json.
    # Whenever a registered provider is still waiting for its key, it asks
    # the founder (once per provider) to add the key to the secrets store.
    try:
        _request_missing_keys(memory, comms)
    except Exception as exc:
        logger.error("Key-request flow failed: %s", exc)

    # --- Collect founder relay results (paid-model assists) -------------------
    # If the founder pasted back an answer from a top-tier model (or
    # declined), settle the request: store the answer, book the assistance
    # debt, close the issue.
    try:
        from self.editable.founder_relay import collect_relay_results

        settled = collect_relay_results(memory, github, encryption)
        if settled:
            logger.info("Settled relay requests: %s", settled)
    except Exception as exc:
        logger.error("Relay collection failed: %s", exc)

    # --- Health checks -------------------------------------------------------
    from self.editable.health import run_health_checks, act_on_report, alert_founder

    healthy = True
    try:
        report = run_health_checks(memory, github)
        logger.info("Health report: %s", report.summary)
        healthy = act_on_report(memory, report, github)
        if not healthy:
            alert_founder(memory, report, comms)
            logger.warning("Hibernation mode: skipping non-essential work.")
    except Exception as exc:
        logger.error("Health checks failed: %s", exc)

    # --- Stage-specific work (skipped while hibernating) ---------------------
    if healthy:
        identity = memory.read_identity()
        stage = identity.get("stage", "baby")
        logger.info("Current stage: %s", stage)
        if stage == "baby":
            _baby_work(memory, model_client)
        elif stage == "foundation":
            _foundation_work(memory, model_client)
        elif stage == "growth":
            _growth_work(memory, model_client)
        elif stage == "running":
            _growth_work(memory, model_client)  # running reuses growth operations
        _stage_transition(memory, model_client)

        # --- Self-editing: the organism improves its own code ----------------
        # At most one verified edit per wake. Every candidate is compiled and
        # import-checked in isolation; failures are diagnosed, repaired once,
        # and reverted when still broken. Founder-queued edits take priority.
        try:
            from self.editable.self_editing import run_self_edit_cycle

            edit_result = run_self_edit_cycle(memory, model_client)
            if edit_result:
                logger.info(
                    "Self-edit %s: %s (%s)",
                    edit_result["outcome"], edit_result["path"], edit_result["goal"][:120],
                )
        except Exception as exc:
            logger.error("Self-edit cycle failed: %s", exc)
            # Founder's rule: copy the error and try solving it with a model.
            try:
                from self.editable.self_editing import diagnose_runtime_failure

                diagnose_runtime_failure(memory, model_client, traceback.format_exc())
            except Exception:
                pass

    # --- Self-modification approvals -----------------------------------------
    try:
        applied = selfmod.process_approvals(_apply_approved_change)
        if applied:
            logger.info("Applied %s approved self-modifications.", applied)
    except Exception as exc:
        logger.error("Self-modification processing failed: %s", exc)

    # --- Daily report ---------------------------------------------------------
    _daily_report(memory, comms)

    # --- Housekeeping ----------------------------------------------------------
    _housekeeping(memory, github, selfmod)

    # --- Completion ------------------------------------------------------------
    logger.info("Wake cycle complete. Sleeping until next run.")
    return 0


def _apply_approved_change(issue_number: int, rel_path: str, issue_body: str = "") -> None:
    """Apply a founder-approved change to a protected file.

    The proposal issue embeds the COMPLETE replacement content in a
    ```new-content``` fenced block. Only after the founder comments
    APPROVED does this callback run; it extracts the content, verifies the
    syntax for Python files, snapshots the old file, and writes the new
    one. Proposals without an embedded content block still require a
    manual pull request (that fact is logged).
    """
    marker = "```new-content"
    start = issue_body.find(marker)
    if start == -1:
        logger.info(
            "Approved change for %s (issue #%s) has no embedded content; "
            "the founder must apply it via a pull request.",
            rel_path,
            issue_number,
        )
        return
    payload = issue_body[start + len(marker):]
    end = payload.find("```")
    if end == -1:
        raise ValueError("new-content block is not terminated")
    new_content = payload[:end].strip("\n") + "\n"

    if rel_path.endswith(".py"):
        import ast

        ast.parse(new_content, filename=rel_path)  # raises on bad syntax

    # Resolve against config.REPO_ROOT (not the module-level constant) so
    # tests that redirect the root are honoured and writes stay contained.
    target = config.REPO_ROOT / rel_path
    if target.exists():
        # Snapshot the pre-change state so a bad approved change can be
        # recovered by the founder from git history AND a local backup.
        backup_dir = config.REPO_ROOT / "state" / "approved_change_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (backup_dir / f"{stamp}_{rel_path.replace('/', '_')}").write_text(
            target.read_text(encoding="utf-8"), encoding="utf-8"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content, encoding="utf-8")
    logger.info(
        "Approved change APPLIED to %s (issue #%s).", rel_path, issue_number
    )


if __name__ == "__main__":
    try:
        exit_code = wake()
    except Exception as exc:  # absolute last-resort guard
        try:
            logger = setup_logging(config.REPO_ROOT / config.LOG_FILE)
        except Exception:
            logger = logging.getLogger("organism.main")
        logger.critical("Unhandled fatal error: %s", exc)
        logger.critical(traceback.format_exc())
        exit_code = 2
    sys.exit(exit_code)