#!/usr/bin/env python3
"""OpenClaw -> Lore bridge: tails OpenClaw JSON logs and sends conversations to Lore.

Watches OpenClaw log files for completed conversation turns, extracts
user/assistant message pairs, and sends them to Lore's POST /v1/conversations
endpoint for automatic memory extraction.

Configuration via environment variables:
    LORE_API_URL      - Lore server URL (required)
    LORE_API_KEY      - Lore API key (required)
    OPENCLAW_LOG_DIR  - OpenClaw log directory (default: /tmp/openclaw)
    LORE_USER_ID      - User ID for extracted memories (optional)
    LORE_PROJECT      - Project scope (optional)
    POLL_INTERVAL     - Seconds between polls (default: 5)
"""

from __future__ import annotations

import glob
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("openclaw-lore-bridge")

# State file tracks last processed position per log file
STATE_FILE = os.environ.get(
    "BRIDGE_STATE_FILE",
    os.path.expanduser("~/.local/state/openclaw-lore-bridge.json"),
)


def _load_state() -> Dict[str, int]:
    """Load position state from disk."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: Dict[str, int]) -> None:
    """Persist position state to disk."""
    state_dir = os.path.dirname(STATE_FILE)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def _send_conversation(
    messages: List[Dict[str, str]],
    *,
    api_url: str,
    api_key: str,
    user_id: Optional[str] = None,
    project: Optional[str] = None,
) -> bool:
    """POST a conversation to Lore. Returns True on success."""
    import httpx

    url = api_url.rstrip("/") + "/v1/conversations"
    payload: Dict[str, Any] = {"messages": messages}
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
            logger.info(
                "Sent %d messages (job_id=%s)",
                result.get("message_count", len(messages)),
                result.get("job_id", "?"),
            )
            return True
    except Exception as e:
        logger.error("Failed to send conversation: %s", e)
        return False


def _parse_log_entry(entry: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    """Extract messages from an OpenClaw log entry.

    Expected format:
        {"type": "conversation", "messages": [{"role": ..., "content": ...}, ...]}
    or:
        {"type": "turn", "role": "...", "content": "..."}

    Returns a list of messages if the entry represents a complete conversation,
    or None if it should be skipped.
    """
    entry_type = entry.get("type", "")

    if entry_type == "conversation":
        messages = entry.get("messages", [])
        if messages and isinstance(messages, list):
            return [
                {"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in messages
                if m.get("content")
            ]

    if entry_type == "turn" and entry.get("content"):
        return [{"role": entry.get("role", "assistant"), "content": entry["content"]}]

    # Also handle {"event": "response", ...} format
    if entry.get("event") == "response" and entry.get("output"):
        messages = []
        if entry.get("input"):
            messages.append({"role": "user", "content": str(entry["input"])})
        messages.append({"role": "assistant", "content": str(entry["output"])})
        return messages

    return None


def _process_log_file(
    log_path: str,
    state: Dict[str, int],
    *,
    api_url: str,
    api_key: str,
    user_id: Optional[str] = None,
    project: Optional[str] = None,
) -> int:
    """Process new entries in a log file. Returns count of conversations sent."""
    offset = state.get(log_path, 0)
    sent = 0

    try:
        file_size = os.path.getsize(log_path)
        if file_size <= offset:
            return 0

        with open(log_path) as f:
            f.seek(offset)
            pending_turns: List[Dict[str, str]] = []

            for line in f:
                line = line.strip()
                if not line:
                    # Blank line separates conversation groups
                    if pending_turns:
                        if _send_conversation(
                            pending_turns,
                            api_url=api_url,
                            api_key=api_key,
                            user_id=user_id,
                            project=project,
                        ):
                            sent += 1
                        pending_turns = []
                    continue

                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                messages = _parse_log_entry(entry)
                if messages is None:
                    continue

                # If it's a full conversation, send immediately
                if entry.get("type") == "conversation":
                    if _send_conversation(
                        messages,
                        api_url=api_url,
                        api_key=api_key,
                        user_id=user_id,
                        project=project,
                    ):
                        sent += 1
                else:
                    pending_turns.extend(messages)

            # Send any remaining turns
            if pending_turns:
                if _send_conversation(
                    pending_turns,
                    api_url=api_url,
                    api_key=api_key,
                    user_id=user_id,
                    project=project,
                ):
                    sent += 1

            state[log_path] = f.tell()

    except Exception as e:
        logger.error("Error processing %s: %s", log_path, e)

    return sent


def _find_log_files(log_dir: str) -> List[str]:
    """Find OpenClaw log files matching the expected naming pattern."""
    pattern = os.path.join(log_dir, "openclaw-*.log")
    return sorted(glob.glob(pattern))


def run_bridge(
    *,
    api_url: str,
    api_key: str,
    log_dir: str = "/tmp/openclaw",
    user_id: Optional[str] = None,
    project: Optional[str] = None,
    poll_interval: float = 5.0,
    once: bool = False,
) -> None:
    """Main bridge loop. Tails OpenClaw logs and sends conversations to Lore.

    Args:
        once: If True, process once and exit (for testing). Otherwise loop forever.
    """
    logger.info("Starting OpenClaw-Lore bridge")
    logger.info("  API URL: %s", api_url)
    logger.info("  Log dir: %s", log_dir)
    logger.info("  Poll interval: %.1fs", poll_interval)

    state = _load_state()
    running = True

    def _shutdown(signum: int, frame: Any) -> None:
        nonlocal running
        logger.info("Received signal %d, shutting down...", signum)
        running = False

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while running:
        log_files = _find_log_files(log_dir)
        total_sent = 0

        for log_path in log_files:
            sent = _process_log_file(
                log_path,
                state,
                api_url=api_url,
                api_key=api_key,
                user_id=user_id,
                project=project,
            )
            total_sent += sent

        if total_sent > 0:
            _save_state(state)
            logger.info("Processed %d conversation(s) this cycle", total_sent)
        else:
            # Still save state periodically to track file positions
            _save_state(state)

        if once:
            break

        time.sleep(poll_interval)

    logger.info("Bridge stopped")


def main() -> None:
    api_url = os.environ.get("LORE_API_URL", "")
    api_key = os.environ.get("LORE_API_KEY", "")

    if not api_url:
        print("Error: LORE_API_URL environment variable is required", file=sys.stderr)
        sys.exit(1)
    if not api_key:
        print("Error: LORE_API_KEY environment variable is required", file=sys.stderr)
        sys.exit(1)

    log_dir = os.environ.get("OPENCLAW_LOG_DIR", "/tmp/openclaw")
    user_id = os.environ.get("LORE_USER_ID")
    project = os.environ.get("LORE_PROJECT")
    poll_interval = float(os.environ.get("POLL_INTERVAL", "5"))

    run_bridge(
        api_url=api_url,
        api_key=api_key,
        log_dir=log_dir,
        user_id=user_id,
        project=project,
        poll_interval=poll_interval,
    )


if __name__ == "__main__":
    main()
