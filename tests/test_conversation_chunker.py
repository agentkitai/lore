"""Unit tests for ConversationChunker."""

from __future__ import annotations

from lore.conversation.chunker import ConversationChunker
from lore.types import ConversationMessage


def _make_message(content: str, role: str = "user") -> ConversationMessage:
    return ConversationMessage(role=role, content=content)


class TestConversationChunker:
    def test_short_conversation_no_chunk(self):
        chunker = ConversationChunker(max_tokens=8000)
        messages = [_make_message("Hello world")]
        chunks = chunker.chunk(messages)
        assert len(chunks) == 1
        assert chunks[0] == messages

    def test_long_conversation_chunks(self):
        chunker = ConversationChunker(max_tokens=100, overlap_messages=2)
        # Create messages that together exceed 100 tokens
        messages = [_make_message(f"This is message number {i} " * 10) for i in range(10)]
        chunks = chunker.chunk(messages)
        assert len(chunks) > 1
        # All messages appear in at least one chunk
        all_contents = set()
        for chunk in chunks:
            for msg in chunk:
                all_contents.add(msg.content)
        for msg in messages:
            assert msg.content in all_contents

    def test_overlap_messages(self):
        chunker = ConversationChunker(max_tokens=100, overlap_messages=2)
        messages = [_make_message(f"Message {i} " * 10) for i in range(10)]
        chunks = chunker.chunk(messages)
        if len(chunks) >= 2:
            # Last 2 messages of chunk 0 should appear at start of chunk 1
            last_two = chunks[0][-2:]
            first_two = chunks[1][:2]
            assert last_two[0].content == first_two[0].content
            assert last_two[1].content == first_two[1].content

    def test_single_huge_message(self):
        chunker = ConversationChunker(max_tokens=100)
        # One message that is way over the token limit
        huge = _make_message("word " * 1000)
        chunks = chunker.chunk([huge])
        # Should still work — returned as a single chunk
        assert len(chunks) == 1
        assert chunks[0][0].content == huge.content

    def test_no_overlap_when_zero(self):
        chunker = ConversationChunker(max_tokens=100, overlap_messages=0)
        messages = [_make_message(f"Message {i} " * 10) for i in range(10)]
        chunks = chunker.chunk(messages)
        # Total messages across chunks should equal original count
        total = sum(len(c) for c in chunks)
        assert total == len(messages)
