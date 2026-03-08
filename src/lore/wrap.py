"""lore wrap — transparent CLI wrapper that captures conversations for Lore.

Usage:
    lore wrap claude -- -p "explain decorators"
    lore wrap codex -- --model o4-mini
    lore wrap <any-command> [-- args...]

Captures stdin/stdout of the wrapped process via a PTY, then sends the
captured conversation to Lore's POST /v1/conversations endpoint (or
local add_conversation if no API URL is configured).
"""

from __future__ import annotations

import io
import os
import pty
import signal
import sys
from typing import Dict, List, Optional


def _send_to_api(
    messages: List[Dict[str, str]],
    *,
    api_url: str,
    api_key: str,
    user_id: Optional[str] = None,
    project: Optional[str] = None,
) -> None:
    """POST conversation to Lore's /v1/conversations endpoint."""
    import httpx

    url = api_url.rstrip("/") + "/v1/conversations"
    payload: Dict = {"messages": messages}
    if user_id:
        payload["user_id"] = user_id
    if project:
        payload["project"] = project

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            result = resp.json()
            print(
                f"\n[lore] Conversation sent ({result.get('message_count', len(messages))} messages, "
                f"job_id={result.get('job_id', '?')})",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"\n[lore] Failed to send conversation: {e}", file=sys.stderr)


def _send_local(
    messages: List[Dict[str, str]],
    *,
    user_id: Optional[str] = None,
    project: Optional[str] = None,
    db: Optional[str] = None,
) -> None:
    """Use local Lore instance to extract memories from conversation."""
    try:
        from lore import Lore

        kwargs: Dict = {"enrichment": True}
        if db:
            kwargs["db_path"] = db
        lore = Lore(**kwargs)
        result = lore.add_conversation(
            messages,
            user_id=user_id,
            project=project,
        )
        lore.close()
        print(
            f"\n[lore] Extracted {result.memories_extracted} memories "
            f"({result.duplicates_skipped} duplicates skipped)",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"\n[lore] Local extraction failed: {e}", file=sys.stderr)


def _parse_conversation(raw_output: str) -> List[Dict[str, str]]:
    """Parse raw terminal output into user/assistant message pairs.

    Heuristic: lines starting with common prompt patterns (>, $, >>>, human:,
    user:, You:) are treated as user input; everything else is assistant output.
    For tools like claude/codex, user input comes from the TTY and assistant
    output is the bulk of the captured text. We group contiguous blocks.
    """
    lines = raw_output.split("\n")
    messages: List[Dict[str, str]] = []
    current_role = "assistant"
    current_lines: List[str] = []

    # Prompt patterns that indicate user input
    prompt_prefixes = (
        "> ", "$ ", ">>> ", "human:", "user:", "You:", "Human:",
        "❯ ", "% ",
    )

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_lines:
                current_lines.append("")
            continue

        is_user = any(stripped.startswith(p) for p in prompt_prefixes)
        role = "user" if is_user else "assistant"

        if role != current_role:
            if current_lines:
                content = "\n".join(current_lines).strip()
                if content:
                    messages.append({"role": current_role, "content": content})
                current_lines = []
            current_role = role

        current_lines.append(line)

    # Flush remaining
    if current_lines:
        content = "\n".join(current_lines).strip()
        if content:
            messages.append({"role": current_role, "content": content})

    # If we only got assistant messages, wrap in a single user+assistant pair
    if messages and all(m["role"] == "assistant" for m in messages):
        full_text = "\n\n".join(m["content"] for m in messages)
        messages = [
            {"role": "user", "content": "(wrapped session)"},
            {"role": "assistant", "content": full_text},
        ]

    return messages


def run_wrap(
    command: List[str],
    *,
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
    user_id: Optional[str] = None,
    project: Optional[str] = None,
    db: Optional[str] = None,
) -> int:
    """Wrap a command, capture its I/O, and send the conversation to Lore.

    Returns the exit code of the wrapped process.
    """
    if not command:
        print("Error: no command to wrap", file=sys.stderr)
        return 1

    captured = io.BytesIO()
    exit_code = 0

    def _read_callback(fd: int) -> bytes:
        data = os.read(fd, 4096)
        captured.write(data)
        return data

    # Save and restore signal handlers around pty.spawn
    old_sigint = signal.getsignal(signal.SIGINT)
    old_sigtstp = signal.getsignal(signal.SIGTSTP)

    try:
        # pty.spawn passes through the terminal transparently
        exit_code = pty.spawn(command, _read_callback)
        # pty.spawn returns raw waitpid status; extract exit code
        if os.WIFEXITED(exit_code):
            exit_code = os.WEXITSTATUS(exit_code)
        else:
            exit_code = 1
    except FileNotFoundError:
        print(f"Error: command not found: {command[0]}", file=sys.stderr)
        return 127
    except Exception as e:
        print(f"Error running command: {e}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTSTP, old_sigtstp)

    # Parse captured output into conversation
    raw = captured.getvalue().decode("utf-8", errors="replace")
    if not raw.strip():
        print("[lore] No output captured, skipping extraction.", file=sys.stderr)
        return exit_code

    messages = _parse_conversation(raw)
    if not messages:
        print("[lore] No conversation detected, skipping extraction.", file=sys.stderr)
        return exit_code

    # Determine delivery method
    api_url = api_url or os.environ.get("LORE_API_URL", "")
    api_key = api_key or os.environ.get("LORE_API_KEY", "")

    if api_url and api_key:
        _send_to_api(
            messages,
            api_url=api_url,
            api_key=api_key,
            user_id=user_id,
            project=project,
        )
    else:
        _send_local(
            messages,
            user_id=user_id,
            project=project,
            db=db,
        )

    return exit_code
