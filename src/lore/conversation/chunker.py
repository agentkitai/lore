"""Token-aware chunking for long conversations."""

from __future__ import annotations

from typing import List

from lore.types import ConversationMessage


class ConversationChunker:
    """Split conversations into chunks that fit within token limits."""

    def __init__(self, max_tokens: int = 8000, overlap_messages: int = 2) -> None:
        self.max_tokens = max_tokens
        self.overlap_messages = overlap_messages

    def chunk(self, messages: List[ConversationMessage]) -> List[List[ConversationMessage]]:
        """Split messages into chunks that fit within token limits.

        Uses simple word-count heuristic (1 token ~ 0.75 words).
        Overlap: last N messages of chunk i are prepended to chunk i+1
        for context continuity.
        """
        total_tokens = self._estimate_tokens(messages)
        if total_tokens <= self.max_tokens:
            return [messages]

        chunks: List[List[ConversationMessage]] = []
        current_chunk: List[ConversationMessage] = []
        current_tokens = 0

        for msg in messages:
            msg_tokens = self._estimate_message_tokens(msg)
            if current_chunk and current_tokens + msg_tokens > self.max_tokens:
                chunks.append(current_chunk)
                # Overlap: carry last N messages into next chunk
                overlap = current_chunk[-self.overlap_messages:] if self.overlap_messages > 0 else []
                current_chunk = list(overlap)
                current_tokens = self._estimate_tokens(current_chunk)
            current_chunk.append(msg)
            current_tokens += msg_tokens

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _estimate_tokens(self, messages: List[ConversationMessage]) -> int:
        """Estimate token count for a list of messages."""
        return sum(self._estimate_message_tokens(m) for m in messages)

    @staticmethod
    def _estimate_message_tokens(msg: ConversationMessage) -> int:
        """Estimate tokens for a single message (word_count / 0.75)."""
        text = f"[{msg.role}]: {msg.content}"
        word_count = len(text.split())
        return int(word_count / 0.75)
