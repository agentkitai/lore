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

# ── 5. Python 3.10+ + Lore ────────────────────────────────────────
# Lore requires Python >=3.10. macOS ships 3.9 which silently installs ancient versions.
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
echo "  System Python: $PY_VERSION"

if python3 -c "import sys; assert sys.version_info >= (3, 10)" 2>/dev/null; then
  PY_BIN="python3"
  PIP_BIN="pip3"
else
  echo "⚠️  Python $PY_VERSION is too old (need 3.10+). Installing Python 3.12 via Homebrew..."
  brew install python@3.12
  PY_BIN="$(brew --prefix python@3.12)/bin/python3.12"
  PIP_BIN="$PY_BIN -m pip"
  echo "  Using: $PY_BIN ($(${PY_BIN} --version))"
fi

echo "📦 Installing Lore SDK..."
$PIP_BIN install --upgrade "lore-sdk[server,enrichment]"
LORE_VERSION=$($PY_BIN -c "import importlib.metadata; print(importlib.metadata.version('lore-sdk'))" 2>/dev/null || echo "unknown")
echo "✅ Lore SDK v$LORE_VERSION installed"

if [ "$LORE_VERSION" = "unknown" ] || $PY_BIN -c "from packaging.version import Version; assert Version('$LORE_VERSION') < Version('0.9.0')" 2>/dev/null; then
  echo "❌ Failed to install lore-sdk >= 0.9.0 (got $LORE_VERSION)"
  echo "   This usually means pip resolved an old version."
  echo "   Debug: $PIP_BIN install --upgrade --verbose 'lore-sdk[server,enrichment]'"
  exit 1
fi

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

# Find lore binary installed by the correct Python
LORE_BIN=$(command -v lore 2>/dev/null || echo "")
if [ -z "$LORE_BIN" ] || [ ! -f "$LORE_BIN" ]; then
  LORE_BIN=$($PY_BIN -c "import site; print(site.USER_BASE + '/bin/lore')" 2>/dev/null || echo "")
fi
if [ -z "$LORE_BIN" ] || [ ! -f "$LORE_BIN" ]; then
  for candidate in \
    "$($PY_BIN -m site --user-base 2>/dev/null)/bin/lore" \
    "$(brew --prefix python@3.12 2>/dev/null)/bin/lore" \
    "/opt/homebrew/bin/lore" \
    "/usr/local/bin/lore" \
    "$HOME/.local/bin/lore"; do
    if [ -f "$candidate" ]; then
      LORE_BIN="$candidate"
      break
    fi
  done
fi

# Build ProgramArguments as a plain string (arrays don't survive heredoc subshells)
if [ -z "$LORE_BIN" ] || [ ! -f "$LORE_BIN" ]; then
  echo "⚠️  lore binary not found, using '$PY_BIN -m lore' for LaunchAgent"
  PLIST_ARGS="        <string>$PY_BIN</string>
        <string>-m</string>
        <string>lore</string>
        <string>serve</string>
        <string>--port</string>
        <string>$LORE_PORT</string>"
else
  echo "  Lore binary: $LORE_BIN"
  PLIST_ARGS="        <string>$LORE_BIN</string>
        <string>serve</string>
        <string>--port</string>
        <string>$LORE_PORT</string>"
fi

cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.lore.server</string>
    <key>ProgramArguments</key>
    <array>
$PLIST_ARGS
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

# Verify plist
echo "  LaunchAgent plist:"
grep -A 2 "ProgramArguments" "$PLIST_FILE" | head -5
echo "  ..."

# Load (or reload) the service
launchctl unload "$PLIST_FILE" 2>/dev/null || true
launchctl load "$PLIST_FILE"
echo "✅ Lore server LaunchAgent loaded"

# ── 9. Wait for server to be ready ───────────────────────────────
echo -n "⏳ Waiting for server..."
for i in $(seq 1 20); do
  if curl -sf "http://localhost:$LORE_PORT/health" &>/dev/null; then
    echo " ready!"
    break
  fi
  sleep 1
  echo -n "."
done

if ! curl -sf "http://localhost:$LORE_PORT/health" &>/dev/null; then
  echo ""
  echo "❌ Server didn't start after 20s. Diagnosing..."
  echo ""
  echo "  Log output:"
  tail -20 /tmp/lore-server.log 2>/dev/null || echo "  (no log file)"
  echo ""
  echo "  LaunchAgent plist contents:"
  cat "$PLIST_FILE"
  echo ""
  echo "  Trying to start manually to see the error:"
  source "$LORE_ENV" 2>/dev/null
  timeout 5 $LORE_BIN serve --port $LORE_PORT 2>&1 || $PY_BIN -m lore serve --port $LORE_PORT 2>&1 &
  MANUAL_PID=$!
  sleep 3
  if curl -sf "http://localhost:$LORE_PORT/health" &>/dev/null; then
    echo "  ✅ Manual start worked! Killing manual process, fixing LaunchAgent..."
    kill $MANUAL_PID 2>/dev/null
  else
    kill $MANUAL_PID 2>/dev/null
    echo "  ❌ Manual start also failed. Check errors above."
    exit 1
  fi
else
  echo "✅ Server healthy at http://localhost:$LORE_PORT"
fi

# ── 10. Setup agent hooks ────────────────────────────────────────
echo ""
echo "📎 Setting up agent hooks..."

# Reuse LORE_BIN from step 8, or fall back to python module
if [ -n "$LORE_BIN" ] && [ -f "$LORE_BIN" ] && [ "$LORE_BIN" != "$PY_BIN" ]; then
  LORE_CMD="$LORE_BIN"
  echo "  Using: $LORE_CMD"
else
  LORE_CMD="$PY_BIN -m lore"
  echo "  Using: $LORE_CMD"
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
  if $PY_BIN -c "import json; s=json.load(open('$HOME/.claude/settings.json')); assert s.get('hooks',{}).get('UserPromptSubmit')" 2>/dev/null; then
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
