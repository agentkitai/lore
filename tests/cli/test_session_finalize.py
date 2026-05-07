"""Phase 6G -- ``lore session-finalize`` tests.

Coverage:

* No observations for the session  -> seal silently, no subagent spawn.
* ``sealed`` marker already present -> idempotent no-op (no spawn,
  no HTTP fetch).
* >=1 observation present          -> subagent spawned with ``claude``
  argv[0] and a prompt containing the session id and observations.
* Format helpers cover empty / oversized narratives.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lore.cli.commands.session_finalize import (
    SUMMARY_PROMPT,
    _format_observations_block,
    run,
)


# ── Format helper unit tests ──────────────────────────────────────────


class TestFormatObservationsBlock:
    def test_empty_returns_placeholder(self):
        assert _format_observations_block([]) == "(none)"

    def test_renders_id_title_narrative(self):
        out = _format_observations_block(
            [
                {"id": "abc", "title": "did stuff", "narrative": "context"},
                {"id": "def", "title": "more", "narrative": "more context"},
            ]
        )
        assert "[abc] did stuff: context" in out
        assert "[def] more: more context" in out

    def test_truncates_long_narrative(self):
        big = "x" * 500
        out = _format_observations_block(
            [{"id": "1", "title": "t", "narrative": big}]
        )
        # Truncated to 200 chars + "..."
        assert "..." in out
        # Combined line length is bounded by ~250 chars.
        assert len(out.splitlines()[0]) < 260

    def test_skips_non_dict(self):
        out = _format_observations_block(["bogus", None, {"id": "x", "title": "ok", "narrative": "y"}])  # type: ignore[list-item]
        assert "[x] ok: y" in out
        assert "bogus" not in out


# ── Idempotent no-op when sealed ──────────────────────────────────────


@pytest.mark.asyncio
async def test_session_finalize_idempotent(tmp_path: Path):
    sid = "sid-already-sealed"
    sealed = tmp_path / "sessions" / sid / "sealed"
    sealed.parent.mkdir(parents=True)
    sealed.touch()

    # The finalize path should NOT spawn anything when sealed already
    # exists. We patch both the HTTP fetch and subprocess spawn to
    # assert neither is reached.
    with patch(
        "lore.cli.commands.session_finalize._fetch_session_observations",
        new=AsyncMock(return_value=[{"id": "1"}]),
    ) as fetch_mock, patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(),
    ) as spawn_mock:
        rc = await run(session_id=sid, lore_home=tmp_path)
    assert rc == 0
    assert sealed.exists()
    fetch_mock.assert_not_called()
    spawn_mock.assert_not_called()


# ── No observations -> seal silently ─────────────────────────────────


@pytest.mark.asyncio
async def test_session_finalize_no_observations_seals_silently(tmp_path: Path):
    sid = "sid-empty"
    sealed = tmp_path / "sessions" / sid / "sealed"
    assert not sealed.exists()

    with patch(
        "lore.cli.commands.session_finalize._fetch_session_observations",
        new=AsyncMock(return_value=[]),
    ), patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(),
    ) as spawn_mock:
        rc = await run(session_id=sid, lore_home=tmp_path)
    assert rc == 0
    assert sealed.exists()
    spawn_mock.assert_not_called()


# ── >=1 observation -> subagent spawned ──────────────────────────────


@pytest.mark.asyncio
async def test_session_finalize_invokes_subagent_when_observations_exist(tmp_path: Path):
    sid = "sid-with-obs"
    observations = [
        {
            "id": "obs-1",
            "title": "fixed CORS",
            "narrative": "Removed wildcard.",
            "project": "github.com/foo/bar",
        }
    ]

    # Stand-in for the asyncio subprocess: ``await proc.wait()`` is
    # what ``run`` calls, and we want it to terminate cleanly.
    fake_proc = MagicMock()
    fake_proc.wait = AsyncMock(return_value=0)
    fake_proc.kill = MagicMock()

    with patch(
        "lore.cli.commands.session_finalize._fetch_session_observations",
        new=AsyncMock(return_value=observations),
    ), patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    ) as spawn_mock:
        rc = await run(session_id=sid, lore_home=tmp_path)

    assert rc == 0
    sealed = tmp_path / "sessions" / sid / "sealed"
    assert sealed.exists()

    spawn_mock.assert_called_once()
    args, kwargs = spawn_mock.call_args
    # First positional is the binary; second is the ``-p`` flag.
    assert args[0] == "claude"
    assert args[1] == "-p"
    prompt = args[2]
    assert sid in prompt
    assert "obs-1" in prompt
    assert "fixed CORS" in prompt
    # The summary directive is present so the subagent knows what to do.
    assert 'tags: ["session-summary"]' in prompt
    # Project literal substituted into the directive.
    assert 'project: "github.com/foo/bar"' in prompt


# ── Subagent failure still seals ─────────────────────────────────────


@pytest.mark.asyncio
async def test_session_finalize_seals_even_on_subagent_failure(tmp_path: Path):
    sid = "sid-fails"
    observations = [{"id": "obs-x", "title": "t", "narrative": "n"}]

    with patch(
        "lore.cli.commands.session_finalize._fetch_session_observations",
        new=AsyncMock(return_value=observations),
    ), patch(
        "asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError("claude not on PATH"),
    ):
        rc = await run(session_id=sid, lore_home=tmp_path)

    assert rc == 0
    assert (tmp_path / "sessions" / sid / "sealed").exists()


# ── Summary prompt template sanity ───────────────────────────────────


def test_summary_prompt_template_has_required_directives():
    # Substitute a representative payload.
    rendered = SUMMARY_PROMPT.format(
        sid="s1",
        project="proj",
        project_arg='"proj"',
        observations_block="- [a] t: n",
    )
    assert 'tags: ["session-summary"]' in rendered
    assert 'scope: "project"' in rendered
    assert "remember_observation EXACTLY ONCE" in rendered
    assert "SUMMARY_EMITTED=1" in rendered
