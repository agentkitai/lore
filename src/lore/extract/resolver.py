"""Conflict resolution for extracted facts."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ulid import ULID

from lore.extract.extractor import ExtractedFact
from lore.store.base import Store
from lore.types import ConflictEntry, Fact, VALID_RESOLUTIONS

logger = logging.getLogger(__name__)


@dataclass
class ResolutionResult:
    """Result of resolving a batch of extracted facts."""

    saved_facts: List[Fact] = field(default_factory=list)
    conflicts: List[ConflictEntry] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)


class ConflictResolver:
    """Applies resolution strategies to extracted facts."""

    def __init__(self, store: Store, relationship_manager: Optional[object] = None) -> None:
        self._store = store
        self._relationship_manager = relationship_manager

    def resolve_all(
        self,
        extracted_facts: List[ExtractedFact],
        memory_id: str,
    ) -> ResolutionResult:
        """Resolve all extracted facts and persist results."""
        result = ResolutionResult(stats={
            "noop": 0, "supersede": 0, "merge": 0, "contradict": 0,
        })

        for ef in extracted_facts:
            resolution = ef.resolution.upper()
            if resolution not in VALID_RESOLUTIONS:
                logger.warning("Unknown resolution %r, treating as NOOP", ef.resolution)
                resolution = "NOOP"

            if resolution == "NOOP":
                self._apply_noop(ef, result)
            elif resolution == "SUPERSEDE":
                self._apply_supersede(ef, memory_id, result)
            elif resolution == "MERGE":
                self._apply_merge(ef, memory_id, result)
            elif resolution == "CONTRADICT":
                self._apply_contradict(ef, memory_id, result)

        return result

    def _apply_noop(self, ef: ExtractedFact, result: ResolutionResult) -> None:
        """NOOP: save fact, no conflict log."""
        self._store.save_fact(ef.fact)
        result.saved_facts.append(ef.fact)
        result.stats["noop"] += 1

    def _apply_supersede(
        self, ef: ExtractedFact, memory_id: str, result: ResolutionResult,
    ) -> None:
        """SUPERSEDE: invalidate old fact, save new, log conflict."""
        old_fact = ef.conflicting_fact
        if old_fact is not None:
            self._store.invalidate_fact(old_fact.id, invalidated_by=memory_id)
            if self._relationship_manager is not None:
                try:
                    self._relationship_manager.expire_relationship_for_fact(old_fact.id)
                except Exception:
                    logger.warning("Failed to expire graph edge for fact %s", old_fact.id)

        self._store.save_fact(ef.fact)
        result.saved_facts.append(ef.fact)

        conflict = ConflictEntry(
            id=str(ULID()),
            new_memory_id=memory_id,
            old_fact_id=old_fact.id if old_fact else "",
            new_fact_id=ef.fact.id,
            subject=ef.fact.subject,
            predicate=ef.fact.predicate,
            old_value=old_fact.object if old_fact else "",
            new_value=ef.fact.object,
            resolution="SUPERSEDE",
            resolved_at=datetime.now(timezone.utc).isoformat(),
            metadata={"reasoning": ef.reasoning} if ef.reasoning else None,
        )
        self._store.save_conflict(conflict)
        result.conflicts.append(conflict)
        result.stats["supersede"] += 1

    def _apply_merge(
        self, ef: ExtractedFact, memory_id: str, result: ResolutionResult,
    ) -> None:
        """MERGE: save new fact (old stays active), log conflict."""
        old_fact = ef.conflicting_fact

        self._store.save_fact(ef.fact)
        result.saved_facts.append(ef.fact)

        conflict = ConflictEntry(
            id=str(ULID()),
            new_memory_id=memory_id,
            old_fact_id=old_fact.id if old_fact else "",
            new_fact_id=ef.fact.id,
            subject=ef.fact.subject,
            predicate=ef.fact.predicate,
            old_value=old_fact.object if old_fact else "",
            new_value=ef.fact.object,
            resolution="MERGE",
            resolved_at=datetime.now(timezone.utc).isoformat(),
            metadata={"reasoning": ef.reasoning} if ef.reasoning else None,
        )
        self._store.save_conflict(conflict)
        result.conflicts.append(conflict)
        result.stats["merge"] += 1

    def _apply_contradict(
        self, ef: ExtractedFact, memory_id: str, result: ResolutionResult,
    ) -> None:
        """CONTRADICT: do NOT save new fact, log conflict with proposed fact in metadata."""
        old_fact = ef.conflicting_fact

        meta: Dict = {"reasoning": ef.reasoning} if ef.reasoning else {}
        meta["proposed_fact"] = {
            "subject": ef.fact.subject,
            "predicate": ef.fact.predicate,
            "object": ef.fact.object,
            "confidence": ef.fact.confidence,
        }

        conflict = ConflictEntry(
            id=str(ULID()),
            new_memory_id=memory_id,
            old_fact_id=old_fact.id if old_fact else "",
            new_fact_id=None,
            subject=ef.fact.subject,
            predicate=ef.fact.predicate,
            old_value=old_fact.object if old_fact else "",
            new_value=ef.fact.object,
            resolution="CONTRADICT",
            resolved_at=datetime.now(timezone.utc).isoformat(),
            metadata=meta,
        )
        self._store.save_conflict(conflict)
        result.conflicts.append(conflict)
        result.stats["contradict"] += 1
