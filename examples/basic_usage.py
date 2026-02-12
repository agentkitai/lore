"""Basic Lore usage — publish, query, and format lessons."""

from lore import Lore

# Initialize with defaults (SQLite at ~/.lore/default.db)
# Using a temp path for this example; omit db_path for the default location.
lore = Lore(db_path="/tmp/lore_example.db")

# Publish some lessons
lore.publish(
    problem="Stripe API returns 429 after 100 requests/min",
    resolution="Add exponential backoff starting at 1s, cap at 32s",
    tags=["stripe", "rate-limit", "api"],
    confidence=0.9,
)

lore.publish(
    problem="OpenAI API times out on large prompts (>100K tokens)",
    resolution="Split into chunks of 50K tokens, process sequentially",
    tags=["openai", "timeout", "chunking"],
    confidence=0.8,
)

lore.publish(
    problem="PostgreSQL connection pool exhausted under load",
    resolution="Increase pool size to 20, add connection timeout of 30s",
    tags=["postgres", "connection-pool"],
    confidence=0.7,
)

# Query for relevant lessons
results = lore.query("how to handle API rate limits")
print(f"Found {len(results)} results:\n")

for r in results:
    print(f"  [{r.score:.3f}] {r.lesson.problem}")
    print(f"           → {r.lesson.resolution}\n")

# Format for system prompt injection
prompt = lore.as_prompt(results)
print("--- Prompt section ---")
print(prompt)

# Clean up
lore.close()
