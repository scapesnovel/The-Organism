"""GitHub API client for The Organism.

Implements the subset of the GitHub REST API the organism needs:
issues, issue comments, commits, trees, branches, contents, and secrets.
All requests use the token from the environment; the token is never logged.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from core import config

LOGGER = logging.getLogger("organism.github")

API_BASE = "https://api.github.com"


class GitHubError(RuntimeError):
    """Raised for GitHub API failures."""


class GitHubClient:
    def __init__(self, token: Optional[str] = None, repo: Optional[str] = None) -> None:
        self.token = token or os.environ.get(config.ENV_GITHUB_TOKEN) or os.environ.get(config.ENV_GH_TOKEN) or ""
        self.repo = repo or os.environ.get(config.ENV_REPOSITORY, "")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    # ------------------------------------------------------------------
    # Low level
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        for attempt in range(4):
            try:
                response = self.session.request(method, url, timeout=60, **kwargs)
            except requests.RequestException as exc:
                LOGGER.warning("GitHub request failed (attempt %s): %s", attempt, exc)
                time.sleep(3 * (attempt + 1))
                continue

            if response.status_code in (429, 502, 503):
                delay = 10 * (attempt + 1)
                LOGGER.warning("GitHub rate-limited (%s); sleeping %ss", response.status_code, delay)
                time.sleep(delay)
                continue

            if response.status_code >= 400:
                detail = response.text[:400]
                if response.status_code in (401, 403):
                    LOGGER.error(
                        "GitHub authorization problem (%s): %s",
                        response.status_code,
                        detail,
                    )
                raise GitHubError(f"GitHub {method} {path} -> {response.status_code}: {detail}")

            if response.status_code == 204 or not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise GitHubError(f"GitHub returned non-JSON for {path}: {exc}") from exc
        raise GitHubError(f"GitHub request failed after retries: {method} {path}")

    def _paginate(self, path: str, per_page: int = 100) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            data = self._request("GET", f"{path}{separator}per_page={per_page}&page={page}")
            if not isinstance(data, list):
                break
            items.extend(data)
            if len(data) < per_page:
                break
            page += 1
        return items

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------
    def list_open_issues(self) -> List[Dict[str, Any]]:
        return self._paginate(f"/repos/{self.repo}/issues?state=open")

    def get_issue(self, number: int) -> Dict[str, Any]:
        return self._request("GET", f"/repos/{self.repo}/issues/{number}")

    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        return self._request("POST", f"/repos/{self.repo}/issues", json=payload)

    def comment_on_issue(self, number: int, body: str) -> Dict[str, Any]:
        return self._request("POST", f"/repos/{self.repo}/issues/{number}/comments", json={"body": body})

    def list_issue_comments(self, number: int) -> List[Dict[str, Any]]:
        return self._paginate(f"/repos/{self.repo}/issues/{number}/comments")

    def close_issue(self, number: int) -> Dict[str, Any]:
        return self._request("PATCH", f"/repos/{self.repo}/issues/{number}", json={"state": "closed"})

    # ------------------------------------------------------------------
    # Contents, commits, branches
    # ------------------------------------------------------------------
    def get_file(self, path: str, ref: str = "HEAD") -> Optional[Dict[str, Any]]:
        try:
            return self._request(
                "GET",
                f"/repos/{self.repo}/contents/{quote(path)}?ref={quote(ref)}",
            )
        except GitHubError as exc:
            if "404" in str(exc):
                return None
            raise

    def get_file_content(self, path: str, ref: str = "HEAD") -> Optional[str]:
        data = self.get_file(path, ref)
        if not data:
            return None
        try:
            return base64.b64decode(data.get("content", "")).decode("utf-8")
        except Exception:
            return None

    def list_commits(self, path: Optional[str] = None, per_page: int = 30) -> List[Dict[str, Any]]:
        # (was: a stray '&' when no path was given plus a blind [:200] slice
        # that could truncate the URL mid-parameter)
        suffix = f"?path={quote(path)}" if path else ""
        return self._paginate(f"/repos/{self.repo}/commits{suffix}", per_page=per_page)

    def latest_commit_sha(self, branch: str = "main") -> str:
        data = self._request("GET", f"/repos/{self.repo}/commits/{quote(branch)}")
        return data.get("sha", "")

    def get_branch(self, branch: str = "main") -> Dict[str, Any]:
        return self._request("GET", f"/repos/{self.repo}/branches/{quote(branch)}")

    def create_branch(self, branch: str, base_sha: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{self.repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )

    def create_blob(self, content: str, encoding: str = "utf-8") -> str:
        payload = {"content": content}
        if encoding == "base64":
            payload["encoding"] = "base64"
        data = self._request("POST", f"/repos/{self.repo}/git/blobs", json=payload)
        return data.get("sha", "")

    def create_tree(self, base_tree: str, entries: List[Dict[str, Any]]) -> str:
        data = self._request(
            "POST",
            f"/repos/{self.repo}/git/trees",
            json={"base_tree": base_tree, "tree": entries},
        )
        return data.get("sha", "")

    def create_commit(self, message: str, tree_sha: str, parents: List[str]) -> str:
        data = self._request(
            "POST",
            f"/repos/{self.repo}/git/commits",
            json={"message": message, "tree": tree_sha, "parents": parents},
        )
        return data.get("sha", "")

    def update_ref(self, branch: str, commit_sha: str) -> Dict[str, Any]:
        return self._request(
            "PATCH",
            f"/repos/{self.repo}/git/refs/heads/{quote(branch)}",
            json={"sha": commit_sha, "force": False},
        )

    # ------------------------------------------------------------------
    # Pull requests
    # ------------------------------------------------------------------
    def create_pull_request(self, title: str, head: str, base: str, body: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{self.repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )

    def list_open_pull_requests(self) -> List[Dict[str, Any]]:
        return self._paginate(f"/repos/{self.repo}/pulls?state=open")

    def get_pull_request(self, number: int) -> Dict[str, Any]:
        return self._request("GET", f"/repos/{self.repo}/pulls/{number}")

    def merge_pull_request(self, number: int) -> Dict[str, Any]:
        return self._request(
            "PUT",
            f"/repos/{self.repo}/pulls/{number}/merge",
            json={"merge_method": "squash"},
        )

    # ------------------------------------------------------------------
    # Secrets (repository-level; requires a PAT with secrets scope)
    # ------------------------------------------------------------------
    def get_public_key(self) -> Optional[Dict[str, Any]]:
        try:
            return self._request("GET", f"/repos/{self.repo}/actions/secrets/public-key")
        except GitHubError:
            return None

    def create_or_update_secret(self, name: str, value: str) -> bool:
        """Create or update a repository secret using libsodium sealing.

        Requires a fine-grained PAT (or classic PAT) with the 'secrets'
        permission for this repository. The secret value is sealed with the
        repository's public key, so it never travels in plaintext.
        """
        pub = self.get_public_key()
        if not pub:
            return False
        try:
            import nacl.bindings  # type: ignore
            from nacl import encoding  # type: ignore
        except ImportError:
            LOGGER.warning(
                "PyNaCl is not installed; cannot create GitHub secrets. "
                "Ask the founder to set %s manually.", name,
            )
            return False

        key_id = pub.get("key_id", "")
        raw_key = base64.b64decode(pub.get("key", ""))
        sealed = nacl.bindings.crypto_box_seal(value.encode("utf-8"), raw_key)
        payload = {
            "encrypted_value": base64.b64encode(sealed).decode("ascii"),
            "key_id": key_id,
        }
        self._request("PUT", f"/repos/{self.repo}/actions/secrets/{name}", json=payload)
        return True

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def whoami(self) -> str:
        data = self._request("GET", "/user")
        return data.get("login", "unknown")

    def can_reach_repo(self) -> bool:
        """Health probe that works with BOTH token types.

        The built-in Actions GITHUB_TOKEN cannot call /user (403), so the
        health check must probe something a repository-scoped installation
        token is allowed to read: the repository itself.
        """
        if not self.repo:
            return False
        try:
            data = self._request("GET", f"/repos/{self.repo}")
            return bool(data.get("full_name"))
        except GitHubError:
            return False

    def get_user_public_key_from_github(self, username: str) -> Optional[str]:
        """Try to fetch a user's PGP public key from their GitHub profile.

        GitHub exposes PGP keys at /users/<name>/gpg_keys. This is used only
        as a convenience bootstrap when the founder has not provided his key
        via the FOUNDER_PUBLIC_KEY secret.
        """
        try:
            keys = self._paginate(f"/users/{quote(username)}/gpg_keys")
            for key in keys:
                armored = key.get("public_key", "")
                if armored:
                    return armored
        except Exception as exc:
            LOGGER.warning("Could not fetch founder PGP key from GitHub: %s", exc)
        return None