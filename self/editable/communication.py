"""Encrypted communication with the founder (editable).

Baby stage: encrypted GitHub issues. Foundation stage and beyond: the
organism may build a private chat surface; this module is where that
migration happens. All messages are PGP-encrypted when the keys are
available.
"""

from __future__ import annotations

import logging
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
        issues: List[dict] = []
        try:
            for issue in self.github.list_open_issues():
                if issue.get("pull_request"):
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
            body = self._decrypt_body(issue)
            if body is None:
                LOGGER.warning("Issue #%s is not decryptable; skipping.", number)
                continue
            LOGGER.info("Processing founder issue #%s", number)
            self.memory.record_experience(f"Received founder communication: {title[:160]}")

            response = self.compose_response(title, body, model_client)
            if self.send_response(number, response):
                sent.append(f"#{number}")
                self.memory.record_decision(
                    f"Answered founder issue #{number} ('{title[:80]}') with encrypted response."
                )
        return sent

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
            self.github.comment_on_issue(issue_number, body)
            return True
        except Exception as exc:
            LOGGER.error("Could not post response to issue #%s: %s", issue_number, exc)
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