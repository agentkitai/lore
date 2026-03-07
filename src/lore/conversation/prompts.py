"""Extraction prompt template for conversation auto-extract."""

CONVERSATION_EXTRACT_PROMPT = """You are a memory extraction system. Analyze the following conversation and extract salient pieces of knowledge worth remembering long-term.

Extract these types of memories:
- **Facts**: Concrete pieces of information (e.g., "ECS memory limit should be 512MB")
- **Decisions**: Choices made during the conversation (e.g., "Using Fargate instead of EC2")
- **Preferences**: User or team preferences (e.g., "Prefers pytest over unittest")
- **Lessons**: Operational insights learned (e.g., "Deploy to staging first to catch memory issues")
- **Corrections**: When earlier information was corrected later in the conversation

Rules:
- Only extract genuinely useful, non-obvious knowledge
- Each memory should be self-contained and understandable without the conversation
- Do NOT extract greetings, acknowledgments, or trivial exchanges
- If the conversation has no extractable knowledge, return an empty list
- Assign a confidence score (0.0-1.0) based on how clearly stated the information is
- Suggest relevant tags (1-5 lowercase tags per memory)

Respond with JSON only:
{{
    "memories": [
        {{
            "content": "Clear, self-contained statement of the knowledge",
            "type": "fact|decision|preference|lesson|correction",
            "confidence": 0.9,
            "tags": ["relevant", "tags"]
        }}
    ]
}}

Conversation:
---
{transcript}
---"""
