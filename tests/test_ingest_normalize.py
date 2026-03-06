"""Tests for content normalization (F7-S1 AC4/AC5, S2 AC1-AC3, S3 AC1-AC2, S4 AC1)."""


from lore.ingest.normalize import MAX_CONTENT_LENGTH, normalize_content


class TestPlainTextNormalization:
    def test_empty_string(self):
        assert normalize_content("", "plain_text") == ""

    def test_whitespace_collapse(self):
        text = "hello\n\n\n\nworld"
        result = normalize_content(text, "plain_text")
        assert result == "hello\n\nworld"

    def test_horizontal_whitespace_collapse(self):
        text = "hello    world"
        result = normalize_content(text, "plain_text")
        assert result == "hello world"

    def test_zero_width_chars_removed(self):
        text = "hello\u200bworld\ufefftest"
        result = normalize_content(text, "plain_text")
        assert result == "helloworldtest"

    def test_length_limit(self):
        text = "a" * 15_000
        result = normalize_content(text, "plain_text")
        assert len(result) == MAX_CONTENT_LENGTH

    def test_strip_leading_trailing(self):
        text = "  hello  "
        result = normalize_content(text, "plain_text")
        assert result == "hello"


class TestSlackMrkdwn:
    def test_user_mentions(self):
        text = "<@U123ABC> said *bold* and _italic_ in <#C456|general>"
        result = normalize_content(text, "slack_mrkdwn")
        assert result == "@U123ABC said bold and italic in #general"

    def test_url_handling(self):
        text = "Check <https://example.com|this link> and <https://plain.com>"
        result = normalize_content(text, "slack_mrkdwn")
        assert result == "Check this link and https://plain.com"

    def test_code_block_stripping(self):
        text = "```python\nprint('hi')\n```"
        result = normalize_content(text, "slack_mrkdwn")
        assert "print('hi')" in result
        assert "```" not in result

    def test_inline_code(self):
        text = "use `code` here"
        result = normalize_content(text, "slack_mrkdwn")
        assert result == "use code here"

    def test_strikethrough(self):
        text = "~deleted~ text"
        result = normalize_content(text, "slack_mrkdwn")
        assert result == "deleted text"


class TestTelegramFormatting:
    def test_html_stripping(self):
        text = '<b>bold</b> and <a href="https://x.com">link</a>'
        result = normalize_content(text, "telegram_html")
        assert result == "bold and link"

    def test_markdown_stripping(self):
        text = "**bold** and __italic__ and ```\ncode\n```"
        result = normalize_content(text, "telegram_markdown")
        assert "bold" in result
        assert "italic" in result
        assert "code" in result
        assert "**" not in result
        assert "__" not in result
        assert "```" not in result


class TestGitCommitNormalization:
    def test_diff_stat_stripped(self):
        text = "feat: add auth\n\n 3 files changed, 10 insertions(+), 2 deletions(-)"
        result = normalize_content(text, "git_commit")
        assert "feat: add auth" in result
        assert "files changed" not in result

    def test_diff_hunks_stripped(self):
        text = "fix: bug\n@@ -10,3 +10,5 @@ def foo():\nsome context"
        result = normalize_content(text, "git_commit")
        assert "fix: bug" in result
        assert "@@" not in result

    def test_trailers_preserved(self):
        text = "feat: new\n\nSigned-off-by: Alice <a@b.com>\nCo-authored-by: Bob <b@c.com>"
        result = normalize_content(text, "git_commit")
        assert "Signed-off-by" in result
        assert "Co-authored-by" in result
