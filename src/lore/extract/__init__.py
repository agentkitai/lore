"""Fact extraction and conflict resolution."""

from lore.extract.extractor import ExtractedFact, FactExtractor
from lore.extract.resolver import ConflictResolver, ResolutionResult

__all__ = ["FactExtractor", "ExtractedFact", "ConflictResolver", "ResolutionResult"]
