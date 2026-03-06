"""LLM-powered metadata enrichment for Lore memories."""

from lore.enrichment.llm import LLMClient
from lore.enrichment.pipeline import EnrichmentPipeline, EnrichmentResult

__all__ = ["EnrichmentPipeline", "EnrichmentResult", "LLMClient"]
