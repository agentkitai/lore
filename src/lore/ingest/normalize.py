"""Content normalization — strip formatting, clean whitespace, enforce limits."""

from __future__ import annotations

import re

MAX_CONTENT_LENGTH = 10_000


def normalize_content(text: str, format: str = "plain_text") -> str:
    """Normalize content from various source formats to clean plain text.

    Pipeline:
    1. Strip source-specific formatting (mrkdwn, HTML, etc.)
    2. Collapse excessive whitespace
    3. Remove zero-width and invisible Unicode characters
    4. Trim to max content length
    5. Strip leading/trailing whitespace
    """
    if not text:
        return ""

    if format == "slack_mrkdwn":
        text = _strip_slack_mrkdwn(text)
    elif format in ("telegram_html", "telegram_markdown"):
        text = _strip_telegram_formatting(text, format)
    elif format == "git_commit":
        text = _normalize_git_message(text)

    text = _collapse_whitespace(text)
    text = _strip_invisible_chars(text)
    text = text[:MAX_CONTENT_LENGTH].strip()
    return text


def _strip_slack_mrkdwn(text: str) -> str:
    """Convert Slack mrkdwn to plain text."""
    # User mentions: <@U123ABC> -> @U123ABC
    text = re.sub(r"<@([A-Z0-9]+)>", r"@\1", text)
    # Channel references: <#C123|channel-name> -> #channel-name
    text = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", text)
    # URLs: <http://example.com|label> -> label, <http://example.com> -> http://example.com
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    # Bold, italic, strikethrough
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"~([^~]+)~", r"\1", text)
    # Code blocks: ```code``` -> code
    text = re.sub(r"```[^\n]*\n?(.*?)```", r"\1", text, flags=re.DOTALL)
    # Inline code: `code` -> code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def _strip_telegram_formatting(text: str, format: str = "telegram_html") -> str:
    """Strip Telegram HTML/Markdown entities to plain text."""
    # HTML tags: <a href="...">link</a> -> link, then strip remaining tags
    text = re.sub(r'<a\s+href="[^"]*">([^<]*)</a>', r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    # Markdown bold/italic (only for telegram_markdown format, but safe to apply always)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    # Code blocks
    text = re.sub(r"```[^\n]*\n?(.*?)```", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def _normalize_git_message(text: str) -> str:
    """Normalize git commit message — keep subject + body, strip diff hunks."""
    # Remove diff-stat lines (e.g., " 3 files changed, 10 insertions(+)")
    text = re.sub(r"^\s*\d+ files? changed.*$", "", text, flags=re.MULTILINE)
    # Remove diff hunks (@@...@@)
    text = re.sub(r"^@@.*@@.*$", "", text, flags=re.MULTILINE)
    # Remove diff +/- lines
    text = re.sub(r"^[+-]{3}\s.*$", "", text, flags=re.MULTILINE)
    return text


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of whitespace (preserving single newlines)."""
    # Collapse multiple blank lines into one
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse horizontal whitespace
    text = re.sub(r"[^\S\n]+", " ", text)
    return text


def _strip_invisible_chars(text: str) -> str:
    """Remove zero-width characters and other invisible Unicode."""
    text = re.sub(r"[\u200b\u200c\u200d\ufeff\u00ad\u2060\u180e]", "", text)
    return text
