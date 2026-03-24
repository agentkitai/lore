"""PII redaction enrichment plugin — regex-based pattern detection."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# Patterns: (label, compiled regex)
_PII_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")),
    ("phone_us", re.compile(
        r"(?<!\d)"                              # no leading digit
        r"(?:\+?1[-.\s]?)?"                     # optional country code
        r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"  # (NNN) NNN-NNNN variants
        r"(?!\d)"                                # no trailing digit
    )),
    ("ssn", re.compile(
        r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"
    )),
    ("credit_card", re.compile(
        r"(?<!\d)"
        r"\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}"
        r"(?!\d)"
    )),
    ("ip_address", re.compile(
        r"(?<!\d)"
        r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)"
        r"(?!\d)"
    )),
]


class PIIRedactorPlugin:
    """Detect and optionally redact PII patterns in memory content."""

    name: str = "pii_redactor"

    def __init__(self, *, redact: bool = False) -> None:
        self._redact = redact

    def enrich(self, content: str, metadata: dict) -> dict:
        """Return metadata with detected PII types (and redacted content if enabled)."""
        detected: Dict[str, int] = {}
        redacted = content

        for label, pattern in _PII_PATTERNS:
            matches = pattern.findall(content)
            if matches:
                detected[label] = len(matches)
                if self._redact:
                    redacted = pattern.sub(f"[{label.upper()}_REDACTED]", redacted)

        result: dict = {}
        if detected:
            result["pii_detected"] = detected
        if self._redact and detected:
            result["redacted_content"] = redacted
        return result
