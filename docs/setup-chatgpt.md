# Setting Up Lore with ChatGPT

> **Experimental.** ChatGPT does not natively support MCP. This guide uses an MCP bridge to connect Lore to ChatGPT. The setup is more involved than native MCP clients and may break as bridge projects evolve.

## Prerequisites

- Python 3.9+
- A ChatGPT Plus or Team subscription (for plugin/action support)
- An MCP-to-HTTP bridge (e.g., [mcp-proxy](https://github.com/nichochar/mcp-proxy) or similar)

## How It Works

Since ChatGPT does not support the MCP protocol directly, you need a bridge that:

1. Runs the Lore MCP server locally
2. Exposes its tools as an HTTP API
3. Registers that API as a ChatGPT Action or plugin

## Configuration

### Step 1: Install Lore and the bridge

```bash
pip install lore-sdk
pip install mcp-proxy  # or your chosen bridge
```

### Step 2: Start the MCP bridge

```bash
mcp-proxy --command "uvx lore-memory" --port 3000
```

This starts Lore's MCP server behind an HTTP proxy on port 3000.

Set environment variables before starting:

```bash
export LORE_PROJECT="my-project"

# Optional LLM features:
# export LORE_ENRICHMENT_ENABLED=true
# export LORE_LLM_PROVIDER=openai
# export LORE_LLM_API_KEY=sk-...

mcp-proxy --command "uvx lore-memory" --port 3000
```

### Step 3: Expose the bridge

For ChatGPT to reach your local bridge, you need a public URL. Options:

- **ngrok:** `ngrok http 3000`
- **Cloudflare Tunnel:** `cloudflared tunnel --url http://localhost:3000`

### Step 4: Register as a ChatGPT Action

1. Go to ChatGPT settings and create a custom GPT or Action.
2. Point the Action URL to your public bridge URL.
3. Import the OpenAPI schema from the bridge (most bridges auto-generate this).

## Verify It Works

1. In ChatGPT, ask: "Remember that our API uses REST with JSON responses"
2. Ask: "What do you know about our API?"
3. You should see the bridge forwarding tool calls to Lore.

## Limitations

- Requires a running bridge process and public URL
- Latency is higher than native MCP integrations
- Bridge projects are third-party and may have their own limitations
- Not all 20 Lore tools may be exposed depending on the bridge's capabilities

## Troubleshooting

| Problem | Solution |
|---------|----------|
| ChatGPT cannot reach the bridge | Verify your tunnel (ngrok/cloudflare) is running and the URL is correct. |
| Tools not appearing | Check the bridge's OpenAPI schema is correctly generated. Restart the bridge. |
| "No module named lore" | Run `pip install lore-sdk` in the same environment the bridge uses. |
| High latency | This is expected with the bridge architecture. Consider using a native MCP client for better performance. |
