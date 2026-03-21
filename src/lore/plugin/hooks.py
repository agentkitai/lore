"""Hook dispatcher — calls plugin hooks with timeout and error isolation."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

HOOK_TIMEOUT_SECONDS = 5


def dispatch_on_remember(plugins: List[Any], memory: Any) -> Any:
    """Call on_remember on all active plugins."""
    for plugin in plugins:
        try:
            memory = _call_with_timeout(plugin.on_remember, memory)
        except Exception:
            logger.warning("Plugin %s on_remember failed", plugin.meta.name, exc_info=True)
    return memory


def dispatch_on_recall(plugins: List[Any], query: str, results: List[Any]) -> List[Any]:
    """Call on_recall on all active plugins."""
    for plugin in plugins:
        try:
            results = _call_with_timeout(plugin.on_recall, query, results)
        except Exception:
            logger.warning("Plugin %s on_recall failed", plugin.meta.name, exc_info=True)
    return results


def dispatch_on_enrich(plugins: List[Any], memory: Any, enrichment: Dict[str, Any]) -> Dict[str, Any]:
    """Call on_enrich on all active plugins."""
    for plugin in plugins:
        try:
            enrichment = _call_with_timeout(plugin.on_enrich, memory, enrichment)
        except Exception:
            logger.warning("Plugin %s on_enrich failed", plugin.meta.name, exc_info=True)
    return enrichment


def dispatch_on_extract(plugins: List[Any], memory: Any, facts: List[Any]) -> List[Any]:
    """Call on_extract on all active plugins."""
    for plugin in plugins:
        try:
            facts = _call_with_timeout(plugin.on_extract, memory, facts)
        except Exception:
            logger.warning("Plugin %s on_extract failed", plugin.meta.name, exc_info=True)
    return facts


def dispatch_on_score(plugins: List[Any], memory: Any, score: float) -> float:
    """Call on_score on all active plugins."""
    for plugin in plugins:
        try:
            score = _call_with_timeout(plugin.on_score, memory, score)
        except Exception:
            logger.warning("Plugin %s on_score failed", plugin.meta.name, exc_info=True)
    return score


def _call_with_timeout(fn, *args, timeout: float = HOOK_TIMEOUT_SECONDS):
    """Call a function with a timeout. Returns result or raises TimeoutError."""
    result = [None]
    error = [None]

    def _run():
        try:
            result[0] = fn(*args)
        except Exception as e:
            error[0] = e

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        raise TimeoutError(f"Plugin hook timed out after {timeout}s")
    if error[0]:
        raise error[0]
    return result[0]
