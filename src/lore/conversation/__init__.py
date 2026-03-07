"""Conversation auto-extract pipeline (v0.8.0).

Accepts raw conversation messages and automatically extracts
salient memories using LLM processing.
"""

from lore.conversation.extractor import ConversationExtractor
from lore.types import ConversationJob, ConversationMessage

__all__ = ["ConversationExtractor", "ConversationJob", "ConversationMessage"]
