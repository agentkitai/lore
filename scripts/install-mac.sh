#!/usr/bin/env bash
# Lore — Full Mac Install (Postgres + pgvector + enrichment pipeline)
# Usage: curl -sSL <url> | bash
#    or: bash install-mac.sh [--api-key YOUR_KEY] [--llm-provider anthropic|openai] [--llm-key sk-...]
#    or: bash install-mac.sh --max-proxy    (uses Claude Max subscription, zero cost)
set -euo pipefail

# ── Defaults ───────────────────────────────────────────────────────
LORE_PORT=8765
LORE_API_KEY="${LORE_API_KEY:-lore_$(openssl rand -hex 16)}"
LLM_PROVIDER="${LORE_LLM_PROVIDER:-}"
LLM_KEY=""
ENRICHMENT_MODEL="claude-haiku-4-5"
USE_MAX_PROXY=false
MAX_PROXY_PORT=3456

# ── Parse args ─────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --api-key)    LORE_API_KEY="$2"; shift 2 ;;
    --llm-provider) LLM_PROVIDER="$2"; shift 2 ;;
    --llm-key)    LLM_KEY="$2"; shift 2 ;;
    --model)      ENRICHMENT_MODEL="$2"; shift 2 ;;
    --port)       LORE_PORT="$2"; shift 2 ;;
    --max-proxy)  USE_MAX_PROXY=true; shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo "🧠 Lore — Full Mac Install"
echo "=========================="
echo ""

# ── 1. Homebrew ────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
  echo "❌ Homebrew not found. Install it first:"
  echo '   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  exit 1
fi
echo "✅ Homebrew found"

# ── 2. PostgreSQL ──────────────────────────────────────────────────
PG_VERSION=""
for v in 17 16 15; do
  if brew list "postgresql@$v" &>/dev/null; then
    PG_VERSION=$v
    break
  fi
done

if [ -z "$PG_VERSION" ]; then
  echo "📦 Installing PostgreSQL 16..."
  brew install postgresql@16
  PG_VERSION=16
  echo "🔄 Starting PostgreSQL..."
  brew services start "postgresql@$PG_VERSION"
  sleep 2
else
  echo "✅ PostgreSQL $PG_VERSION found"
  # Ensure it's running
  if ! brew services list | grep "postgresql@$PG_VERSION" | grep -q started; then
    echo "🔄 Starting PostgreSQL..."
    brew services start "postgresql@$PG_VERSION"
    sleep 2
  fi
fi

PG_CONFIG="$(brew --prefix "postgresql@$PG_VERSION")/bin/pg_config"
export PATH="$(brew --prefix "postgresql@$PG_VERSION")/bin:$PATH"

# ── 3. pgvector ────────────────────────────────────────────────────
PG_SHAREDIR="$($PG_CONFIG --sharedir)"
PG_PKGLIBDIR="$($PG_CONFIG --pkglibdir)"
VECTOR_CONTROL="$PG_SHAREDIR/extension/vector.control"

if [ -f "$VECTOR_CONTROL" ]; then
  echo "✅ pgvector already installed (found $VECTOR_CONTROL)"
else
  echo "📦 Installing pgvector for PostgreSQL $PG_VERSION..."

  # Try Homebrew first
  brew install pgvector 2>&1 || true

  # Check if Homebrew put it in the right place
  if [ ! -f "$VECTOR_CONTROL" ]; then
    echo "  Homebrew pgvector didn't target PostgreSQL $PG_VERSION, building from source..."
    TMPDIR=$(mktemp -d)
    git clone --branch v0.8.0 --depth 1 https://github.com/pgvector/pgvector.git "$TMPDIR/pgvector"
    cd "$TMPDIR/pgvector"
    make PG_CONFIG="$PG_CONFIG"
    make install PG_CONFIG="$PG_CONFIG"
    cd -
    rm -rf "$TMPDIR"
  fi

  # Final verify
  if [ -f "$VECTOR_CONTROL" ]; then
    echo "✅ pgvector installed for PostgreSQL $PG_VERSION"
  else
    echo "❌ pgvector installation failed. Expected: $VECTOR_CONTROL"
    exit 1
  fi

  # Restart Postgres to pick up the extension
  brew services restart "postgresql@$PG_VERSION"
  sleep 2
