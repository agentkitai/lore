"""Git commit hook adapter — commit message normalization, webhook verification."""

from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from .base import NormalizedMessage, SourceAdapter
from ..normalize import normalize_content


class GitAdapter(SourceAdapter):
    adapter_name = "git"

    def __init__(self, webhook_secret: Optional[str] = None):
        self.webhook_secret = webhook_secret

    def verify(self, request_headers: dict, request_body: bytes) -> bool:
        """Verify GitHub/GitLab webhook via X-Hub-Signature-256 header."""
        if not self.webhook_secret:
            return True

        signature = request_headers.get("x-hub-signature-256", "")
        if not signature.startswith("sha256="):
            return False

        expected = "sha256=" + hmac.new(
            self.webhook_secret.encode(),
            request_body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    def normalize(self, payload: dict) -> NormalizedMessage:
        # Handle both GitHub webhook format and simple {message, author, sha} format
        commits = payload.get("commits", [payload])
        messages = []
        for commit in commits:
            msg = commit.get("message", "")
            if msg:
                messages.append(msg)

        first_commit = commits[0] if commits else {}
        author = first_commit.get("author", {})
        user = author.get("email") if isinstance(author, dict) else str(author)

        repo = payload.get("repository", {})
        if isinstance(repo, dict):
            repo_name = repo.get("full_name") or payload.get("repo", "")
        else:
            repo_name = payload.get("repo", "")

        return NormalizedMessage(
            content=normalize_content("\n\n".join(messages), "git_commit"),
            user=user,
            channel=str(repo_name),
            timestamp=first_commit.get("timestamp") or first_commit.get("date", ""),
            source_message_id=first_commit.get("id") or first_commit.get("sha", ""),
            raw_format="git_commit",
            memory_type="code",
            tags=["git-commit"],
        )
