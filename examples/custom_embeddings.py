"""Using Lore with a custom embedding function.

This example shows how to plug in your own embedding model.
Here we use a simple hash-based fake embedder for demonstration.
Replace with OpenAI, Cohere, or any embedding API in production.
"""

import hashlib

from lore import Lore


def fake_embedding_fn(text: str) -> list[float]:
    """A deterministic fake embedding for demonstration.

    In production, replace this with a real embedding model:

        import openai
        client = openai.OpenAI()

        def embed(text: str) -> list[float]:
            res = client.embeddings.create(
                model="text-embedding-3-small", input=text
            )
            return res.data[0].embedding
    """
    h = hashlib.sha256(text.encode()).digest()
    # Produce a 384-dim vector from the hash (repeating)
    vec = []
    for i in range(384):
        vec.append((h[i % 32] - 128) / 128.0)
    return vec


lore = Lore(
    db_path="/tmp/lore_custom_embed.db",
    embedding_fn=fake_embedding_fn,
)

lore.publish(
    problem="SMS sending fails for international numbers",
    resolution="Use E.164 format with country code prefix",
    tags=["sms", "international"],
    confidence=0.85,
)

results = lore.query("phone number formatting issues")
for r in results:
    print(f"[{r.score:.3f}] {r.lesson.problem}")

lore.close()
