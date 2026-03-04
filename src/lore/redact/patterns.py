"""Built-in regex patterns for the redaction pipeline."""

from __future__ import annotations

import math

# Layer 1: API keys — prefix-based
API_KEY = (
    r'\b(?:'
    r'sk-[A-Za-z0-9]{20,}'           # OpenAI
    r'|AKIA[A-Z0-9]{16}'             # AWS access key ID
    r'|ghp_[A-Za-z0-9]{36,}'         # GitHub PAT
    r'|gh[sor]_[A-Za-z0-9]{36,}'     # GitHub other tokens
    r'|xox[bp]-[A-Za-z0-9\-]{10,}'   # Slack
    r')\b'
)

# JWT tokens (three base64url-encoded segments separated by dots)
JWT_TOKEN = r'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'

# PEM private key blocks
PRIVATE_KEY_BLOCK = r'-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+|ENCRYPTED\s+)?PRIVATE\s+KEY-----'

# AWS secret access keys (40-char base64 near an AKIA access key ID)
AWS_SECRET_KEY = (
    r'(?<![A-Za-z0-9/+=])'
    r'[A-Za-z0-9/+=]{40}'
    r'(?![A-Za-z0-9/+=])'
)


def shannon_entropy(s: str) -> float:
    """Compute Shannon entropy of a string in bits."""
    if not s:
        return 0.0
    length = len(s)
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    return -sum(
        (count / length) * math.log2(count / length)
        for count in freq.values()
    )

# Layer 2: Email addresses
EMAIL = r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'

# Layer 3: Phone numbers (international formats)
PHONE = (
    r'(?<!\d)'                         # not preceded by digit
    r'(?:'
    r'\+\d{1,3}[\s\-]?'               # international prefix
    r')?'
    r'(?:'
    r'\(\d{2,4}\)[\s\-]?'             # area code in parens
    r'|\d{2,4}[\s\-]'                 # area code without parens
    r')'
    r'\d{3,4}[\s\-]?\d{3,4}'          # rest of number
    r'(?!\d)'                          # not followed by digit
)

# Layer 4: IP addresses (v4 and v6)
IPV4 = (
    r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}'
    r'(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
)

IPV6 = (
    r'(?:'
    r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b'          # full
    r'|'
    r'\b(?:[0-9a-fA-F]{1,4}:){1,7}:'                           # compressed trailing
    r'|'
    r'::(?:[0-9a-fA-F]{1,4}:){0,6}[0-9a-fA-F]{1,4}\b'        # compressed leading
    r'|'
    r'\b(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}\b'       # compressed middle (1 gap)
    r'|'
    r'::1\b'                                                    # loopback
    r')'
)

# Layer 5: Credit card numbers — matched broadly, validated with Luhn
# Matches 13-19 digit sequences optionally separated by spaces or dashes
CREDIT_CARD = (
    r'\b'
    r'\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{1,7}'
    r'\b'
)
