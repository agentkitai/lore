"""Phase 6G -- ``lore setup claude-code`` installs the two new hooks.

Coverage:

* Both ``~/.claude/hooks/lore-capture-prompt.sh`` and
  ``~/.claude/hooks/lore-capture-end.sh`` exist after install.
* Both are user-executable.
* The Claude Code settings JSON registers
  ``UserPromptSubmit -> lore-capture-prompt.sh`` and
  ``SessionEnd -> lore-capture-end.sh``.
* Both hook scripts are bash-syntax-clean.
"""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from lore import setup as setup_mod


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point setup_claude_code at a temp HOME so tests don't touch the
    real ~/.claude. We patch ``Path.home`` rather than the env var so
    the various helpers in setup.py that compute paths via
    ``Path.home()`` see the redirect."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_setup_claude_code_installs_phase6g_hooks(fake_home: Path):
    setup_mod.setup_claude_code(server_url="http://localhost:8765", api_key="test")

    prompt_hook = fake_home / ".claude" / "hooks" / "lore-capture-prompt.sh"
    end_hook = fake_home / ".claude" / "hooks" / "lore-capture-end.sh"
    assert prompt_hook.exists(), f"missing: {prompt_hook}"
    assert end_hook.exists(), f"missing: {end_hook}"

    # Both must be user-executable.
    assert prompt_hook.stat().st_mode & stat.S_IXUSR
    assert end_hook.stat().st_mode & stat.S_IXUSR


def test_setup_claude_code_phase6g_hooks_are_bash_clean(fake_home: Path):
    setup_mod.setup_claude_code(server_url="http://localhost:8765", api_key="test")
    prompt_hook = fake_home / ".claude" / "hooks" / "lore-capture-prompt.sh"
    end_hook = fake_home / ".claude" / "hooks" / "lore-capture-end.sh"
    for hook in (prompt_hook, end_hook):
        result = subprocess.run(
            ["bash", "-n", str(hook)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, f"{hook}: {result.stderr}"


def test_setup_claude_code_registers_phase6g_events(fake_home: Path):
    setup_mod.setup_claude_code(server_url="http://localhost:8765", api_key="test")

    settings_path = fake_home / ".claude" / "settings.json"
    assert settings_path.exists()
    settings = json.loads(settings_path.read_text())

    prompt_hook = str(fake_home / ".claude" / "hooks" / "lore-capture-prompt.sh")
    end_hook = str(fake_home / ".claude" / "hooks" / "lore-capture-end.sh")

    user_prompt_hooks = settings.get("hooks", {}).get("UserPromptSubmit", []) or []
    session_end_hooks = settings.get("hooks", {}).get("SessionEnd", []) or []

    assert setup_mod._claude_hook_already_registered(user_prompt_hooks, prompt_hook), (
        f"UserPromptSubmit -> capture-prompt hook not registered. "
        f"settings={settings}"
    )
    assert setup_mod._claude_hook_already_registered(session_end_hooks, end_hook), (
        f"SessionEnd -> capture-end hook not registered. settings={settings}"
    )


def test_setup_claude_code_phase6g_idempotent(fake_home: Path):
    """Running setup twice does not double-register hooks."""
    setup_mod.setup_claude_code(server_url="http://localhost:8765", api_key="test")
    setup_mod.setup_claude_code(server_url="http://localhost:8765", api_key="test")

    settings_path = fake_home / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())

    prompt_hook = str(fake_home / ".claude" / "hooks" / "lore-capture-prompt.sh")
    end_hook = str(fake_home / ".claude" / "hooks" / "lore-capture-end.sh")

    # Count registrations across the matcher-grouped shape.
    def _count(event_hooks: list, target: str) -> int:
        n = 0
        for h in event_hooks:
            if not isinstance(h, dict):
                continue
            if h.get("command") == target:
                n += 1
            for inner in h.get("hooks", []) or []:
                if isinstance(inner, dict) and inner.get("command") == target:
                    n += 1
        return n

    user_prompt_hooks = settings.get("hooks", {}).get("UserPromptSubmit", []) or []
    session_end_hooks = settings.get("hooks", {}).get("SessionEnd", []) or []
    assert _count(user_prompt_hooks, prompt_hook) == 1
    assert _count(session_end_hooks, end_hook) == 1
