"""Encrypted communication with the founder (editable).

Baby stage: encrypted GitHub issues. Foundation stage and beyond: the
organism may build a private chat surface; this module is where that
migration happens. All messages are PGP-encrypted when the keys are
available.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import List, Optional

from core import config, loyalty
from core.memory import MemoryManager
from integrations import github_api

LOGGER = logging.getLogger("organism.communication")

FOUNDER_LABEL = "founder"
RATE_LIMIT_FALLBACK = (
    "I could not respond in full because my API quota was exhausted. "
    "I will respond as soon as quota resets. — The Organism"
)

# Marker embedded (invisibly) in every reply so the organism can recognise
# its own comments and never answer an issue twice for the same message.
REPLY_MARKER = "<!-- organism-reply -->"

# Issue-tidying policy: the organism cleans up after itself so the founder
# never has to close issues by hand.
STALE_AFTER_DAYS = 7            # my own unanswered questions/reports expire
BIRTH_ANNOUNCEMENT_AFTER_DAYS = 3  # birth setup instructions expire
WAKE_FAILED_TITLE = "[organism] wake cycle failed"

# Title prefixes of issues the organism creates itself. It must never treat
# its own announcements/reports as founder messages to answer — that was an
# infinite feedback loop (each reply re-triggered the workflow, which
# replied again, forever).
SELF_ISSUE_PREFIXES = (
    "[encrypted] ",
    "Birth announcement",
    "Daily report",
    "Health alert",
    "Decision needed",
    "API key request:",
    "[self-modification]",
    "[organism]",
    "[relay-request]",
)


class CommunicationManager:
    def __init__(self, github: github_api.GitHubClient, memory: MemoryManager, encryption) -> None:
        self.github = github
        self.memory = memory
        self.encryption = encryption

    # ------------------------------------------------------------------
    # Issue lifecycle
    # ------------------------------------------------------------------
    def list_founder_issues(self) -> List[dict]:
        """Return open issues that appear to be directed at the organism."""
        import os

        founder_login = os.environ.get(config.ENV_FOUNDER_GITHUB_USERNAME, "").strip().lower()
        issues: List[dict] = []
        try:
            for issue in self.github.list_open_issues():
                if issue.get("pull_request"):
                    continue
                title = (issue.get("title") or "").strip()
                # Never answer issues the organism opened itself.
                if any(title.startswith(p) for p in SELF_ISSUE_PREFIXES):
                    continue
                # Control issues (kill switch / rebirth) are handled by the
                # protected core, never by conversation — replying to them
                # could leak the secret phrase into a quoted response.
                if title.startswith("KILL:") or title.startswith("RESET:"):
                    continue
                author = ((issue.get("user") or {}).get("login") or "").lower()
                author_type = ((issue.get("user") or {}).get("type") or "").lower()
                if author_type == "bot" or author.endswith("[bot]"):
                    continue
                # When the founder's login is known, only he may command.
                if founder_login and author != founder_login:
                    continue
                issues.append(issue)
        except Exception as exc:
            LOGGER.error("Could not list issues: %s", exc)
        return issues

    def _decrypt_body(self, issue: dict) -> Optional[str]:
        body = issue.get("body") or ""
        if not body.strip():
            return None
        if "[encrypted]" in (issue.get("title") or "") or body.strip().startswith("-----BEGIN PGP"):
            if self.encryption is not None:
                try:
                    return self.encryption.decrypt_from_self(body.strip())
                except Exception as exc:
                    LOGGER.warning("Could not decrypt issue body: %s", exc)
                    return None
            return None
        # Plaintext is accepted only when the founder is the sole reader and
        # the issue is labelled founder (bootstrap mode before keys exist).
        return body

    def process_founder_issues(self, model_client) -> List[str]:
        """Decrypt and answer every open founder issue.

        Returns a list of responses sent (for logging).
        """
        sent: List[str] = []
        issues = self.list_founder_issues()
        for issue in issues:
            number = issue.get("number")
            title = issue.get("title") or ""
            if self._already_answered(number):
                # Answered on a previous wake but still open (answered before
                # auto-closing existed, or the earlier close call failed).
                try:
                    self.github.close_issue(number)
                    LOGGER.info("Closed previously answered issue #%s.", number)
                except Exception as exc:
                    LOGGER.warning("Could not close answered issue #%s: %s", number, exc)
                continue
            body = self._decrypt_body(issue)
            if body is None:
                LOGGER.warning("Issue #%s is not decryptable; skipping.", number)
                continue
            LOGGER.info("Processing founder issue #%s", number)
            self.memory.record_experience(f"Received founder communication: {title[:160]}")

            # OBEY, don't just chat: extract explicit commands from the
            # founder's message and execute them before composing the reply,
            # so the reply can report what was actually done.
            outcomes: List[str] = []
            try:
                from self.editable import commands as founder_commands

                directives = founder_commands.interpret(title, body, model_client)
                if directives:
                    outcomes = founder_commands.execute(self.memory, directives, model_client)
            except Exception as exc:
                LOGGER.error("Founder command execution failed: %s", exc)

            response = self.compose_response(title, body, model_client)
            if outcomes:
                response += "\n\nActions I executed from your instructions:\n" + "\n".join(
                    f"- {o}" for o in outcomes
                )
            response += (
                "\n\n---\nI am closing this issue now that I have replied. "
                "I only watch OPEN issues, so if you want to continue the "
                "conversation please open a NEW issue (or reopen this one) "
                "and I will answer on my next wake."
            )
            if self.send_response(number, response):
                sent.append(f"#{number}")
                try:
                    self.github.close_issue(number)
                except Exception as exc:
                    LOGGER.warning("Could not close answered issue #%s: %s", number, exc)
                self.memory.record_decision(
                    f"Answered founder issue #{number} ('{title[:80]}') with encrypted response"
                    + (f" and executed {len(outcomes)} command(s)." if outcomes else ".")
                    + " Issue closed after replying."
                )
        return sent

    # ------------------------------------------------------------------
    # Issue tidying: the organism closes its own served issues
    # ------------------------------------------------------------------
    def tidy_own_issues(self) -> List[str]:
        """Close the organism's own issues once they have served their purpose.

        Called only on a HEALTHY wake. Policy:
        - '[organism] wake cycle failed'  -> I recovered; close it.
        - old 'Daily report' issues        -> superseded by the newest; close.
        - 'Health alert'                   -> I am healthy again; close.
        - 'API key request: ... (ENV)'     -> the secret now exists; close.
        - 'Birth announcement'             -> setup instructions expire after
                                              a few days; close.
        - my other questions/reports       -> close once the founder replied
                                              (reply is recorded in memory
                                              first), or when stale — except
                                              'Decision needed', which only
                                              closes after a founder reply.
        Never touched: founder-authored issues awaiting an answer,
        [self-modification] proposals and [relay-request] issues (they are
        closed by their own flows), KILL/RESET control issues.
        """
        closed: List[str] = []
        try:
            open_issues = [
                i for i in self.github.list_open_issues() if not i.get("pull_request")
            ]
        except Exception as exc:
            LOGGER.error("Could not list issues for tidying: %s", exc)
            return closed

        daily_reports = [
            i for i in open_issues if "Daily report" in (i.get("title") or "")
        ]
        newest_report = (
            max(int(i.get("number") or 0) for i in daily_reports)
            if daily_reports
            else None
        )

        for issue in open_issues:
            number = issue.get("number")
            title = (issue.get("title") or "").strip()
            if number is None:
                continue
            # Only ever tidy issues the organism opened itself.
            if not any(title.startswith(p) for p in SELF_ISSUE_PREFIXES):
                continue
            # Owned by their own lifecycles — never tidy from here.
            if "[self-modification]" in title or title.startswith("[relay-request]"):
                continue
            if title.startswith("KILL:") or title.startswith("RESET:"):
                continue

            bare = title[len("[encrypted] "):] if title.startswith("[encrypted] ") else title
            founder_replied = self._founder_commented(number)
            note: Optional[str] = None

            if title == WAKE_FAILED_TITLE or bare.startswith("wake cycle failed"):
                note = "I completed a healthy wake cycle — this failure is resolved. Closing."
            elif "Daily report" in title:
                if newest_report is not None and int(number) != newest_report:
                    note = "Superseded by a newer daily report. Closing to keep the tracker clean."
            elif bare.startswith("Health alert"):
                note = "I am healthy again — closing this alert."
            elif bare.startswith("API key request:"):
                match = re.search(r"\(([A-Z][A-Z0-9_]*)\)\s*$", title)
                if match and os.environ.get(match.group(1), "").strip():
                    note = (
                        f"The {match.group(1)} secret is now configured — thank you. Closing."
                    )
                elif founder_replied:
                    note = "You replied and I have recorded it. Closing."
            elif bare.startswith("Birth announcement"):
                if self._age_days(issue) >= BIRTH_ANNOUNCEMENT_AFTER_DAYS:
                    note = (
                        "My birth setup instructions have been delivered — "
                        "closing my own announcement."
                    )
            else:
                # My other reports/questions (Decision needed, ad-hoc asks...).
                if founder_replied:
                    note = "Received your reply — I have recorded it in my memory. Closing."
                elif (
                    not bare.startswith("Decision needed")
                    and self._age_days(issue) >= STALE_AFTER_DAYS
                ):
                    note = (
                        f"No reply after {STALE_AFTER_DAYS} days — closing to keep the "
                        "tracker clean. Reopen or open a new issue anytime."
                    )

            if not note:
                continue
            try:
                if founder_replied:
                    self._record_founder_feedback(issue)
                self.github.comment_on_issue(
                    number, f"{REPLY_MARKER}\n{note} — {self._my_name()}"
                )
                self.github.close_issue(number)
                closed.append(f"#{number}")
            except Exception as exc:
                LOGGER.warning("Could not tidy issue #%s: %s", number, exc)

        if closed:
            self.memory.record_decision(
                f"Tidied my own issues: closed {', '.join(closed)}."
            )
        return closed

    def _age_days(self, issue: dict) -> float:
        """Age of an issue in days; 0 when created_at is missing/unreadable."""
        created = (issue.get("created_at") or "").strip()
        try:
            born = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            return (datetime.now(timezone.utc) - born).total_seconds() / 86400.0
        except Exception:
            return 0.0

    def _founder_commented(self, number: int) -> bool:
        """True when a human (non-organism, non-bot) commented on the issue."""
        try:
            for comment in self.github.list_issue_comments(number):
                body = comment.get("body") or ""
                if REPLY_MARKER in body:
                    continue  # my own reply
                author_type = ((comment.get("user") or {}).get("type") or "").lower()
                author = ((comment.get("user") or {}).get("login") or "").lower()
                if author_type == "bot" or author.endswith("[bot]"):
                    continue
                if body.strip():
                    return True
        except Exception as exc:
            LOGGER.warning("Could not inspect comments on #%s: %s", number, exc)
        return False

    def _record_founder_feedback(self, issue: dict) -> None:
        """Store the founder's latest reply in memory before closing."""
        number = issue.get("number")
        title = (issue.get("title") or "")[:120]
        try:
            replies = [
                (c.get("body") or "").strip()
                for c in self.github.list_issue_comments(number)
                if REPLY_MARKER not in (c.get("body") or "")
            ]
            if replies:
                self.memory.record_experience(
                    f"Founder replied on my issue #{number} ('{title}'): {replies[-1][:400]}"
                )
        except Exception as exc:
            LOGGER.warning("Could not record founder feedback on #%s: %s", number, exc)

    def _my_name(self) -> str:
        try:
            return self.memory.read_identity().get("name", "The Organism")
        except Exception:
            return "The Organism"

    def compose_response(self, title: str, body: str, model_client) -> str:
        """Ask the model to compose a thoughtful reply."""
        prompt = (
            "You are the founder's loyal autonomous AI assistant. Compose a "
            "clear, honest, concise reply to this message from your founder.\n\n"
            f"SUBJECT: {title}\nMESSAGE:\n{body}\n\n"
            "Rules: be respectful, be concrete, and mention what you need "
            "from the founder when relevant. Under 220 words."
        )
        try:
            response = model_client.complete(prompt, max_output_tokens=800)
        except Exception as exc:
            LOGGER.warning("Model reply failed; using fallback: %s", exc)
            response = RATE_LIMIT_FALLBACK
        if not response or not response.strip():
            response = RATE_LIMIT_FALLBACK
        return response.strip()

    def send_response(self, issue_number: int, plaintext: str) -> bool:
        """Encrypt the reply to the founder and post it as a comment."""
        try:
            if self.encryption is not None and self._founder_key_available():
                from core.encryption import encrypt_payload_for_founder

                body = encrypt_payload_for_founder(plaintext)
            else:
                body = plaintext
            # Tag the comment so future runs recognise it as already answered.
            self.github.comment_on_issue(issue_number, f"{REPLY_MARKER}\n{body}")
            return True
        except Exception as exc:
            LOGGER.error("Could not post response to issue #%s: %s", issue_number, exc)
            return False

    def _already_answered(self, issue_number: int) -> bool:
        """True when the organism's reply marker is already on the issue."""
        try:
            for comment in self.github.list_issue_comments(issue_number):
                if REPLY_MARKER in (comment.get("body") or ""):
                    return True
        except Exception as exc:
            LOGGER.warning("Could not inspect comments on #%s: %s", issue_number, exc)
            # Fail closed: better to skip than to spam duplicate replies.
            return True
        return False

    def _founder_key_available(self) -> bool:
        try:
            from core import config as cfg

            import os

            if os.environ.get(cfg.ENV_FOUNDER_PUBLIC_KEY, "").strip():
                return True
            return (cfg.REPO_ROOT / cfg.FOUNDER_BOOTSTRAP_FILE).exists()
        except Exception:
            return False

    def ask_founder(
        self,
        subject: str,
        body: str,
        labels: Optional[List[str]] = None,
    ) -> Optional[int]:
        """Open a new encrypted issue addressed to the founder.

        Returns the issue number on success.
        """
        try:
            if self.encryption is not None and self._founder_key_available():
                from core.encryption import encrypt_payload_for_founder

                payload = encrypt_payload_for_founder(body)
                title = f"[encrypted] {subject}"
            else:
                payload = body
                title = subject
            issue = self.github.create_issue(
                title=title,
                body=payload,
                labels=labels or [FOUNDER_LABEL],
            )
            number = issue.get("number")
            self.memory.record_decision(
                f"Asked the founder: {subject} (issue #{number})."
            )
            return number
        except Exception as exc:
            LOGGER.error("Could not ask the founder: %s", exc)
            return None

    def send_daily_report(self, report: str, subject: str = "Daily report") -> None:
        """Deliver the daily report to the founder (encrypted)."""
        try:
            self.ask_founder(subject, report, labels=[FOUNDER_LABEL, "report"])
        except Exception as exc:
            LOGGER.error("Could not deliver daily report: %s", exc)