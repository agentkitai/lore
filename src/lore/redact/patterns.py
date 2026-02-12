"""Built-in regex patterns for the 6-layer redaction pipeline."""

from __future__ import annotations

# Layer 1: API keys — prefix-based
API_KEY = (
    r'\b(?:'
    r'sk-[A-Za-z0-9]{20,}'           # OpenAI
    r'|AKIA[A-Z0-9]{16}'             # AWS
    r'|ghp_[A-Za-z0-9]{36,}'         # GitHub PAT
    r'|gh[sor]_[A-Za-z0-9]{36,}'     # GitHub other tokens
    r'|xox[bp]-[A-Za-z0-9\-]{10,}'   # Slack
    r')\b'
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