fi

# ── 4. Create database ────────────────────────────────────────────
if psql -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw lore; then
  echo "✅ Database 'lore' exists"
else
  echo "📦 Creating database 'lore'..."
  createdb lore
fi

echo "Enabling pgvector extension..."
if ! psql lore -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>&1; then
  echo "❌ Failed to create vector extension. Trying with superuser..."
  if ! psql lore -c "CREATE EXTENSION IF NOT EXISTS vector;" -U postgres 2>&1; then
    echo "❌ pgvector extension failed. You may need to:"
    echo "   1. Verify pgvector is installed: ls $(pg_config --pkglibdir)/vector.so"
    echo "   2. Restart Postgres: brew services restart postgresql@$PG_VERSION"
    echo "   3. Try: psql lore -c 'CREATE EXTENSION vector;'"
    exit 1
  fi
fi
echo "✅ pgvector extension enabled"

# ── 5. Python + Lore ──────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "📦 Installing Python..."
  brew install python@3.12
fi

echo "📦 Installing Lore SDK..."
pip3 install --quiet "lore-sdk[server,enrichment]"
echo "✅ Lore SDK installed ($(pip3 show lore-sdk 2>/dev/null | grep Version | cut -d' ' -f2))"

# ── 6. Claude Max Proxy (optional) ────────────────────────────────
if [ "$USE_MAX_PROXY" = true ]; then
  echo "📦 Installing Claude Max API Proxy..."
  if ! command -v node &>/dev/null; then
    brew install node
  fi
  npm install -g claude-max-api-proxy

  # Find the binary (npm global bin might not be in PATH)
  MAX_PROXY_BIN=$(npm bin -g 2>/dev/null)/claude-max-api-proxy
  if [ ! -f "$MAX_PROXY_BIN" ]; then
    MAX_PROXY_BIN=$(npm prefix -g)/bin/claude-max-api-proxy
  fi

  # Ensure npm global bin is in PATH
  NPM_BIN_DIR=$(npm prefix -g)/bin
  if ! echo "$PATH" | grep -q "$NPM_BIN_DIR"; then
    SHELL_RC="$HOME/.zshrc"
    [ -f "$HOME/.bashrc" ] && [ ! -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.bashrc"
    if ! grep -q "npm prefix" "$SHELL_RC" 2>/dev/null; then
      echo 'export PATH="$(npm prefix -g)/bin:$PATH"' >> "$SHELL_RC"
      echo "  Added npm bin to PATH in $SHELL_RC"
    fi
    export PATH="$NPM_BIN_DIR:$PATH"
  fi

  # Create LaunchAgent for the proxy
  PROXY_PLIST="$HOME/Library/LaunchAgents/com.claude-max-api-proxy.plist"
  cat > "$PROXY_PLIST" <<PXML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude-max-api-proxy</string>
    <key>ProgramArguments</key>
    <array>
        <string>$MAX_PROXY_BIN</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/claude-max-proxy.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/claude-max-proxy.log</string>
</dict>
</plist>
PXML
  launchctl unload "$PROXY_PLIST" 2>/dev/null || true
  launchctl load "$PROXY_PLIST"

  # Wait for proxy to start
  echo -n "⏳ Waiting for Max proxy..."
  for i in $(seq 1 10); do
    if curl -sf "http://localhost:$MAX_PROXY_PORT/v1/models" &>/dev/null; then
      echo " ready!"
      break
    fi
    sleep 1
    echo -n "."
  done
  echo "✅ Claude Max API Proxy running on port $MAX_PROXY_PORT"

  # Set LLM provider to route through proxy
  LLM_PROVIDER="openai"
fi

# ── 7. Environment file ──────────────────────────────────────────
LORE_ENV="$HOME/.lore/env"
mkdir -p "$HOME/.lore"

cat > "$LORE_ENV" <<EOF
# Lore server configuration
# Generated by install-mac.sh on $(date -Iseconds)
LORE_STORE=postgres
LORE_PG_URL=postgresql://localhost/lore
LORE_API_KEY=$LORE_API_KEY
LORE_PORT=$LORE_PORT

# Enrichment pipeline
LORE_ENRICHMENT_ENABLED=true
LORE_CLASSIFY=true
LORE_FACT_EXTRACTION=true
LORE_KNOWLEDGE_GRAPH=true
EOF

# Add LLM config if provided
if [ -n "$LLM_PROVIDER" ]; then
  cat >> "$LORE_ENV" <<EOF

# LLM provider
LORE_LLM_PROVIDER=$LLM_PROVIDER
LORE_ENRICHMENT_MODEL=$ENRICHMENT_MODEL
EOF
  if [ "$USE_MAX_PROXY" = true ]; then
    echo "OPENAI_API_BASE=http://localhost:$MAX_PROXY_PORT/v1" >> "$LORE_ENV"
  elif [ "$LLM_PROVIDER" = "anthropic" ] && [ -n "$LLM_KEY" ]; then
    echo "ANTHROPIC_API_KEY=$LLM_KEY" >> "$LORE_ENV"
  elif [ "$LLM_PROVIDER" = "openai" ] && [ -n "$LLM_KEY" ]; then
    echo "OPENAI_API_KEY=$LLM_KEY" >> "$LORE_ENV"
  fi
fi

echo "✅ Config saved to $LORE_ENV"

# ── 8. LaunchAgent (auto-start on login) ──────────────────────────
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/com.lore.server.plist"
mkdir -p "$PLIST_DIR"

LORE_BIN=$(command -v lore || echo "$(python3 -c 'import site; print(site.USER_BASE)')/bin/lore")

cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.lore.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>$LORE_BIN</string>
        <string>serve</string>
        <string>--port</string>
        <string>$LORE_PORT</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
$(while IFS='=' read -r key val; do
    [[ -z "$key" || "$key" == \#* ]] && continue
    echo "        <key>$key</key>"
    echo "        <string>$val</string>"
done < "$LORE_ENV")
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/lore-server.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/lore-server.log</string>
</dict>
</plist>
EOF

# Load (or reload) the service
launchctl unload "$PLIST_FILE" 2>/dev/null || true
launchctl load "$PLIST_FILE"
echo "✅ Lore server started (auto-starts on login)"

# ── 9. Wait for server to be ready ───────────────────────────────
echo -n "⏳ Waiting for server..."
for i in $(seq 1 15); do
  if curl -sf "http://localhost:$LORE_PORT/health" &>/dev/null; then
    echo " ready!"
    break
  fi
  sleep 1
  echo -n "."
done

if ! curl -sf "http://localhost:$LORE_PORT/health" &>/dev/null; then
  echo ""
  echo "⚠️  Server didn't start. Check logs: tail -f /tmp/lore-server.log"
  echo "   You can start manually: source ~/.lore/env && lore serve"
else
  echo "✅ Server healthy at http://localhost:$LORE_PORT"
fi

# ── 10. Setup agent hooks ────────────────────────────────────────
echo ""
echo "📎 Setting up agent hooks..."

# Find the lore binary (pip may install to different locations on Mac)
LORE_BIN=$(command -v lore 2>/dev/null || python3 -c "import site; print(site.USER_BASE + '/bin/lore')" 2>/dev/null || echo "")
if [ -z "$LORE_BIN" ] || [ ! -f "$LORE_BIN" ]; then
  # Try common pip install locations
  for candidate in \
    "$(python3 -m site --user-base 2>/dev/null)/bin/lore" \
    "/usr/local/bin/lore" \
    "$HOME/.local/bin/lore" \
    "$HOME/Library/Python/3.12/bin/lore" \
    "$HOME/Library/Python/3.11/bin/lore" \
    "$HOME/Library/Python/3.10/bin/lore"; do
    if [ -f "$candidate" ]; then
      LORE_BIN="$candidate"
      break
    fi
  done
fi

if [ -z "$LORE_BIN" ] || [ ! -f "$LORE_BIN" ]; then
  echo "  ⚠️  'lore' binary not found in PATH. Falling back to python3 -m lore"
  LORE_CMD="python3 -m lore"
else
  echo "  Using: $LORE_BIN"
  LORE_CMD="$LORE_BIN"
fi

# Claude Code hook
echo "  Installing Claude Code hook..."
$LORE_CMD setup claude-code --server-url "http://localhost:$LORE_PORT" --api-key "$LORE_API_KEY" && \
  echo "  ✅ Claude Code hook installed" || echo "  ❌ Claude Code setup failed (see error above)"

# Codex hook
echo "  Installing Codex CLI hook..."
$LORE_CMD setup codex --server-url "http://localhost:$LORE_PORT" --api-key "$LORE_API_KEY" && \
  echo "  ✅ Codex CLI hook installed" || echo "  ❌ Codex setup failed (see error above)"

# Cursor hook (project-level)
echo "  Installing Cursor hook..."
$LORE_CMD setup cursor --server-url "http://localhost:$LORE_PORT" --api-key "$LORE_API_KEY" && \
  echo "  ✅ Cursor hook installed" || echo "  ❌ Cursor setup failed (see error above)"

# Verify Claude Code hook specifically
if [ -f "$HOME/.claude/settings.json" ]; then
  if python3 -c "import json; s=json.load(open('$HOME/.claude/settings.json')); assert s.get('hooks',{}).get('UserPromptSubmit')" 2>/dev/null; then
    echo "  ✓ Verified: Claude Code UserPromptSubmit hook registered"
  else
    echo "  ⚠️  Claude Code settings.json exists but hook not registered"
    echo "     Contents: $(cat "$HOME/.claude/settings.json")"
  fi
else
  echo "  ⚠️  ~/.claude/settings.json not found — Claude Code may not be installed"
fi

echo ""
echo "════════════════════════════════════════"
echo "🧠 Lore is running!"
echo "════════════════════════════════════════"
echo ""
echo "  Server:   http://localhost:$LORE_PORT"
echo "  Health:   http://localhost:$LORE_PORT/health"
echo "  Config:   ~/.lore/env"
echo "  Logs:     /tmp/lore-server.log"
echo "  API Key:  $LORE_API_KEY"
echo ""
echo "Setup more agents:"
echo "  lore setup claude-code --server-url http://localhost:$LORE_PORT --api-key $LORE_API_KEY"
echo "  lore setup cursor     --server-url http://localhost:$LORE_PORT --api-key $LORE_API_KEY"
echo "  lore setup codex      --server-url http://localhost:$LORE_PORT --api-key $LORE_API_KEY"
echo ""
if [ "$USE_MAX_PROXY" = true ]; then
  echo "  Max Proxy: http://localhost:$MAX_PROXY_PORT (LaunchAgent)"
  echo "  Proxy Log: /tmp/claude-max-proxy.log"
  echo ""
elif [ -z "$LLM_PROVIDER" ]; then
  echo "⚠️  No LLM provider configured. Enrichment will skip LLM features."
  echo "   Re-run with --max-proxy (free, uses Max sub) or add manually:"
  echo "   LORE_LLM_PROVIDER=anthropic"
  echo "   ANTHROPIC_API_KEY=sk-ant-..."
  echo "   Then: launchctl unload ~/Library/LaunchAgents/com.lore.server.plist"
  echo "         launchctl load ~/Library/LaunchAgents/com.lore.server.plist"
  echo ""
fi
echo "Done! 🎉"
