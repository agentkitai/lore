"""Phase 6G — capture-extract prompt builder tests.

Covers the additive Phase 6G behavior of ``_build_extraction_prompt``:

* ``kind:"prompt"`` buffer entries render as ``User said: "..."``.
* Prompt slice with ≥1 prompt → REQUIRED intent-emission directive.
* Prompt slice with 0 prompts → SKIP intent-emission directive.
* ``Project: <resolved>`` appears in the rendered prompt.
* Per-observation ``scope`` directive is present.
* The literal resolved project string is the value the subagent is told
  to pass on every ``mcp__lore__remember_observation`` call.

The legacy ``_build_prompt`` shim is exercised by ``tests/test_capture_hook.py``;
those assertions still pass since we render legacy JSONL lines verbatim.
"""

from __future__ import annotations

from lore.cli.commands import capture as cap


# ── Buffer-entry rendering ───────────────────────────────────────────


class TestRenderBufferEntry:
    def test_prompt_kind_renders_as_user_said(self):
        out = cap._render_buffer_entry(
            {"seq": 7, "kind": "prompt", "text": "fix the CORS bug"}
        )
        assert out == '[7] User said: "fix the CORS bug"'

    def test_tool_kind_renders_as_jsonl(self):
        out = cap._render_buffer_entry(
            {"seq": 3, "kind": "tool", "tool": "Edit", "input_summary": "x"}
        )
        # JSONL form, sorted keys (existing legacy contract).
        assert '"tool": "Edit"' in out or '"tool":"Edit"' in out
        assert out.startswith("{")

    def test_missing_kind_treated_as_tool(self):
        out = cap._render_buffer_entry(
            {"seq": 1, "tool": "Bash", "input_summary": "ls"}
        )
        assert out.startswith("{")
        assert '"tool"' in out


# ── Project plumbing ──────────────────────────────────────────────────


class TestProjectInPrompt:
    def test_resolved_project_in_header(self):
        prompt = cap._build_extraction_prompt(
            buffer_slice=[{"seq": 1, "kind": "tool", "tool": "Edit"}],
            transcript_tail="",
            recent_titles=[],
            project="github.com/foo/bar",
        )
        assert "Project: github.com/foo/bar" in prompt
        # And it shows up as the literal in the directive.
        assert 'project="github.com/foo/bar"' in prompt

    def test_no_project_renders_unknown(self):
        prompt = cap._build_extraction_prompt(
            buffer_slice=[{"seq": 1, "kind": "tool", "tool": "Edit"}],
            transcript_tail="",
            recent_titles=[],
            project=None,
        )
        assert "Project: (unknown — no git remote)" in prompt
        # The literal substituted into the directive becomes None.
        assert "project=None" in prompt


# ── kind:"prompt" rendering ──────────────────────────────────────────


class TestPromptEntriesRender:
    def test_prompt_entry_renders_user_said(self):
        prompt = cap._build_extraction_prompt(
            buffer_slice=[
                {"seq": 1, "kind": "prompt", "text": "ship the feature"},
                {"seq": 2, "kind": "tool", "tool": "Edit"},
            ],
            transcript_tail="",
            recent_titles=[],
            project="proj",
        )
        assert 'User said: "ship the feature"' in prompt


# ── Intent directive (conditional on prompt entries) ─────────────────


class TestIntentDirective:
    def test_intent_directive_required_when_prompts_present(self):
        prompt = cap._build_extraction_prompt(
            buffer_slice=[
                {"seq": 1, "kind": "prompt", "text": "fix CORS"},
                {"seq": 2, "kind": "tool", "tool": "Edit"},
            ],
            transcript_tail="",
            recent_titles=[],
            project="proj",
        )
        assert "Intent summary (REQUIRED for this batch)" in prompt
        assert 'tags=["intent"]' in prompt
        # The skip variant must NOT appear in this branch.
        assert "Intent summary (SKIP for this batch)" not in prompt

    def test_intent_directive_skipped_when_no_prompts(self):
        prompt = cap._build_extraction_prompt(
            buffer_slice=[
                {"seq": 1, "kind": "tool", "tool": "Edit"},
                {"seq": 2, "kind": "tool", "tool": "Bash"},
            ],
            transcript_tail="",
            recent_titles=[],
            project="proj",
        )
        assert "Intent summary (SKIP for this batch)" in prompt
        assert "Do NOT emit a tags=" in prompt
        # The required variant must NOT appear.
        assert "Intent summary (REQUIRED for this batch)" not in prompt


# ── Scope directive ───────────────────────────────────────────────────


class TestScopeDirective:
    def test_scope_required_directive_present(self):
        prompt = cap._build_extraction_prompt(
            buffer_slice=[{"seq": 1, "kind": "tool", "tool": "Edit"}],
            transcript_tail="",
            recent_titles=[],
            project="proj",
        )
        assert "Scope (REQUIRED on every remember_observation call)" in prompt
        # Both options are documented.
        assert 'scope="project"' in prompt
        assert 'scope="global"' in prompt
        # When-in-doubt rule.
        assert 'pick "project"' in prompt


# ── _has_prompt_entries helper ────────────────────────────────────────


class TestHasPromptEntries:
    def test_true_when_prompt_present(self):
        assert cap._has_prompt_entries(
            [{"seq": 1, "kind": "prompt", "text": "x"}]
        )

    def test_false_when_only_tool(self):
        assert not cap._has_prompt_entries(
            [{"seq": 1, "kind": "tool", "tool": "Edit"}]
        )

    def test_false_when_kind_missing(self):
        assert not cap._has_prompt_entries([{"seq": 1, "tool": "Edit"}])

    def test_false_on_empty(self):
        assert not cap._has_prompt_entries([])
