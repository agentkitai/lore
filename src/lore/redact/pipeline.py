"""Redaction pipeline orchestrator — 3-layer security scanning."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Set, Tuple

from lore.redact import patterns as P

logger = logging.getLogger(__name__)

# Type for custom patterns: (regex_string, label)
PatternDef = Tuple[str, str]

# Default actions per finding type
_DEFAULT_ACTIONS: Dict[str, Literal["mask", "block"]] = {
    # PII → mask
    "email": "mask",
    "phone": "mask",
    "ip_address": "mask",
    "credit_card": "mask",
    "person": "mask",
    "location": "mask",
    # Secrets → block
    "api_key": "block",
    "jwt_token": "block",
    "private_key": "block",
    "aws_secret_key": "block",
    "high_entropy_string": "block",
    "secret": "block",  # generic from detect-secrets L2
}

# Minimum entropy for high-entropy string detection
_ENTROPY_THRESHOLD = 4.5
_ENTROPY_MIN_LENGTH = 20


@dataclass
class Finding:
    """A single security finding."""

    type: str
    value: str  # the matched text (for position tracking, NOT for display)
    start: int
    end: int
    action: Literal["mask", "block"]


@dataclass
class ScanResult:
    """Result of a full security scan."""

    text: str  # original text
    findings: List[Finding] = field(default_factory=list)

    @property
    def action(self) -> Literal["mask", "block", "pass"]:
        """Overall action: block if any finding is block, else mask, else pass."""
        if not self.findings:
            return "pass"
        if any(f.action == "block" for f in self.findings):
            return "block"
        return "mask"

    @property
    def blocked_types(self) -> List[str]:
        """Types of findings that triggered block."""
        return [f.type for f in self.findings if f.action == "block"]

    def masked_text(self) -> str:
        """Return text with all findings replaced by [REDACTED:type] tokens."""
        if not self.findings:
            return self.text
        # Sort findings by start position, reversed so we can replace from end
        sorted_findings = sorted(self.findings, key=lambda f: f.start, reverse=True)
        result = self.text
        for f in sorted_findings:
            result = result[:f.start] + f"[REDACTED:{f.type}]" + result[f.end:]
        return result


def _luhn_check(number: str) -> bool:
    """Validate a credit card number using the Luhn algorithm."""
    digits = [int(d) for d in number]
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


class RedactionPipeline:
    """3-layer security scanning and redaction pipeline.

    Layer 1 (L1): Regex patterns — always active, fast
    Layer 2 (L2): detect-secrets — optional, entropy analysis
    Layer 3 (L3): SpaCy NER — optional, named entity recognition

    Call ``scan(text)`` for a full ScanResult, or ``run(text)`` for
    backward-compatible masked text output.
    """

    def __init__(
        self,
        custom_patterns: Optional[List[PatternDef]] = None,
        security_scan_levels: Optional[List[int]] = None,
        security_action_overrides: Optional[Dict[str, Literal["mask", "block"]]] = None,
    ) -> None:
        self._levels: Set[int] = set(security_scan_levels or [1])
        self._action_overrides = security_action_overrides or {}

        # L1: Compile regex patterns
        self._cc_pattern = re.compile(P.CREDIT_CARD)
        self._jwt_pattern = re.compile(P.JWT_TOKEN)
        self._private_key_pattern = re.compile(P.PRIVATE_KEY_BLOCK)
        self._aws_secret_pattern = re.compile(P.AWS_SECRET_KEY)

        self._simple_layers: List[Tuple[re.Pattern[str], str]] = [
            (re.compile(P.API_KEY), "api_key"),
            (re.compile(P.EMAIL), "email"),
            (re.compile(P.PHONE), "phone"),
            (re.compile(P.IPV4), "ip_address"),
            (re.compile(P.IPV6), "ip_address"),
        ]

        self._custom_layers: List[Tuple[re.Pattern[str], str]] = []
        if custom_patterns:
            for pat_str, label in custom_patterns:
                self._custom_layers.append((re.compile(pat_str), label))

        # L2: detect-secrets (lazy init)
        self._l2_available: Optional[bool] = None
        self._l2_scanner = None

        # L3: SpaCy NER (lazy init)
        self._l3_available: Optional[bool] = None
        self._l3_nlp = None

    def _get_action(self, finding_type: str) -> Literal["mask", "block"]:
        """Resolve action for a finding type, checking overrides first."""
        if finding_type in self._action_overrides:
            return self._action_overrides[finding_type]
        return _DEFAULT_ACTIONS.get(finding_type, "mask")

    def scan(self, text: str) -> ScanResult:
        """Run all enabled layers and return a ScanResult."""
        findings: List[Finding] = []

        # L1: Always run regex patterns
        if 1 in self._levels:
            findings.extend(self._scan_l1(text))

        # L2: detect-secrets
        if 2 in self._levels:
            findings.extend(self._scan_l2(text))

        # L3: SpaCy NER
        if 3 in self._levels:
            findings.extend(self._scan_l3(text))

        # Deduplicate overlapping findings (keep broader ones)
        findings = self._deduplicate_findings(findings)

        return ScanResult(text=text, findings=findings)

    def run(self, text: str) -> str:
        """Backward-compatible: scan and return masked text.

        Note: This does NOT raise on block — callers should use scan()
        directly if they need to handle block actions.
        """
        result = self.scan(text)
        return result.masked_text()

    # ------------------------------------------------------------------
    # L1: Regex patterns
    # ------------------------------------------------------------------

    def _scan_l1(self, text: str) -> List[Finding]:
        findings: List[Finding] = []

        # Credit cards (Luhn validated)
        for m in self._cc_pattern.finditer(text):
            raw = m.group(0)
            digits_only = re.sub(r"[\s\-]", "", raw)
            if 13 <= len(digits_only) <= 19 and _luhn_check(digits_only):
                findings.append(Finding(
                    type="credit_card", value=raw,
                    start=m.start(), end=m.end(),
                    action=self._get_action("credit_card"),
                ))

        # Simple patterns (API keys, emails, phones, IPs)
        for pattern, ftype in self._simple_layers:
            for m in pattern.finditer(text):
                findings.append(Finding(
                    type=ftype, value=m.group(0),
                    start=m.start(), end=m.end(),
                    action=self._get_action(ftype),
                ))

        # JWT tokens
        for m in self._jwt_pattern.finditer(text):
            findings.append(Finding(
                type="jwt_token", value=m.group(0),
                start=m.start(), end=m.end(),
                action=self._get_action("jwt_token"),
            ))

        # PEM private key blocks
        for m in self._private_key_pattern.finditer(text):
            findings.append(Finding(
                type="private_key", value=m.group(0),
                start=m.start(), end=m.end(),
                action=self._get_action("private_key"),
            ))

        # AWS secret keys (40-char base64 near AKIA)
        if "AKIA" in text:
            for m in self._aws_secret_pattern.finditer(text):
                val = m.group(0)
                # Skip if it's the AKIA key itself (already caught by API_KEY)
                if val.startswith("AKIA"):
                    continue
                findings.append(Finding(
                    type="aws_secret_key", value=val,
                    start=m.start(), end=m.end(),
                    action=self._get_action("aws_secret_key"),
                ))

        # High-entropy strings
        findings.extend(self._scan_entropy(text))

        # Custom patterns
        for pattern, ftype in self._custom_layers:
            for m in pattern.finditer(text):
                findings.append(Finding(
                    type=ftype, value=m.group(0),
                    start=m.start(), end=m.end(),
                    action=self._get_action(ftype),
                ))

        return findings

    def _scan_entropy(self, text: str) -> List[Finding]:
        """Detect high-entropy hex/base64 strings."""
        findings: List[Finding] = []
        # Match contiguous alphanumeric strings of 20+ chars
        for m in re.finditer(r'\b[A-Za-z0-9]{20,}\b', text):
            val = m.group(0)
            # Skip all-alpha lowercase (likely English words)
            if val.isalpha() and val.islower():
                continue
            # Must contain both letters and digits to be suspicious
            if not (any(c.isalpha() for c in val) and any(c.isdigit() for c in val)):
                continue
            if P.shannon_entropy(val) >= _ENTROPY_THRESHOLD:
                findings.append(Finding(
                    type="high_entropy_string", value=val,
                    start=m.start(), end=m.end(),
                    action=self._get_action("high_entropy_string"),
                ))
        return findings

    # ------------------------------------------------------------------
    # L2: detect-secrets
    # ------------------------------------------------------------------

    def _init_l2(self) -> bool:
        """Try to import detect-secrets. Returns True if available."""
        if self._l2_available is not None:
            return self._l2_available
        try:
            from detect_secrets.core.scan import scan_line  # noqa: F401
            from detect_secrets.settings import configure_settings_from_baseline

            # Initialize with default plugins
            configure_settings_from_baseline({"plugins_used": [
                {"name": "HexHighEntropyString"},
                {"name": "Base64HighEntropyString"},
                {"name": "KeywordDetector"},
            ]})
            self._l2_available = True
        except Exception:
            logger.info("detect-secrets not available — L2 scanning disabled")
            self._l2_available = False
        return self._l2_available

    def _scan_l2(self, text: str) -> List[Finding]:
        """Run detect-secrets line-by-line."""
        if not self._init_l2():
            return []

        findings: List[Finding] = []
        try:
            from detect_secrets.core.scan import scan_line

            offset = 0
            for line in text.split("\n"):
                for secret in scan_line(line):
                    # detect-secrets returns PotentialSecret objects
                    stype = secret.type
                    # Use the raw secret value if available
                    secret_val = secret.secret_value or line
                    start = text.find(secret_val, offset)
                    if start == -1:
                        start = offset
                    end = start + len(secret_val)
                    findings.append(Finding(
                        type="secret", value=secret_val,
                        start=start, end=end,
                        action=self._get_action("secret"),
                    ))
                offset += len(line) + 1  # +1 for newline
        except Exception as e:
            logger.warning("detect-secrets L2 scan failed: %s", e)
        return findings

    # ------------------------------------------------------------------
    # L3: SpaCy NER
    # ------------------------------------------------------------------

    def _init_l3(self) -> bool:
        """Try to import spacy and load en_core_web_sm."""
        if self._l3_available is not None:
            return self._l3_available
        try:
            import spacy
            self._l3_nlp = spacy.load("en_core_web_sm")
            self._l3_available = True
        except Exception:
            logger.info("spacy/en_core_web_sm not available — L3 NER disabled")
            self._l3_available = False
        return self._l3_available

    def _scan_l3(self, text: str) -> List[Finding]:
        """Run SpaCy NER to find person names and locations."""
        if not self._init_l3():
            return []

        findings: List[Finding] = []
        try:
            doc = self._l3_nlp(text)
            for ent in doc.ents:
                if ent.label_ == "PERSON":
                    findings.append(Finding(
                        type="person", value=ent.text,
                        start=ent.start_char, end=ent.end_char,
                        action=self._get_action("person"),
                    ))
                elif ent.label_ in ("GPE", "LOC"):
                    findings.append(Finding(
                        type="location", value=ent.text,
                        start=ent.start_char, end=ent.end_char,
                        action=self._get_action("location"),
                    ))
                # ORG entities are NOT masked (often needed in technical context)
        except Exception as e:
            logger.warning("SpaCy L3 scan failed: %s", e)
        return findings

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate_findings(findings: List[Finding]) -> List[Finding]:
        """Remove overlapping findings, keeping broader matches."""
        if len(findings) <= 1:
            return findings
        # Sort by start, then by span length descending
        findings.sort(key=lambda f: (f.start, -(f.end - f.start)))
        deduped: List[Finding] = []
        last_end = -1
        for f in findings:
            if f.start >= last_end:
                deduped.append(f)
                last_end = f.end
            # If overlapping but this one is a block and existing is mask, prefer block
            elif f.action == "block" and deduped and deduped[-1].action == "mask":
                deduped[-1] = f
                last_end = f.end
        return deduped


def redact(
    text: str,
    pipeline: Optional[RedactionPipeline] = None,
) -> str:
    """Convenience function: redact sensitive data from *text*."""
    if pipeline is None:
        pipeline = RedactionPipeline()
    return pipeline.run(text)
