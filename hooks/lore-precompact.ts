/**
 * OpenClaw pre-compaction hook — saves session snapshot before context
 * window compaction fires.
 *
 * Event:    session:compacting
 * Blocking: false (compaction proceeds regardless)
 * Timeout:  3000ms
 */

const LORE_API_URL = process.env.LORE_API_URL || "http://localhost:8765";
const LORE_API_KEY = process.env.LORE_API_KEY || "";
const MAX_CONTENT_LENGTH = 4000;

interface CompactionEvent {
  session_id?: string;
  messages?: Array<{ role: string; content: string }>;
}

export default async function handler(event: CompactionEvent): Promise<void> {
  try {
    const messages = event.messages || [];
    if (messages.length === 0) return;

    // Concatenate message contents, capped at MAX_CONTENT_LENGTH
    let content = "";
    for (const msg of messages) {
      const piece = `[${msg.role}] ${msg.content}\n`;
      if (content.length + piece.length > MAX_CONTENT_LENGTH) {
        content += piece.slice(0, MAX_CONTENT_LENGTH - content.length);
        break;
      }
      content += piece;
    }

    if (!content.trim()) return;

    const body = JSON.stringify({
      content,
      session_id: event.session_id || undefined,
      title: "Pre-compaction snapshot",
      tags: ["auto-compaction"],
    });

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (LORE_API_KEY) {
      headers["Authorization"] = `Bearer ${LORE_API_KEY}`;
    }

    const response = await fetch(`${LORE_API_URL}/v1/snapshots`, {
      method: "POST",
      headers,
      body,
      signal: AbortSignal.timeout(3000),
    });

    if (response.ok) {
      const result = await response.json();
      console.log(`[lore-precompact] Snapshot saved: ${result.id}`);
    } else {
      console.warn(`[lore-precompact] Failed: ${response.status}`);
    }
  } catch (err) {
    // Fire-and-forget: log but never throw
    console.warn(`[lore-precompact] Error (non-blocking): ${err}`);
  }
}
