"""Language detection enrichment plugin — simple heuristics, no deps."""

from __future__ import annotations

import re
from typing import Dict

# Common short words per language (top-frequency function words).
_LANG_MARKERS: Dict[str, set] = {
    "en": {"the", "is", "are", "was", "were", "have", "has", "and", "for", "not", "this", "that", "with"},
    "es": {"el", "la", "los", "las", "de", "en", "por", "con", "una", "que", "es", "del"},
    "fr": {"le", "la", "les", "de", "des", "est", "un", "une", "et", "en", "du", "pour"},
    "de": {"der", "die", "das", "ist", "ein", "eine", "und", "von", "mit", "auf", "den"},
    "pt": {"de", "que", "em", "um", "uma", "os", "das", "dos", "com", "por", "para"},
    "it": {"il", "di", "che", "la", "un", "una", "per", "del", "della", "con", "sono"},
    "zh": set(),  # detected by Unicode range
    "ja": set(),  # detected by Unicode range
}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_HIRAGANA_RE = re.compile(r"[\u3040-\u309f]")
_KATAKANA_RE = re.compile(r"[\u30a0-\u30ff]")

_WORD_RE = re.compile(r"[a-zA-Z\u00C0-\u024F]+")


class LanguageDetectPlugin:
    """Detect the dominant language of content using word-frequency heuristics."""

    name: str = "language_detect"

    def enrich(self, content: str, metadata: dict) -> dict:
        lang = self._detect(content)
        return {"language": lang}

    # ------------------------------------------------------------------

    def _detect(self, text: str) -> str:
        # CJK / Japanese check first (no word splitting needed)
        if _HIRAGANA_RE.search(text) or _KATAKANA_RE.search(text):
            return "ja"
        if _CJK_RE.search(text):
            return "zh"

        words = [w.lower() for w in _WORD_RE.findall(text)]
        if not words:
            return "unknown"

        word_set = set(words)
        best_lang = "en"
        best_score = 0

        for lang, markers in _LANG_MARKERS.items():
            if not markers:
                continue
            overlap = len(word_set & markers)
            if overlap > best_score:
                best_score = overlap
                best_lang = lang

        return best_lang
