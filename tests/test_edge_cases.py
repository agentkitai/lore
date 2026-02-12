"""Edge case tests: unicode, long text, empty DB, special characters."""

import pytest

from lore import Lore
from lore.store.memory import MemoryStore


@pytest.fixture
def lore():
    store = MemoryStore()
    l = Lore(store=store, redact=False)
    yield l
    l.close()


@pytest.fixture
def lore_redact():
    store = MemoryStore()
    l = Lore(store=store, redact=True)
    yield l
    l.close()


class TestEmptyDB:
    def test_query_empty_returns_empty(self, lore):
        results = lore.query("anything")
        assert results == []

    def test_list_empty_returns_empty(self, lore):
        assert lore.list() == []

    def test_get_nonexistent_returns_none(self, lore):
        assert lore.get("nonexistent") is None

    def test_delete_nonexistent_returns_false(self, lore):
        assert lore.delete("nonexistent") is False

    def test_export_empty(self, lore):
        assert lore.export_lessons() == []

    def test_import_empty_list(self, lore):
        assert lore.import_lessons(data=[]) == 0


class TestUnicode:
    def test_publish_unicode_problem(self, lore):
        lid = lore.publish(
            problem="API返回错误代码：429（请求过多）",
            resolution="添加指数退避策略",
        )
        lesson = lore.get(lid)
        assert "429" in lesson.problem
        assert "指数退避" in lesson.resolution

    def test_publish_emoji(self, lore):
        lid = lore.publish(
            problem="🔥 Server on fire 🔥",
            resolution="🧯 Deploy fix ASAP 🚀",
        )
        lesson = lore.get(lid)
        assert "🔥" in lesson.problem
        assert "🚀" in lesson.resolution

    def test_publish_arabic(self, lore):
        lid = lore.publish(
            problem="خطأ في الاتصال بالخادم",
            resolution="أعد المحاولة بعد 30 ثانية",
        )
        lesson = lore.get(lid)
        assert lesson.problem == "خطأ في الاتصال بالخادم"

    def test_publish_mixed_scripts(self, lore):
        lid = lore.publish(
            problem="Error in модуль авторизации for user テスト",
            resolution="Fix encoding — использовать UTF-8",
        )
        lesson = lore.get(lid)
        assert "модуль" in lesson.problem
        assert "テスト" in lesson.problem

    def test_query_unicode(self, lore):
        lore.publish(problem="日本語のテスト", resolution="修正済み")
        results = lore.query("日本語")
        assert len(results) >= 1


class TestLongText:
    def test_publish_very_long_problem(self, lore):
        long_text = "x" * 100_000
        lid = lore.publish(problem=long_text, resolution="short fix")
        lesson = lore.get(lid)
        assert len(lesson.problem) == 100_000

    def test_publish_very_long_resolution(self, lore):
        long_text = "Step 1. " * 20_000
        lid = lore.publish(problem="complex issue", resolution=long_text)
        lesson = lore.get(lid)
        assert len(lesson.resolution) == len(long_text)

    def test_query_long_text(self, lore):
        lore.publish(problem="test", resolution="test")
        long_query = "search " * 10_000
        results = lore.query(long_query)
        # Should not crash; may or may not find results
        assert isinstance(results, list)


class TestSpecialCharacters:
    def test_sql_injection_attempt(self, lore):
        lid = lore.publish(
            problem="'; DROP TABLE lessons; --",
            resolution="Bobby Tables strikes again",
        )
        lesson = lore.get(lid)
        assert "DROP TABLE" in lesson.problem

    def test_html_tags(self, lore):
        lid = lore.publish(
            problem="<script>alert('xss')</script>",
            resolution="<b>sanitize</b> inputs",
        )
        lesson = lore.get(lid)
        assert "<script>" in lesson.problem

    def test_newlines_and_tabs(self, lore):
        lid = lore.publish(
            problem="Error\non\nmultiple\nlines",
            resolution="Fix:\n\t1. Do this\n\t2. Do that",
        )
        lesson = lore.get(lid)
        assert "\n" in lesson.problem
        assert "\t" in lesson.resolution

    def test_null_bytes(self, lore):
        lid = lore.publish(
            problem="Has null\x00byte",
            resolution="Strip\x00nulls",
        )
        lesson = lore.get(lid)
        assert lesson is not None

    def test_backslashes_and_quotes(self, lore):
        lid = lore.publish(
            problem='Path is C:\\Users\\test\\"file"',
            resolution="Use raw strings r'C:\\path'",
        )
        lesson = lore.get(lid)
        assert "C:\\" in lesson.problem

    def test_empty_tags(self, lore):
        lid = lore.publish(
            problem="test", resolution="test", tags=[]
        )
        lesson = lore.get(lid)
        assert lesson.tags == []

    def test_tags_with_special_chars(self, lore):
        lid = lore.publish(
            problem="test",
            resolution="test",
            tags=["rate-limit", "v2.0", "c++", "c#"],
        )
        lesson = lore.get(lid)
        assert "c++" in lesson.tags
        assert "c#" in lesson.tags


class TestConfidenceBoundaries:
    def test_confidence_zero(self, lore):
        lid = lore.publish(problem="test", resolution="test", confidence=0.0)
        lesson = lore.get(lid)
        assert lesson.confidence == 0.0

    def test_confidence_one(self, lore):
        lid = lore.publish(problem="test", resolution="test", confidence=1.0)
        lesson = lore.get(lid)
        assert lesson.confidence == 1.0

    def test_confidence_negative_raises(self, lore):
        with pytest.raises(ValueError):
            lore.publish(problem="test", resolution="test", confidence=-0.1)

    def test_confidence_above_one_raises(self, lore):
        with pytest.raises(ValueError):
            lore.publish(problem="test", resolution="test", confidence=1.1)


class TestRedactionEdgeCases:
    def test_redact_empty_string(self, lore_redact):
        lid = lore_redact.publish(problem="", resolution="")
        lesson = lore_redact.get(lid)
        assert lesson.problem == ""

    def test_redact_only_pii(self, lore_redact):
        lid = lore_redact.publish(
            problem="test@example.com",
            resolution="sk-abc123def456ghi789jkl012mno",
        )
        lesson = lore_redact.get(lid)
        assert "test@example.com" not in lesson.problem
        assert "sk-abc123" not in lesson.resolution
