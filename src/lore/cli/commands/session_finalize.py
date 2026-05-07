"""``lore session-finalize`` -- Phase 6G SessionEnd handler.

Emits one ``meta.kind="summary"`` observation summarizing what the
session accomplished, then writes a ``sealed`` marker file so subsequent
finalize calls no-op. Idempotent -- reading the sealed marker is the
gate.

Called by ``hooks/lore-capture-end.sh`` after a foreground
``lore capture-extract`` flush has finished writing per-batch
observations. The summary is then the highest-signal entry the next
session's ``recent_activity`` picks up.

Failure modes are deliberately permissive:

* No ``claude`` binary on PATH -- log + seal anyway. We don't want a
  misconfigured environment to leave sessions unsealed forever.
* HTTP fetch fails / empty observations -- seal silently and skip the
  subagent. There's nothing to summarize.
* Subagent timeout / exit non-zero -- still seal. The per-batch
  observations are already in the DB; missing the summary is a soft
  loss, but loop-retrying isn't going to fix it without manual
  intervention.

Always returns exit code 0 (fail-open).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any, Optional


SUMMARY_PROMPT = """\
You are summarizing session {sid} for project {project}.

Below is the list of observations already extracted for this session.
Read them, then call mcp__lore__remember_observation EXACTLY ONCE with:

  - title: <=80 chars, what this session accomplished overall
  - facts: 3-5 atomic items (decisions made, problems solved, open threads)
  - narrative: 2-3 sentences of context
  - tags: ["session-summary"]    # this is what marks it as a summary
  - scope: "project"
  - project: {project_arg}

Do NOT re-extract anything from raw tool logs or buffer files. Read only
the observations listed below.

Observations:
{observations_block}

After you've called remember_observation, print on the FINAL line:
SUMMARY_EMITTED=1
"""


def _format_observations_block(observations: list[dict]) -> str:
    """One short line per observation: ``- [id] title: narrative``."""
    lines: list[str] = []
    for o in observations:
        if not isinstance(o, dict):
            continue
        oid = o.get("id") or ""
        title = (o.get("title") or "").strip()
        narrative = (o.get("narrative") or "").strip()
        if len(narrative) > 200:
            narrative = narrative[:200].rstrip() + "..."
        lines.append(f"- [{oid}] {title}: {narrative}")
    return "\n".join(lines) if lines else "(none)"


async def _fetch_session_observations(
    *,
    api_url: str,
    api_key: Optional[str],
    session_id: str,
    timeout: float = 10.0,
    limit: int = 200,
) -> list[dict]:
    """Pull recent observations from the server, then filter to ``session_id``.

    The server's ``GET /v1/observations`` does not accept a session-id
    filter today; we filter client-side. Returns ``[]`` on any HTTP
    failure (the caller treats that as "nothing to summarize").
    """
    try:
        import httpx
    except ImportError:
        return []

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(base_url=api_url, timeout=timeout) as client:
            resp = await client.get(
                "/v1/observations",
                headers=headers,
                params={"limit": limit},
            )
    except Exception:
        return []
    if resp.status_code != 200:
        return []
    try:
        body = resp.json()
    except Exception:
        return []
    observations = body.get("observations") if isinstance(body, dict) else None
    if not isinstance(observations, list):
        return []
    out: list[dict] = []
    for o in observations:
        if not isinstance(o, dict):
            continue
        sid = o.get("session_id")
        if sid is None and isinstance(o.get("meta"), dict):
            sid = o["meta"].get("session_id")
        if sid == session_id:
            out.append(o)
    return out


async def run(*, session_id: str, lore_home: Path) -> int:
    """Emit one ``meta.kind="summary"`` observation, then write ``sealed``.

    Idempotent: if ``<lore_home>/sessions/<sid>/sealed`` already exists,
    returns 0 immediately without spawning anything.
    """
    sealed = lore_home / "sessions" / session_id / "sealed"
    if sealed.exists():
        return 0  # idempotent no-op

    api_url = os.environ.get("LORE_API_URL", "http://localhost:8765")
    api_key = os.environ.get("LORE_API_KEY") or _read_solo_key()

    observations = await _fetch_session_observations(
        api_url=api_url,
        api_key=api_key,
        session_id=session_id,
    )

    sealed.parent.mkdir(parents=True, exist_ok=True)
    if not observations:
        # Nothing to summarize -- seal silently so the next SessionEnd
        # doesn't keep retrying this empty case.
        sealed.touch()
        return 0

    project = ""
    for o in observations:
        if isinstance(o, dict) and o.get("project"):
            project = str(o["project"])
            break

    project_arg = f'"{project}"' if project else "None"
    project_display = project if project else "(unknown)"
    obs_block = _format_observations_block(observations)
    prompt = SUMMARY_PROMPT.format(
        sid=session_id,
        project=project_display,
        project_arg=project_arg,
        observations_block=obs_block,
    )

    log_path = lore_home / "sessions" / session_id / "finalize.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_path, "w", encoding="utf-8") as logf:
            proc = await asyncio.create_subprocess_exec(
                "claude", "-p", prompt,
                stdout=logf, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=120.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
    except FileNotFoundError:
        # ``claude`` not on PATH -- sealed-anyway is the right call.
        pass
    except Exception:
        # Catch-all so we always seal. Documented invariant: SessionEnd
        # seals exactly once.
        pass

    sealed.touch()
    return 0


def _read_solo_key() -> Optional[str]:
    """Mirror of the auto-bootstrap key fallback used elsewhere in the CLI."""
    key_path = Path.home() / ".lore" / "key.txt"
    if not key_path.exists():
        return None
    try:
        return (key_path.read_text(encoding="utf-8").strip()) or None
    except OSError:
        return None


def cmd_session_finalize(args: argparse.Namespace) -> int:
    """argparse handler for ``lore session-finalize``."""
    session_id = getattr(args, "session_id", None) or ""
    if not session_id:
        return 0
    lore_home = Path(
        getattr(args, "lore_home", None)
        or os.environ.get("LORE_HOME")
        or (Path.home() / ".lore")
    )
    return asyncio.run(run(session_id=session_id, lore_home=lore_home))


def main(argv: Optional[list[str]] = None) -> int:
    """Standalone entry point used by tests / debugging.

    The hook invokes ``lore session-finalize`` instead, but exposing
    this lets the command run without the rest of the CLI being loaded.
    """
    p = argparse.ArgumentParser(prog="lore session-finalize")
    p.add_argument("--session-id", required=True, dest="session_id")
    p.add_argument(
        "--lore-home",
        default=os.environ.get("LORE_HOME", str(Path.home() / ".lore")),
        dest="lore_home",
    )
    args = p.parse_args(argv)
    return cmd_session_finalize(args)


__all__ = [
    "run",
    "cmd_session_finalize",
    "main",
    "SUMMARY_PROMPT",
    "_fetch_session_observations",
    "_format_observations_block",
]
