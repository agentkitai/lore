"""Embedding engine for Lore SDK."""

from lore.embed.base import Embedder
from lore.embed.local import CODE_MODEL, PROSE_MODEL, LocalEmbedder, make_code_embedder
from lore.embed.router import EmbeddingRouter, detect_content_type

__all__ = [
    "Embedder",
    "LocalEmbedder",
    "EmbeddingRouter",
    "detect_content_type",
    "make_code_embedder",
    "PROSE_MODEL",
    "CODE_MODEL",
]
