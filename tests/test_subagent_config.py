"""Tests for ``lore.subagent_config``.

Guards the model defaults, env-var override chain, MCP config bodies,
and lazy materialization. Without this, the cheap-subagent flag set
could silently regress back to inheriting the user's full Claude Code
stack (Opus + plugins + thinking) on every spawn.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lore import subagent_config as sc


@pytest.fixture(autouse=True)
def _isolated_lore_home(monkeypatch, tmp_path):
    monkeypatch.setenv("LORE_HOME", str(tmp_path))
    for var in ("LORE_SUBAGENT_MODEL", "LORE_DREAM_MODEL", "LORE_GRAPH_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


# ── model resolution ──────────────────────────────────────────────


class TestModelResolution:
    def test_capture_defaults_to_haiku(self):
        cfg = sc.subagent_config(role="capture", with_lore_mcp=True)
        assert "haiku" in cfg.model

    def test_graph_defaults_to_haiku(self):
        cfg = sc.subagent_config(role="graph", with_lore_mcp=False)
        assert "haiku" in cfg.model

    def test_dream_defaults_to_sonnet(self):
        cfg = sc.subagent_config(role="dream", with_lore_mcp=True)
        assert "sonnet" in cfg.model

    def test_lore_subagent_model_overrides_all_roles(self, monkeypatch):
        monkeypatch.setenv("LORE_SUBAGENT_MODEL", "claude-opus-4-7")
        for role in ("capture", "dream", "graph"):
            assert sc.subagent_config(role=role, with_lore_mcp=False).model == "claude-opus-4-7"

    def test_role_specific_override_wins(self, monkeypatch):
        monkeypatch.setenv("LORE_SUBAGENT_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("LORE_DREAM_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("LORE_GRAPH_MODEL", "claude-haiku-4-5")
        assert sc.subagent_config(role="dream", with_lore_mcp=True).model == "claude-sonnet-4-6"
        assert sc.subagent_config(role="graph", with_lore_mcp=False).model == "claude-haiku-4-5"
        assert sc.subagent_config(role="capture", with_lore_mcp=True).model == "claude-opus-4-7"


# ── MCP config body ───────────────────────────────────────────────


class TestMcpConfig:
    def test_with_lore_mcp_writes_lore_only(self, _isolated_lore_home):
        cfg = sc.subagent_config(role="capture", with_lore_mcp=True)
        body = json.loads(Path(cfg.mcp_config_path).read_text())
        assert list(body["mcpServers"].keys()) == ["lore"]
        # No baked-in env: the spawned `lore mcp` inherits credentials
        # from the parent process, so secrets stay out of ~/.lore.
        assert "env" not in body["mcpServers"]["lore"]

    def test_without_lore_mcp_writes_empty(self, _isolated_lore_home):
        cfg = sc.subagent_config(role="graph", with_lore_mcp=False)
        body = json.loads(Path(cfg.mcp_config_path).read_text())
        assert body == {"mcpServers": {}}

    def test_paths_differ_for_with_vs_without(self, _isolated_lore_home):
        with_lore = sc.subagent_config(role="capture", with_lore_mcp=True)
        without = sc.subagent_config(role="graph", with_lore_mcp=False)
        assert with_lore.mcp_config_path != without.mcp_config_path


# ── settings body ─────────────────────────────────────────────────


class TestSettings:
    def test_disables_plugins_thinking_and_high_effort(self, _isolated_lore_home):
        cfg = sc.subagent_config(role="capture", with_lore_mcp=True)
        body = json.loads(Path(cfg.settings_path).read_text())
        assert body["enabledPlugins"] == {}
        assert body["alwaysThinkingEnabled"] is False
        assert body["effortLevel"] == "low"

    def test_hooks_are_empty_to_break_subagent_recursion(self, _isolated_lore_home):
        # Without this, the subagent's own PostToolUse / Stop / SessionEnd
        # events fire the user's lore-capture-* hooks and spawn nested
        # capture-extracts ad infinitum. Observed in production:
        # ~700 spawns/hour on Haiku, ~$34/h.
        cfg = sc.subagent_config(role="capture", with_lore_mcp=True)
        body = json.loads(Path(cfg.settings_path).read_text())
        assert body["hooks"] == {
            "UserPromptSubmit": [],
            "PostToolUse": [],
            "Stop": [],
            "SessionEnd": [],
        }


# ── env_overrides() — recursion guard fallback ────────────────────


class TestEnvOverrides:
    def test_disarms_capture_and_dream_hook_kill_switches(self, _isolated_lore_home):
        cfg = sc.subagent_config(role="capture", with_lore_mcp=True)
        env = cfg.env_overrides()
        # Master kill switches the lore hook scripts honor — second line
        # of defense in case the parent claude process has cached
        # ~/.claude/settings.json and is still firing user hooks against
        # the subagent's session.
        assert env["LORE_AUTO_SAVE"] == "false"
        assert env["LORE_DREAM_AUTO"] == "false"

    def test_env_overrides_same_for_all_roles(self, _isolated_lore_home):
        # Recursion is a Claude-Code-level concern; affects all subagent
        # types equally regardless of role.
        for role in ("capture", "dream", "graph"):
            cfg = sc.subagent_config(role=role, with_lore_mcp=False)
            env = cfg.env_overrides()
            assert env["LORE_AUTO_SAVE"] == "false"
            assert env["LORE_DREAM_AUTO"] == "false"


# ── claude_flags() shape ──────────────────────────────────────────


class TestClaudeFlags:
    def test_flag_set_complete_and_ordered(self, _isolated_lore_home):
        cfg = sc.subagent_config(role="capture", with_lore_mcp=True)
        flags = cfg.claude_flags()
        # Each flag carries one positional value; pair them up.
        assert flags[0] == "--model"
        assert flags[1] == cfg.model
        assert "--strict-mcp-config" in flags
        # mcp-config / settings paths must round-trip.
        i_mcp = flags.index("--mcp-config")
        assert flags[i_mcp + 1] == str(cfg.mcp_config_path)
        i_set = flags.index("--settings")
        assert flags[i_set + 1] == str(cfg.settings_path)


# ── materialization ───────────────────────────────────────────────


class TestMaterialization:
    def test_creates_subagent_dir_lazily(self, _isolated_lore_home):
        subagent_dir = _isolated_lore_home / "subagent"
        assert not subagent_dir.exists()
        sc.subagent_config(role="capture", with_lore_mcp=True)
        assert subagent_dir.is_dir()

    def test_repeated_calls_do_not_rewrite_unchanged_files(self, _isolated_lore_home):
        cfg = sc.subagent_config(role="capture", with_lore_mcp=True)
        mtime_before = Path(cfg.mcp_config_path).stat().st_mtime_ns
        # Second call with same inputs should be a no-op write.
        sc.subagent_config(role="capture", with_lore_mcp=True)
        mtime_after = Path(cfg.mcp_config_path).stat().st_mtime_ns
        assert mtime_before == mtime_after
