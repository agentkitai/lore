"""Owner-only authz for destructive temporal ops (#71).

supersede / consolidate mark a source memory superseded, so — like delete (#69)
— only the owner (or an unowned row, or solo/no-principal mode) may do it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

pytest.importorskip("fastapi")

from lore.server.routes.temporal import _owner_can_mutate


@dataclass
class _Mem:
    user_id: str | None


@pytest.mark.parametrize(
    "owner,principal,expected",
    [
        ("alice", "alice", True),   # owner acts on own row
        ("alice", "bob", False),    # teammate may NOT supersede/consolidate alice's row
        (None, "bob", True),        # unowned row is fair game (fail-open, legacy/solo/bg)
        ("alice", None, True),      # solo / no-principal mode → unfiltered
        (None, None, True),         # solo + unowned
    ],
)
def test_owner_can_mutate(owner, principal, expected):
    assert _owner_can_mutate(_Mem(user_id=owner), principal) is expected
