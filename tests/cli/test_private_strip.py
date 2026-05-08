"""Phase 6G T3: ``strip_private`` redaction helper.

Contract: the unredacted text never reaches anything past the hook
boundary. The helper:
* strips balanced ``<private>...</private>`` blocks (case-insensitive,
  multi-line, multiple blocks);
* fails closed on an unbalanced opening tag (strips to end-of-string);
* leaves text without any tags untouched.
"""

from __future__ import annotations

from lore.cli.commands._project import strip_private


def test_balanced_single_block_is_removed():
    text = "before <private>SECRET</private> after"
    assert strip_private(text) == "before  after"


def test_multiple_balanced_blocks_all_stripped():
    text = (
        "alpha <private>S1</private> beta "
        "<private>S2</private> gamma"
    )
    out = strip_private(text)
    assert "S1" not in out
    assert "S2" not in out
    assert "alpha" in out and "beta" in out and "gamma" in out


def test_unbalanced_opening_tag_strips_to_end_of_string():
    text = "kept <private>this and everything after leaks unless we fail-closed"
    out = strip_private(text)
    assert out == "kept "
    assert "leaks" not in out
    assert "<private>" not in out


def test_multiline_block_is_stripped():
    text = (
        "first line\n"
        "<private>\n"
        "  multi-line secret\n"
        "  with newlines\n"
        "</private>\n"
        "last line"
    )
    out = strip_private(text)
    assert "multi-line secret" not in out
    assert "first line" in out
    assert "last line" in out


def test_no_tags_returns_text_unchanged():
    text = "no tags here, just plain prose."
    assert strip_private(text) == text


def test_empty_block_collapses_to_nothing():
    text = "wrap[<private></private>]wrap"
    assert strip_private(text) == "wrap[]wrap"


def test_case_insensitive_tag_matching():
    text = "x <PRIVATE>SECRET</PRIVATE> y"
    out = strip_private(text)
    assert "SECRET" not in out
    assert out == "x  y"


def test_empty_string_passes_through():
    assert strip_private("") == ""


def test_unbalanced_after_balanced_still_fails_closed():
    """A balanced block followed by an unbalanced one: fail-closed wins."""
    text = "a <private>ok</private> b <private>leak"
    out = strip_private(text)
    assert "leak" not in out
    assert out == "a  b "
