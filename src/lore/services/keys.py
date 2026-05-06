"""Keys service — API key creation, listing, and revocation with cache invalidation."""

from __future__ import annotations

import hashlib
import secrets
from typing import Optional, Sequence, Tuple

from lore.persistence import (
    NewApiKey,
    Store,
    StoredApiKey,
)
from lore.persistence.exceptions import LastRootKeyError, StoreNotFoundError
from lore.server import auth


RAW_KEY_PREFIX = "lore_sk_"
_KEY_PREFIX_LEN = 12  # length of stored key_prefix (lore_sk_ + 4 hex chars)


def _generate_key() -> Tuple[str, str, str]:
    """Generate a fresh API key. Returns (raw_key, key_hash, key_prefix).

    Wire-compat: matches the existing routes/keys.py format —
    `lore_sk_` + 64 hex chars; key_prefix is the first 12 chars; SHA-256 of the raw key.
    """
    raw_key = RAW_KEY_PREFIX + secrets.token_hex(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:_KEY_PREFIX_LEN]
    return raw_key, key_hash, key_prefix


async def create_api_key(
    store: Store,
    *,
    org_id: str,
    name: str,
    project: Optional[str] = None,
    is_root: bool = False,
    workspace_id: Optional[str] = None,
) -> Tuple[StoredApiKey, str]:
    """Create a new API key, returning (stored_key, raw_key).

    The raw_key is only returned here — the caller must expose it to the user once.
    """
    raw_key, key_hash, key_prefix = _generate_key()
    new_key = NewApiKey(
        org_id=org_id,
        name=name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        project=project,
        is_root=is_root,
        workspace_id=workspace_id,
    )
    stored = await store.create_api_key(new_key)
    return stored, raw_key


async def list_api_keys(store: Store, org_id: str) -> Sequence[StoredApiKey]:
    """Return all API keys for *org_id*."""
    return await store.list_api_keys(org_id)


async def revoke_api_key(store: Store, key_id: str, org_id: str) -> None:
    """Revoke the API key identified by *key_id*, scoped to *org_id*.

    Raises:
        StoreNotFoundError: if the key does not exist or belongs to another org.
        LastRootKeyError: if the key is the last active root key for the org.
    """
    row = await store.get_api_key(key_id)
    if row is None or row.org_id != org_id:
        raise StoreNotFoundError("api_keys", key_id)

    if row.is_root and row.revoked_at is None:
        count = await store.count_active_root_keys(row.org_id)
        if count == 1:
            raise LastRootKeyError(
                f"Cannot revoke the last root key for org {row.org_id!r}"
            )

    await store.revoke_api_key(key_id)
    auth.invalidate_key(row.key_hash)
