"""ConversationExtractor — orchestrates extract -> dedup -> store pipeline."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from lore.conversation.prompts import CONVERSATION_EXTRACT_PROMPT
from lore.types import ConversationJob, ConversationMessage

if TYPE_CHECKING:
    from lore.lore import Lore

logger = logging.getLogger(__name__)

# LLM type -> valid memory type mapping
_TYPE_MAP: Dict[str, str] = {
    "fact": "fact",
    "decision": "general",
    "preference": "preference",
    "lesson": "lesson",
    "correction": "general",
}


class ConversationExtractor:
    """Orchestrates: concat -> extract -> dedup -> store."""

    def __init__(
        self,
        lore: "Lore",
        dedup_threshold: float = 0.92,
    ) -> None:
        self._lore = lore
        self._dedup_threshold = dedup_threshold

    def extract(
        self,
        messages: List[ConversationMessage],
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        project: Optional[str] = None,
    ) -> ConversationJob:
        """Run the full extraction pipeline synchronously."""
        from ulid import ULID

        start = time.monotonic()
        job_id = str(ULID())

        # Stage 1: VALIDATE
        if not messages:
            raise ValueError("messages must be non-empty")
        if self._lore._enrichment_pipeline is None:
            raise RuntimeError(
                "Conversation extraction requires an LLM. "
                "Initialize Lore with enrichment=True and configure an LLM provider."
            )

        job = ConversationJob(
            job_id=job_id,
            status="processing",
            message_count=len(messages),
        )

        # Stage 2: CONCATENATE
        transcript = self._format_transcript(messages)

        # Stage 3: CHUNK
        from lore.conversation.chunker import ConversationChunker

        chunker = ConversationChunker()
        chunks = chunker.chunk(messages)

        # Stage 4: EXTRACT per chunk
        all_candidates: List[Dict[str, Any]] = []
        errors: List[str] = []
        for i, chunk in enumerate(chunks):
            chunk_transcript = self._format_transcript(chunk)
            try:
                candidates = self._extract_candidates(chunk_transcript)
                all_candidates.extend(candidates)
            except Exception as e:
                logger.warning("LLM extraction failed for chunk %d: %s", i, e)
                if len(chunks) > 1:
                    errors.append(f"Chunk {i} failed: {e}")
                    continue
                raise RuntimeError(f"Extraction failed: {e}")

        # Stage 5 + 6: DEDUP + STORE
        for candidate in all_candidates:
            content = candidate["content"]

            # Check for duplicates
            if self._is_duplicate(content):
                job.duplicates_skipped += 1
                continue

            # Store via lore.remember()
            memory_type = self._map_type(candidate.get("type", "general"))
            metadata: Dict[str, Any] = {
                "source": "conversation",
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "conversation_length": len(messages),
            }
            if user_id:
                metadata["user_id"] = user_id
            if session_id:
                metadata["session_id"] = session_id
            # Add extraction model info
            if self._lore._enrichment_pipeline and self._lore._enrichment_pipeline.llm:
                metadata["extraction_model"] = self._lore._enrichment_pipeline.llm.model

            memory_id = self._lore.remember(
                content=content,
                type=memory_type,
                tier="long",
                tags=candidate.get("tags", []),
                metadata=metadata,
                source="conversation",
                project=project or self._lore.project,
                confidence=candidate.get("confidence", 0.8),
            )
            job.memory_ids.append(memory_id)

        job.memories_extracted = len(job.memory_ids)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        job.processing_time_ms = elapsed_ms

        if errors:
            job.error = "; ".join(errors)
            # If we got some memories, still mark as completed
            job.status = "completed" if job.memories_extracted > 0 else "failed"
        else:
            job.status = "completed"

        return job

    def _format_transcript(self, messages: List[ConversationMessage]) -> str:
        """Format messages into a structured transcript."""
        lines = []
        for msg in messages:
            lines.append(f"[{msg.role}]: {msg.content}")
        return "\n\n".join(lines)

    def _extract_candidates(self, transcript: str) -> List[Dict[str, Any]]:
        """Call LLM to extract memory candidates from transcript."""
        prompt = CONVERSATION_EXTRACT_PROMPT.format(transcript=transcript)
        response = self._lore._enrichment_pipeline.llm.complete(prompt)
        return self._parse_extraction_response(response)

    def _parse_extraction_response(self, response: str) -> List[Dict[str, Any]]:
        """Parse LLM JSON response. Best-effort: returns partial results."""
        text = response.strip()
        # Strip markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Conversation extract: malformed JSON: %s", text[:200])
            return []

        memories = data.get("memories", [])
        if not isinstance(memories, list):
            return []

        valid: List[Dict[str, Any]] = []
        for m in memories:
            if not isinstance(m, dict):
                continue
            content = str(m.get("content", "")).strip()
            if not content:
                continue
            valid.append({
                "content": content,
                "type": m.get("type", "general"),
                "confidence": max(0.0, min(1.0, float(m.get("confidence", 0.8)))),
                "tags": [str(t).lower() for t in m.get("tags", []) if isinstance(t, str)][:5],
            })
        return valid

    def _is_duplicate(self, content: str) -> bool:
        """Check if candidate memory is too similar to existing memories."""
        results = self._lore.recall(content, limit=3)
        for r in results:
            if r.score >= self._dedup_threshold:
                return True
        return False

    @staticmethod
    def _map_type(llm_type: str) -> str:
        """Map LLM extraction type to valid memory type."""
        return _TYPE_MAP.get(llm_type, "general")
