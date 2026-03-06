"""Classification dataclass and abstract classifier."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict

from lore.classify.taxonomies import DOMAIN_LABELS, EMOTION_LABELS, INTENT_LABELS


@dataclass
class Classification:
    """Result of classifying a piece of text."""

    intent: str
    domain: str
    emotion: str
    confidence: Dict[str, float] = field(default_factory=dict)
    low_confidence: bool = False

    def to_dict(self) -> Dict:
        """Convert to dict suitable for metadata.classification storage."""
        d: Dict = {
            "intent": self.intent,
            "domain": self.domain,
            "emotion": self.emotion,
            "confidence": dict(self.confidence),
        }
        if self.low_confidence:
            d["low_confidence"] = True
        return d


def make_classification(
    intent: str,
    domain: str,
    emotion: str,
    confidence: Dict[str, float],
) -> Classification:
    """Create a validated Classification. Raises ValueError for unknown labels."""
    if intent not in INTENT_LABELS:
        raise ValueError(f"Unknown intent: {intent!r}")
    if domain not in DOMAIN_LABELS:
        raise ValueError(f"Unknown domain: {domain!r}")
    if emotion not in EMOTION_LABELS:
        raise ValueError(f"Unknown emotion: {emotion!r}")
    clamped = {k: max(0.0, min(1.0, v)) for k, v in confidence.items()}
    return Classification(intent=intent, domain=domain, emotion=emotion, confidence=clamped)


class Classifier(ABC):
    """Abstract classifier — implemented by LLM and rule-based backends."""

    @abstractmethod
    def classify(self, text: str) -> Classification:
        """Classify text by intent, domain, and emotion."""
        ...
