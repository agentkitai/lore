"""Redaction pipeline orchestrator."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from lore.redact import patterns as P

# Type for custom patterns: (regex_string, label)
PatternDef = Tuple[str, str]


def _luhn_check(number: str) -> bool:
    """Validate a credit card number using the Luhn algorithm."""
    digits = [int(d) for d in number]
    # Process from right to left, doubling every second digit
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


class RedactionPipeline:
    """6-layer regex redaction pipeline.

    Compiles all patterns once at init. Call ``run(text)`` to redact.
    """

    def __init__(
        self,
        custom_patterns: Optional[List[PatternDef]] = None,
    ) -> None:
        # Credit card pattern (needs Luhn — runs before phone to avoid conflicts)
        self._cc_pattern = re.compile(P.CREDIT_CARD)

        # Other layers in order
        self._simple_layers: List[Tuple[re.Pattern[str], str]] = [
            (re.compile(P.API_KEY), "[REDACTED:api_key]"),
            (re.compile(P.EMAIL), "[REDACTED:email]"),
            (re.compile(P.PHONE), "[REDACTED:phone]"),
            (re.compile(P.IPV4), "[REDACTED:ip_address]"),
            (re.compile(P.IPV6), "[REDACTED:ip_address]"),
        ]

        # Layer 6: custom patterns
        self._custom_layers: List[Tuple[re.Pattern[str], str]] = []
        if custom_patterns:
            for pat_str, label in custom_patterns:
                self._custom_layers.append(
                    (re.compile(pat_str), f"[REDACTED:{label}]")
                )

    def run(self, text: str) -> str:
        """Apply all redaction layers to *text* and return cleaned version."""
        # Credit cards first (before phone, to avoid conflicts with spaced digits)
        text = self._cc_pattern.sub(self._cc_replacer, text)

        # API keys, emails, phones, IPs
        for pattern, replacement in self._simple_layers:
            text = pattern.sub(replacement, text)

        # Layer 6: custom
        for pattern, replacement in self._custom_layers:
            text = pattern.sub(replacement, text)

        return text

    @staticmethod
    def _cc_replacer(match: re.Match[str]) -> str:
        """Replace credit card numbers only if they pass Luhn check."""
        raw = match.group(0)
        digits_only = re.sub(r"[\s\-]", "", raw)
        if len(digits_only) < 13 or len(digits_only) > 19:
            return raw
        if _luhn_check(digits_only):
            return "[REDACTED:credit_card]"
        return raw


def redact(
    text: str,
    pipeline: Optional[RedactionPipeline] = None,
) -> str:
    """Convenience function: redact sensitive data from *text*."""
    if pipeline is None:
        pipeline = RedactionPipeline()
    return pipeline.run(text)
