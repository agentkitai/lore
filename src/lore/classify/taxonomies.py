"""Classification taxonomy constants."""

from typing import Tuple

INTENT_LABELS: Tuple[str, ...] = (
    "question",      # Asking for information ("How do I deploy?")
    "statement",     # Declaring a fact ("The build is broken")
    "instruction",   # Directing action ("Run tests before merging")
    "preference",    # Personal choice/convention ("I always use bun")
    "observation",   # Noting without judgment ("Deploy took 12 min")
    "decision",      # Recording a choice ("We chose Postgres over MySQL")
)

DOMAIN_LABELS: Tuple[str, ...] = (
    "technical",       # Code, tools, infra, debugging
    "personal",        # Habits, non-work topics
    "business",        # Strategy, metrics, stakeholders
    "creative",        # Design, writing, brainstorming
    "administrative",  # Process, scheduling, org
)

EMOTION_LABELS: Tuple[str, ...] = (
    "neutral",      # No strong emotional signal
    "frustrated",   # Annoyance, blockers
    "excited",      # Enthusiasm, positive energy
    "curious",      # Exploration, wondering
    "confident",    # Certainty, conviction
    "uncertain",    # Doubt, hedging
)
