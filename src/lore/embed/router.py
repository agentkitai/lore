"""Dual embedding router — routes content to prose or code embedder."""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Literal, Tuple

from lore.embed.base import Embedder

logger = logging.getLogger(__name__)


def detect_content_type(text: str) -> Literal["code", "prose"]:
    """Classify text as code or prose using lightweight heuristics.

    Returns ``"code"`` when the text looks like source code, otherwise
    ``"prose"``.  Pure regex/string operations — no ML, <0.1 ms.
    """
    indicators = 0

    # Syntax characters at end of lines: { } ; ( )
    if re.search(r"[{};()]\s*$", text, re.MULTILINE):
        indicators += 2

    # Language keywords
    kw_matches = re.findall(
        r"\b(def |function |class |import |from |const |let |var |return |if |elif |else:)",
        text,
    )
    if kw_matches:
        indicators += 2
    if len(kw_matches) >= 3:
        indicators += 1  # multiple keywords → strong code signal

    # Operator patterns common in code
    if re.search(r"(=>|->|::|\.\.)", text):
        indicators += 1

    # Indentation-heavy (proxy for code blocks)
    lines = text.split("\n")
    if len(lines) > 1:
        indented = sum(1 for ln in lines if ln.startswith("  ") or ln.startswith("\t"))
        if indented / len(lines) > 0.4:
            indicators += 1

    # Fenced code blocks
    if re.search(r"```", text):
        indicators += 2

    # Camel/snake identifiers like myFunc or my_func chained with dots
    if re.search(r"\w+\.\w+\(", text):
        indicators += 1

    return "code" if indicators >= 3 else "prose"


class EmbeddingRouter(Embedder):
    """Routes content to a prose or code embedder based on heuristics.

    Implements the :class:`Embedder` protocol so it can be used as a
    drop-in replacement throughout the Lore SDK.

    When only a prose embedder is available (code model download failed),
    falls back to prose for all content.
    """

    def __init__(
        self,
        prose_embedder: Embedder,
        code_embedder: Embedder | None = None,
    ) -> None:
        self._prose = prose_embedder
        self._code = code_embedder or prose_embedder
        self._last_embed_model: str = "prose"

    @property
    def last_embed_model(self) -> str:
        """The model tag (``"prose"`` or ``"code"``) used by the last
        :meth:`embed` call.  Useful for storing in memory metadata."""
        return self._last_embed_model

    # --- Embedder protocol ---------------------------------------------------

    def embed(self, text: str) -> List[float]:
        """Embed *text*, routing to the appropriate model."""
        ctype = detect_content_type(text)
        self._last_embed_model = ctype
        if ctype == "code":
            return self._code.embed(text)
        return self._prose.embed(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts, grouping by detected content type."""
        if not texts:
            return []

        # Classify each text
        types: List[Literal["code", "prose"]] = [
            detect_content_type(t) for t in texts
        ]

        # Gather indices per type
        groups: Dict[str, List[Tuple[int, str]]] = {"prose": [], "code": []}
        for i, (t, ctype) in enumerate(zip(texts, types)):
            groups[ctype].append((i, t))

        results: List[List[float] | None] = [None] * len(texts)  # type: ignore[assignment]

        for ctype, items in groups.items():
            if not items:
                continue
            indices, batch_texts = zip(*items)
            embedder = self._code if ctype == "code" else self._prose
            vecs = embedder.embed_batch(list(batch_texts))
            for idx, vec in zip(indices, vecs):
                results[idx] = vec

        return results  # type: ignore[return-value]

    # --- Dual query helpers ---------------------------------------------------

    def embed_query_dual(self, query: str) -> Dict[str, List[float]]:
        """Embed *query* with **both** models.

        Returns ``{"prose": [...], "code": [...]}``.  Used at search time
        so each memory can be compared against the model that embedded it.
        """
        prose_vec = self._prose.embed(query)
        code_vec = self._code.embed(query)
        return {"prose": prose_vec, "code": code_vec}
