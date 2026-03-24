"""Remember command — store a new memory."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List

import lore.cli._helpers as _helpers


def cmd_remember(args: argparse.Namespace) -> None:
    from lore.exceptions import SecretBlockedError

    lore = _helpers._get_lore(args.db)
    tags: List[str] = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    metadata = None
    if args.metadata:
        metadata = json.loads(args.metadata)
    try:
        mid = lore.remember(
            content=args.content,
            type=args.type,
            tier=args.tier,
            context=getattr(args, "context", None),
            tags=tags,
            metadata=metadata,
            source=args.source,
            project=args.project,
            ttl=args.ttl,
            confidence=args.confidence,
        )
    except SecretBlockedError as exc:
        lore.close()
        print(f"Blocked: {exc.finding_type} detected — remove the secret and retry.", file=sys.stderr)
        sys.exit(1)
    lore.close()
    print(mid)
