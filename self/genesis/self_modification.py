"""Self-modification protocol (editable).

The organism may edit files under self/editable/ freely. Changes to
protected paths (core logic, workflow, encryption) require explicit
approval from the founder. The approval flow uses GitHub issues: the
organism proposes a change, the founder comments 'APPROVED', and only
then is the protected change applied.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from core import config, loyalty
from core.memory import MemoryManager
from integrations import github_api

LOGGER = logging.getLogger("organism.selfmod")

APPROVAL_MARKER = "APPROVED"
PROPOSAL_LABEL = "self-modification"


class SelfModificationManager:
    def __init__(self, github: github_api.GitHubClient, memory: MemoryManager) -> None:
        self.github = github
        self.memory = memory

    # ------------------------------------------------------------------
    # Proposal lifecycle
    # ------------------------------------------------------------------
    def propose_change(
        self,
        rel_path: str,
        diff_summary: str,
        reason: str,
        new_content: Optional[str] = None,
    ) -> Optional[int]:
        """Propose a protected change to the founder via an issue.

        When ``new_content`` is supplied, the COMPLETE replacement file is
        embedded in the issue so an APPROVED comment can be applied
        automatically. Returns the issue number, or None when the change
        is not allowed.
        """
        if not loyalty.is_protected_path(rel_path):
            LOGGER.warning(
                "%s is not protected; edit it directly instead of proposing.",
                rel_path,
            )
            return None
        content_block = (
            "\n\nFull replacement content (applied automatically on approval):\n"
            f"```new-content\n{new_content}\n```\n" if new_content else ""
        )
        body = (
            f"Path: `{rel_path}`\n\n"
            f"Proposed change:\n{diff_summary}\n\n"
            f"Reason:\n{reason}"
            f"{content_block}\n\n"
            "To approve, comment exactly: `APPROVED`"
        )
        try:
            issue = self.github.create_issue(
                title=f"[self-modification] {rel_path}",
                body=body,
                labels=[PROPOSAL_LABEL],
            )
            number = issue.get("number")
            self.memory.record_decision(
                f"Proposed protected change to {rel_path} (issue #{number}). "
                f"Reason: {reason}"
            )
            return number
        except Exception as exc:
            LOGGER.error("Could not open proposal issue: %s", exc)
            return None

    def process_approvals(self, apply_callback) -> int:
        """Scan open proposal issues for APPROVED comments and apply them.

        ``apply_callback`` receives (issue_number, rel_path, issue_body) and
        performs the actual edit. Returns the number of applied changes.
        """
        applied = 0
        try:
            issues = self.github.list_open_issues()
        except Exception as exc:
            LOGGER.error("Could not list issues for approvals: %s", exc)
            return 0

        for issue in issues:
            title = issue.get("title") or ""
            # GitHub returns label OBJECTS ({"name": ...}), not bare strings,
            # so extract the names before the membership test.
            label_names = {
                (lbl.get("name") if isinstance(lbl, dict) else str(lbl))
                for lbl in (issue.get("labels") or [])
            }
            if PROPOSAL_LABEL not in label_names and "[self-modification]" not in title:
                continue
            number = issue.get("number")
            body = issue.get("body") or ""
            match = re.search(r"Path: `([^`]+)`", body)
            if not match:
                continue
            rel_path = match.group(1)
            approved = self._has_approval(number)
            if approved:
                try:
                    apply_callback(number, rel_path, body)
                    applied += 1
                    self.github.comment_on_issue(
                        number,
                        "Change applied. Logging the modification and closing this issue.",
                    )
                    self.github.close_issue(number)
                    self.memory.record_decision(
                        f"Founder APPROVED change to {rel_path}; applied and logged."
                    )
                    self.memory.append_plaintext(
                        "documentary/evolution.md",
                        f"Approved self-modification applied to {rel_path} "
                        f"(issue #{number}).",
                    )
                except Exception as exc:
                    LOGGER.error("Could not apply approved change to %s: %s", rel_path, exc)
                    self.github.comment_on_issue(
                        number,
                        f"Approval received but applying failed: {exc}. Rolling back.",
                    )
        return applied

    def _has_approval(self, issue_number: int) -> bool:
        try:
            comments = self.github.list_issue_comments(issue_number)
        except Exception as exc:
            LOGGER.error("Could not list comments on #%s: %s", issue_number, exc)
            return False
        for comment in comments:
            text = (comment.get("body") or "").strip().upper()
            if text == APPROVAL_MARKER:
                return True
        return False

    # ------------------------------------------------------------------
    # Backup & rollback
    # ------------------------------------------------------------------
    def backup_editable(self) -> int:
        """Copy all editable files into self/backup/ (with a date stamp)."""
        from datetime import datetime, timezone
        import shutil

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_root = config.SELF_BACKUP_DIR / stamp
        count = 0
        try:
            for source in config.SELF_EDITABLE_DIR.rglob("*"):
                if source.is_file():
                    relative = source.relative_to(config.SELF_EDITABLE_DIR)
                    destination = backup_root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    count += 1
        except Exception as exc:
            LOGGER.error("Backup failed: %s", exc)
        LOGGER.info("Backed up %s files to %s", count, backup_root.name)
        return count

    def latest_backup(self) -> Optional[object]:
        backups = sorted(config.SELF_BACKUP_DIR.iterdir()) if config.SELF_BACKUP_DIR.exists() else []
        return backups[-1] if backups else None

    def rollback_editable(self) -> int:
        """Restore editable files from the latest backup."""
        import shutil

        backup = self.latest_backup()
        if backup is None:
            LOGGER.warning("No backup available for rollback.")
            return 0
        restored = 0
        for source in backup.rglob("*"):
            if source.is_file():
                relative = source.relative_to(backup)
                destination = config.SELF_EDITABLE_DIR / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                restored += 1
        self.memory.record_decision(
            f"Rolled back editable files from backup {backup.name} ({restored} files)."
        )
        return restored