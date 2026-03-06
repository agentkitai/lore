"""Rule-based classifier — keyword/pattern matching fallback."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from lore.classify.base import Classification, Classifier

_MATCHED_CONFIDENCE = 0.6
_DEFAULT_CONFIDENCE = 0.3


class RuleBasedClassifier(Classifier):
    """Keyword/pattern matching fallback — no LLM required."""

    INTENT_PATTERNS: Dict[str, List[str]] = {
        "question": [
            r"\?\s*$",
            r"^(how|what|why|when|where|who|can|should|is|are|do|does)\b",
        ],
        "instruction": [
            r"^(always|never|make sure|don't|do not|ensure|run|use|set)\b",
        ],
        "preference": [
            r"\b(prefer|always use|i like|i want|my choice|i use)\b",
        ],
        "decision": [
            r"\b(decided|we chose|going with|settled on|decision)\b",
        ],
        "observation": [
            r"\b(noticed|observed|seems|appears|looks like|today)\b",
        ],
        # "statement" is the default fallback — no patterns
    }

    DOMAIN_PATTERNS: Dict[str, List[str]] = {
        "technical": [
            r"\b(code|bug|api|deploy|test|git|docker|server|database|"
            r"function|class|error|config|build|compile|debug|CI|CD|"
            r"kubernetes|k8s|npm|pip|yarn|bun|webpack|vite)\b",
        ],
        "business": [
            r"\b(revenue|customer|stakeholder|okr|metric|strategy|"
            r"roadmap|budget|kpi|roi|market|sales|growth)\b",
        ],
        "creative": [
            r"\b(design|ui|ux|brand|color|layout|write|content|"
            r"story|illustration|prototype|wireframe|mockup)\b",
        ],
        "administrative": [
            r"\b(meeting|schedule|process|policy|review|approval|"
            r"deadline|standup|retro|sprint|planning)\b",
        ],
        # "personal" is the default fallback — no patterns
    }

    EMOTION_PATTERNS: Dict[str, List[str]] = {
        "frustrated": [
            r"\b(annoying|broken|keeps? (failing|breaking)|ugh|"
            r"frustrat|stupid|damn|hate|worst|terrible|horrible)\b",
        ],
        "excited": [
            r"\b(amazing|awesome|love|great|fantastic|excited|"
            r"finally|incredible|brilliant|perfect|beautiful)\b",
        ],
        "curious": [
            r"\b(wonder|curious|interesting|how come|what if|"
            r"explore|intriguing|fascinating)\b",
        ],
        "confident": [
            r"\b(definitely|certainly|sure|confident|absolutely|"
            r"clearly|obviously|without doubt|100%)\b",
        ],
        "uncertain": [
            r"\b(maybe|perhaps|not sure|might|possibly|i think|"
            r"unsure|unclear|probably|seems like)\b",
        ],
        # "neutral" is the default fallback — no patterns
    }

    def classify(self, text: str) -> Classification:
        intent, intent_conf = self._classify_axis(text, self.INTENT_PATTERNS, "statement")
        domain, domain_conf = self._classify_axis(text, self.DOMAIN_PATTERNS, "personal")
        emotion, emotion_conf = self._classify_axis(text, self.EMOTION_PATTERNS, "neutral")
        return Classification(
            intent=intent,
            domain=domain,
            emotion=emotion,
            confidence={
                "intent": intent_conf,
                "domain": domain_conf,
                "emotion": emotion_conf,
            },
        )

    def _classify_axis(
        self, text: str, patterns: Dict[str, List[str]], default: str
    ) -> Tuple[str, float]:
        """Match text against patterns for a single axis.

        Returns (label, confidence). If multiple labels match, returns the
        one with the most pattern hits. Default label gets _DEFAULT_CONFIDENCE.
        """
        text_lower = text.lower().strip()
        best_label = default
        best_hits = 0

        for label, regexes in patterns.items():
            hits = sum(
                1 for regex in regexes
                if re.search(regex, text_lower, re.IGNORECASE)
            )
            if hits > best_hits:
                best_hits = hits
                best_label = label

        confidence = _MATCHED_CONFIDENCE if best_hits > 0 else _DEFAULT_CONFIDENCE
        return best_label, confidence

    def _classify_intent(self, text: str) -> str:
        label, _ = self._classify_axis(text, self.INTENT_PATTERNS, "statement")
        return label

    def _classify_domain(self, text: str) -> str:
        label, _ = self._classify_axis(text, self.DOMAIN_PATTERNS, "personal")
        return label

    def _classify_emotion(self, text: str) -> str:
        label, _ = self._classify_axis(text, self.EMOTION_PATTERNS, "neutral")
        return label
