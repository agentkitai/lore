"""Cheap-subagent config materialization.

Lore spawns ``claude -p`` subagents from three places (capture-extract,
dream, graph_extraction). Without flags, those subagents inherit the
parent user's full Claude Code stack — default model (often Opus),
``alwaysThinkingEnabled``, ``effortLevel``, every enabled plugin, and
every MCP server (including lore itself, recursively). On Opus this
costs roughly $0.35 / spawn and ~46k cache-creation tokens just to load
the system prompt before the subagent does its real work.

This module materializes two artifacts under ``~/.lore/subagent/`` —
a minimal MCP config (lore-only or empty) and a minimal settings
override (no plugins, no thinking, low effort) — and returns the
paths plus the chosen model. Spawn sites add ``--model``,
``--strict-mcp-config``, ``--mcp-config``, and ``--settings``.

Environment overrides:
  * ``LORE_SUBAGENT_MODEL``  — fallback default for all roles
  * ``LORE_DREAM_MODEL``     — dream-specific override
  * ``LORE_GRAPH_MODEL``     — graph-extraction-specific override

Defaults: capture and graph_extraction use Haiku 4.5 (one-shot
extraction is well within Haiku's range); dream uses Sonnet 4.6
because it does multi-step reflection and runs at most once per 24h.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# ── Model defaults ────────────────────────────────────────────────

_DEFAULT_CHEAP_MODEL = "claude-haiku-4-5"
_DEFAULT_DREAM_MODEL = "claude-sonnet-4-6"


def _resolve_model(role: str) -> str:
    base = os.environ.get("LORE_SUBAGENT_MODEL")
    if role == "dream":
        return os.environ.get("LORE_DREAM_MODEL") or base or _DEFAULT_DREAM_MODEL
    if role == "graph":
        return os.environ.get("LORE_GRAPH_MODEL") or base or _DEFAULT_CHEAP_MODEL
    return base or _DEFAULT_CHEAP_MODEL


# ── Path layout ───────────────────────────────────────────────────


def _subagent_dir() -> Path:
    home = os.environ.get("LORE_HOME") or os.path.expanduser("~/.lore")
    return Path(home) / "subagent"


def _mcp_with_lore_path() -> Path:
    return _subagent_dir() / "mcp-with-lore.json"


def _mcp_empty_path() -> Path:
    return _subagent_dir() / "mcp-empty.json"


def _settings_path() -> Path:
    return _subagent_dir() / "settings.json"


# ── Config bodies ─────────────────────────────────────────────────


def _mcp_with_lore_body() -> dict:
    # No ``env`` block: the spawned ``lore mcp`` server inherits env
    # from ``claude -p`` which inherits from this Python process. That
    # lets LORE_API_URL / LORE_API_KEY / LORE_STORE flow through
    # naturally without baking secrets into a file under ~/.lore.
    return {
        "mcpServers": {
            "lore": {
                "command": "lore",
                "args": ["mcp"],
            }
        }
    }


def _mcp_empty_body() -> dict:
    return {"mcpServers": {}}


def _settings_body() -> dict:
    # ``--settings`` merges with the user's settings.json. Setting these
    # keys explicitly overrides any inherited values.
    return {
        "enabledPlugins": {},
        "alwaysThinkingEnabled": False,
        "effortLevel": "low",
    }


# ── Materialization ───────────────────────────────────────────────


def _write_if_changed(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = json.dumps(body, indent=2, sort_keys=True) + "\n"
    try:
        if path.exists() and path.read_text(encoding="utf-8") == new:
            return
    except OSError:
        pass
    path.write_text(new, encoding="utf-8")


@dataclass(frozen=True, slots=True)
class SubagentConfig:
    model: str
    mcp_config_path: Path
    settings_path: Path

    def claude_flags(self) -> list[str]:
        return [
            "--model", self.model,
            "--strict-mcp-config",
            "--mcp-config", str(self.mcp_config_path),
            "--settings", str(self.settings_path),
        ]


def subagent_config(*, role: str, with_lore_mcp: bool) -> SubagentConfig:
    """Return paths + model for a subagent spawn.

    ``role`` is ``"capture"``, ``"dream"``, or ``"graph"`` and selects
    the env-var override chain. ``with_lore_mcp=True`` materializes a
    config exposing only ``mcp__lore__*`` to the subagent;
    ``False`` materializes an empty MCP config (graph_extraction
    needs no tool calls).
    """
    settings_path = _settings_path()
    _write_if_changed(settings_path, _settings_body())

    if with_lore_mcp:
        mcp_path = _mcp_with_lore_path()
        _write_if_changed(mcp_path, _mcp_with_lore_body())
    else:
        mcp_path = _mcp_empty_path()
        _write_if_changed(mcp_path, _mcp_empty_body())

    return SubagentConfig(
        model=_resolve_model(role),
        mcp_config_path=mcp_path,
        settings_path=settings_path,
    )
