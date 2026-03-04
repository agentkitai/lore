"""Edge case tests: unicode, long text, empty DB, special characters."""

import pytest

from lore import Lore
from lore.store.memory import MemoryStore


@pytest.fixture
def lore():
    store = MemoryStore()
    lo = Lore(store=store, redact=False)
    yield lo
    lo.close()


@pytest.fixture
def lore_redact():
    store = MemoryStore()
    lo = Lore(store=store, redact=True)
    yield lo
    lo.close()


class TestEmptyDB:
    def test_recall_empty_returns_empty(self, lore):
        results = lore.recall("anything")
        assert results == []

    def test_list_empty_returns_empty(self, lore):
        assert lore.list_memories() == []

    def test_get_nonexistent_returns_none(self, lore):
        assert lore.get("nonexistent") is None

    def test_forget_nonexistent_returns_false(self, lore):
        assert lore.forget("nonexistent") is False

    def test_stats_empty(self, lore):
        s = lore.stats()
        assert s.total == 0


class TestUnicode:
    def test_remember_unicode_content(self, lore):
        mid = lore.remember("API返回错误代码：429（请求过多）— 添加指数退避策略")
        memory = lore.get(mid)
        assert "429" in memory.content
        assert "指数退避" in memory.content

    def test_remember_emoji(self, lore):
        mid = lore.remember("🔥 Server on fire 🔥 — 🧯 Deploy fix ASAP 🚀")
        memory = lore.get(mid)
        assert "🔥" in memory.content
        assert "🚀" in memory.content

    def test_remember_arabic(self, lore):
        mid = lore.remember("خطأ في الاتصال بالخادم — أعد المحاولة بعد 30 ثانية")
        memory = lore.get(mid)
        assert "خطأ في الاتصال بالخادم" in memory.content

    def test_remember_mixed_scripts(self, lore):
        mid = lore.remember("Error in модуль авторизации for user テスト — Fix encoding UTF-8")
        memory = lore.get(mid)
        assert "модуль" in memory.content
        assert "テスト" in memory.content

    def test_recall_unicode(self, lore):
        lore.remember("日本語のテスト — 修正済み")
        results = lore.recall("日本語")
        assert len(results) >= 1


class TestLongText:
    def test_remember_very_long_content(self, lore):
        long_text = "x" * 100_000
        mid = lore.remember(long_text)
        memory = lore.get(mid)
        assert len(memory.content) == 100_000

    def test_recall_long_text(self, lore):
        lore.remember("test memory")
        long_query = "search " * 10_000
        results = lore.recall(long_query)
        assert isinstance(results, list)


class TestSpecialCharacters:
    def test_sql_injection_attempt(self, lore):
        mid = lore.remember("'; DROP TABLE memories; -- Bobby Tables strikes again")
        memory = lore.get(mid)
        assert "DROP TABLE" in memory.content

    def test_html_tags(self, lore):
        mid = lore.remember("<script>alert('xss')</script> — <b>sanitize</b> inputs")
        memory = lore.get(mid)
        assert "<script>" in memory.content

    def test_newlines_and_tabs(self, lore):
        mid = lore.remember("Error\non\nmultiple\nlines\n\t1. Do this\n\t2. Do that")
        memory = lore.get(mid)
        assert "\n" in memory.content
        assert "\t" in memory.content

    def test_null_bytes(self, lore):
        mid = lore.remember("Has null\x00byte content")
        memory = lore.get(mid)
        assert memory is not None

    def test_backslashes_and_quotes(self, lore):
        mid = lore.remember('Path is C:\\Users\\test\\"file" — Use raw strings r\'C:\\path\'')
        memory = lore.get(mid)
        assert "C:\\" in memory.content

    def test_empty_tags(self, lore):
        mid = lore.remember("test", tags=[])
        memory = lore.get(mid)
        assert memory.tags == []

    def test_tags_with_special_chars(self, lore):
        mid = lore.remember("test", tags=["rate-limit", "v2.0", "c++", "c#"])
        memory = lore.get(mid)
        assert "c++" in memory.tags
        assert "c#" in memory.tags


class TestConfidenceBoundaries:
    def test_confidence_zero(self, lore):
        mid = lore.remember("test", confidence=0.0)
        memory = lore.get(mid)
        assert memory.confidence == 0.0

    def test_confidence_one(self, lore):
        mid = lore.remember("test", confidence=1.0)
        memory = lore.get(mid)
        assert memory.confidence == 1.0

    def test_confidence_negative_raises(self, lore):
        with pytest.raises(ValueError):
            lore.remember("test", confidence=-0.1)

    def test_confidence_above_one_raises(self, lore):
        with pytest.raises(ValueError):
            lore.remember("test", confidence=1.1)


class TestRedactionEdgeCases:
    def test_redact_empty_string(self, lore_redact):
        mid = lore_redact.remember("")
        memory = lore_redact.get(mid)
        assert memory.content == ""

    def test_redact_only_pii(self, lore_redact):
        # PII (email) should be masked, not blocked
        mid = lore_redact.remember("test@example.com is a contact")
        memory = lore_redact.get(mid)
        assert "test@example.com" not in memory.content

    def test_block_api_key(self, lore_redact):
        # API keys trigger a block action (SecretBlockedError)
        from lore.exceptions import SecretBlockedError

        with pytest.raises(SecretBlockedError):
            lore_redact.remember("sk-abc123def456ghi789jkl012mno")
