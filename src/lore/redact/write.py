"""Write-side redaction: one helper + a config-driven redactor, applied to
every memory write that funnels through ``services.memories`` (server routes,
AsyncLore, observations, lessons, conversation/consolidation).

The sync ``Lore`` SDK already redacts in ``Lore.remember`` and keeps its own
redactor (secrets *block* by default — see tests/test_redact_integration.py);
it shares ``redact_for_write`` below but passes its own pipeline. The server
pass defaults to **mask + tag** (writes succeed, content masked, memory tagged)
with blocking opt-in, per the feature brief.
"""

from __future__ import annotations

import functools
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from lore.exceptions import SecretBlockedError
from lore.redact.pipeline import RedactionPipeline

logger = logging.getLogger(__name__)

# Finding types the pipeline blocks by default. The write-side pass masks them
# instead (so a paste of a token gets stored as [REDACTED:…] rather than
# rejected) unless LORE_REDACT_BLOCK is set.
_SECRET_TYPES = (
    "api_key",
    "jwt_token",
    "private_key",
    "aws_secret_key",
    "high_entropy_string",
    "secret",
)


def _load_denylist(path: Optional[str]) -> List[Tuple[str, str]]:
    """Read a newline-separated denylist file into ``(regex, label)`` patterns.

    Each non-empty, non-``#`` line is a literal term to redact (names, domains,
    …). Prefix a line with ``re:`` to supply a raw regex instead. Bad regexes
    and an unreadable file are logged and skipped — redaction degrades, never
    crashes a write.
    """
    if not path:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as e:
        logger.warning("LORE_REDACT_DENYLIST unreadable (%s): %s", path, e)
        return []
    out: List[Tuple[str, str]] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pat = line[3:] if line.startswith("re:") else re.escape(line)
        try:
            re.compile(pat)
        except re.error as e:
            logger.warning("Skipping invalid denylist pattern %r: %s", line, e)
            continue
        out.append((pat, "denylisted"))
    return out


@functools.lru_cache(maxsize=1)
def get_write_redactor() -> Optional[RedactionPipeline]:
    """Process-wide redactor for the write path, built once from env config:

      ``LORE_REDACT_DISABLED`` — set to disable redaction entirely (returns None).
      ``LORE_REDACT_DENYLIST`` — path to a denylist file (literal terms, or
                                 ``re:`` lines for regexes).
      ``LORE_REDACT_BLOCK``    — set to BLOCK writes containing secrets
                                 (default: mask + tag everything).

    Cached; tests that mutate the env must call ``get_write_redactor.cache_clear()``.
    """
    if os.environ.get("LORE_REDACT_DISABLED"):
        return None
    custom = _load_denylist(os.environ.get("LORE_REDACT_DENYLIST"))
    overrides: Dict[str, str] = {}
    if not os.environ.get("LORE_REDACT_BLOCK"):
        overrides = {t: "mask" for t in _SECRET_TYPES}
    return RedactionPipeline(
        custom_patterns=custom,
        security_action_overrides=overrides,  # type: ignore[arg-type]
    )


def redact_for_write(
    redactor: Optional[RedactionPipeline],
    content: Optional[str],
    context: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Dict]:
    """Scan + redact ``content``/``context`` before persistence.

    Returns ``(content, context, redaction_meta)``. Raises
    :class:`SecretBlockedError` when any finding's action is ``block``.
    ``redaction_meta`` is ``{}`` when nothing matched, else
    ``{'redacted': True, 'redacted_types': [...]}`` for tagging the memory.

    ``content``/``context`` may be None (partial update) — None/empty fields are
    skipped. A None ``redactor`` is a pass-through (redaction disabled).
    """
    if redactor is None:
        return content, context, {}
    types: List[str] = []

    if content:
        scan = redactor.scan(content)
        if scan.action == "block":
            raise SecretBlockedError(scan.blocked_types[0])
        if scan.findings:
            types.extend(f.type for f in scan.findings)
            content = scan.masked_text()

    if context:
        cscan = redactor.scan(context)
        if cscan.action == "block":
            raise SecretBlockedError(cscan.blocked_types[0])
        if cscan.findings:
            types.extend(f.type for f in cscan.findings)
            context = cscan.masked_text()

    meta: Dict = (
        {"redacted": True, "redacted_types": sorted(set(types))} if types else {}
    )
    return content, context, meta
