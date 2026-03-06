# Quickstart

Get Lore running in 5 minutes. No API keys required for basic use.

## Install

```bash
pip install lore-sdk
```

Or run the MCP server directly without installing:

```bash
uvx lore-memory
```

## Configure Your AI Tool

Add Lore to whichever AI tool you use:

- [Claude Desktop](setup-claude-desktop.md)
- [Claude Code](setup-claude-code.md)
- [Cursor](setup-cursor.md)
- [VS Code / Copilot](setup-vscode.md)
- [Windsurf](setup-windsurf.md)
- [Cline](setup-cline.md)
- [ChatGPT](setup-chatgpt.md)

Or see [MCP Setup](mcp-setup.md) for the full index and common configuration options.

## Try It

Once configured, try these in your AI tool:

**Save a memory:**

> "Remember that our API uses REST with JSON responses and all endpoints require authentication via Bearer tokens."

**Recall memories:**

> "What do you know about our API?"

**Check what entities Lore knows about:**

> "Show me the entity map from Lore."

## Use the CLI

The CLI works immediately after install:

```bash
# Store a memory
lore remember "Always use exponential backoff for Stripe rate limits"

# Search memories
lore recall "how to handle rate limits"

# List all memories
lore memories

# View statistics
lore stats
```

## Enable LLM Features (Optional)

By default, Lore uses local ONNX embeddings and rule-based classification. No API key is needed.

To unlock LLM-powered enrichment, fact extraction, and smart classification, add these environment variables to your MCP config:

```
LORE_ENRICHMENT_ENABLED=true
LORE_LLM_PROVIDER=anthropic
LORE_LLM_MODEL=claude-sonnet-4-20250514
LORE_LLM_API_KEY=sk-ant-...
```

Or for OpenAI:

```
LORE_LLM_PROVIDER=openai
LORE_LLM_MODEL=gpt-4o-mini
LORE_LLM_API_KEY=sk-...
```

LLM features include:
- **Enrichment** -- automatic topic extraction, sentiment analysis, entity recognition, and categorization
- **Fact extraction** -- structured (subject, predicate, object) triples from unstructured text
- **LLM classification** -- higher-accuracy intent/domain/emotion classification
- **Consolidation summaries** -- LLM-generated summaries when merging related memories

## Next Steps

- [Architecture overview](architecture.md) -- understand how Lore works
- [API Reference](api-reference.md) -- complete tool, CLI, and SDK reference
- [Migration Guide](migration-v0.5-to-v0.6.md) -- upgrading from v0.5
- [Self-hosted deployment](self-hosted.md) -- run Lore Cloud on your own infrastructure
