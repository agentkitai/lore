"""Demonstration of Lore's automatic PII redaction."""

from lore import Lore

lore = Lore(db_path="/tmp/lore_redaction.db")

# Publish a lesson containing sensitive data
lesson_id = lore.publish(
    problem="Auth failed for user john@example.com with key sk-abc123def456ghi789jkl012mno",
    resolution="The API key was expired. Contact admin at +1-555-867-5309",
    tags=["auth", "api-key"],
    confidence=0.9,
)

# Retrieve the stored lesson — sensitive data is redacted
lesson = lore.get(lesson_id)
print("Stored problem:")
print(f"  {lesson.problem}")
print()
print("Stored resolution:")
print(f"  {lesson.resolution}")
print()

# Custom redaction patterns
lore2 = Lore(
    db_path="/tmp/lore_redaction2.db",
    redact_patterns=[
        (r"ACCT-\d{8}", "account_id"),
        (r"ORD-[A-Z0-9]{10}", "order_id"),
    ],
)

lesson_id2 = lore2.publish(
    problem="Payment failed for ACCT-12345678 on order ORD-AB12CD34EF",
    resolution="Retry with updated billing info",
)

lesson2 = lore2.get(lesson_id2)
print("Custom redaction:")
print(f"  {lesson2.problem}")

lore.close()
lore2.close()
