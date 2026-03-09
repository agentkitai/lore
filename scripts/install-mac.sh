#!/usr/bin/env bash
# Lore — Full Mac Install (Postgres + pgvector + enrichment pipeline)
# Usage: bash install-mac.sh [--api-key KEY] [--llm-provider anthropic|openai] [--llm-key sk-...]
#        bash install-mac.sh --max-proxy    (uses Claude Max subscription, zero cost)
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
    --api-key)      LORE_API_KEY="$2"; shift 2 ;;
    --llm-provider) LLM_PROVIDER="$2"; shift 2 ;;
    --llm-key)      LLM_KEY="$2"; shift 2 ;;
    --model)        ENRICHMENT_MODEL="$2"; shift 2 ;;
    --port)         LORE_PORT="$2"; shift 2 ;;
    --max-proxy)    USE_MAX_PROXY=true; shift ;;
    *) echo "❌ Unknown arg: $1"; exit 1 ;;
  esac
done

echo ""
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
  if brew list "postgresql@$v" 2>&1 | grep -q "postgresql@$v"; then
    PG_VERSION=$v
    break
  fi
done

if [ -z "$PG_VERSION" ]; then
  echo "📦 Installing PostgreSQL 17..."
  brew install postgresql@17
  PG_VERSION=17
fi
echo "✅ PostgreSQL $PG_VERSION found"

# Ensure it's running
if ! brew services list | grep "postgresql@$PG_VERSION" | grep -q started; then
  echo "🔄 Starting PostgreSQL $PG_VERSION..."
  brew services start "postgresql@$PG_VERSION"
  sleep 3
fi
echo "✅ PostgreSQL $PG_VERSION is running"

PG_PREFIX="$(brew --prefix "postgresql@$PG_VERSION")"
PG_CONFIG="$PG_PREFIX/bin/pg_config"
export PATH="$PG_PREFIX/bin:$PATH"

if [ ! -x "$PG_CONFIG" ]; then
  echo "❌ pg_config not found at $PG_CONFIG"
  echo "   Try: brew reinstall postgresql@$PG_VERSION"
  exit 1
fi

# ── 3. pgvector ────────────────────────────────────────────────────
PG_SHAREDIR="$("$PG_CONFIG" --sharedir)"
VECTOR_CONTROL="$PG_SHAREDIR/extension/vector.control"

if [ -f "$VECTOR_CONTROL" ]; then
  echo "✅ pgvector installed (found $VECTOR_CONTROL)"
else
  echo "📦 Installing pgvector for PostgreSQL $PG_VERSION..."

  # Try Homebrew first — it may or may not target the right PG
  brew install pgvector 2>&1 || true

  if [ ! -f "$VECTOR_CONTROL" ]; then
    echo "⚠️  Homebrew pgvector didn't target PostgreSQL $PG_VERSION, building from source..."
    BUILD_DIR=$(mktemp -d)
    git clone --branch v0.8.0 --depth 1 https://github.com/pgvector/pgvector.git "$BUILD_DIR/pgvector"
    cd "$BUILD_DIR/pgvector"
    make PG_CONFIG="$PG_CONFIG"
    make install PG_CONFIG="$PG_CONFIG"
    cd - > /dev/null
    rm -rf "$BUILD_DIR"
  fi

  if [ -f "$VECTOR_CONTROL" ]; then
    echo "✅ pgvector installed for PostgreSQL $PG_VERSION"
  else
    echo "❌ pgvector installation failed"
    echo "   Expected: $VECTOR_CONTROL"
    echo "   Try building manually: https://github.com/pgvector/pgvector#installation"
    exit 1
  fi

  # Restart Postgres to pick up the new extension
  brew services restart "postgresql@$PG_VERSION"
  sleep 3
fi

# ── 4. Database ────────────────────────────────────────────────────
if psql -lqt 2>&1 | cut -d \| -f 1 | grep -qw lore; then
  echo "✅ Database 'lore' exists"
else
  echo "📦 Creating database 'lore'..."
  createdb lore
  echo "✅ Database 'lore' created"
fi

echo "📦 Enabling pgvector extension..."
if ! psql lore -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>&1; then
  echo "⚠️  Retrying with superuser..."
  if ! psql lore -c "CREATE EXTENSION IF NOT EXISTS vector;" -U postgres 2>&1; then
    echo "❌ pgvector extension failed"
    echo "   1. Verify: ls $("$PG_CONFIG" --pkglibdir)/vector.so"
    echo "   2. Restart: brew services restart postgresql@$PG_VERSION"
    echo "   3. Try: psql lore -c 'CREATE EXTENSION vector;'"
    exit 1
  fi
fi
echo "✅ pgvector extension enabled"

# ── 5. Python 3.10+ ───────────────────────────────────────────────
PY_BIN=""
PIP_CMD=""

# Check if system python3 is new enough
if command -v python3 &>/dev/null; then
  SYS_PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1 || echo "0.0")
  echo "  System Python: $SYS_PY_VER"
  if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>&1; then
    PY_BIN="python3"
  fi
fi

# If system python is too old, install 3.12 via Homebrew
if [ -z "$PY_BIN" ]; then
  echo "⚠️  Python < 3.10 detected. Installing Python 3.12 via Homebrew..."
  brew install python@3.12
  PY_BIN="$(brew --prefix python@3.12)/bin/python3.12"
  if [ ! -x "$PY_BIN" ]; then
    echo "❌ Python 3.12 not found at $PY_BIN after install"
    exit 1
  fi
  echo "✅ Python 3.12 installed: $PY_BIN"
fi

PIP_CMD="$PY_BIN -m pip"
echo "  Using Python: $PY_BIN ($($PY_BIN --version 2>&1))"

# ── 6. Lore SDK ───────────────────────────────────────────────────
echo "📦 Installing Lore SDK..."
$PIP_CMD install --upgrade "lore-sdk[server,enrichment]" 2>&1

LORE_VERSION=$($PY_BIN -c "import importlib.metadata; print(importlib.metadata.version('lore-sdk'))" 2>&1 || echo "unknown")
echo "  Installed version: $LORE_VERSION"

if [ "$LORE_VERSION" = "unknown" ]; then
  echo "❌ lore-sdk not found after install"
  echo "   Debug: $PIP_CMD install --upgrade --verbose 'lore-sdk[server,enrichment]'"
  exit 1
fi

# Verify >= 0.9.4
if $PY_BIN -c "
from packaging.version import Version
v = Version('$LORE_VERSION')
assert v >= Version('0.9.4'), f'Got {v}, need >= 0.9.4'
" 2>&1; then
  echo "✅ Lore SDK v$LORE_VERSION installed"
else
  echo "❌ lore-sdk version $LORE_VERSION is too old (need >= 0.9.4)"
  echo "   This usually means pip resolved an old version for your Python."
  echo "   Debug: $PIP_CMD install --upgrade --verbose 'lore-sdk[server,enrichment]'"
  exit 1
fi

# ── Find lore binary ──────────────────────────────────────────────
LORE_BIN=""

# Search known locations in priority order
PY_USER_BASE=$($PY_BIN -m site --user-base 2>&1 || echo "")

for candidate in \
  "$(command -v lore 2>&1 || echo "")" \
  "${PY_USER_BASE:+$PY_USER_BASE/bin/lore}" \
  "$(brew --prefix python@3.12 2>&1 || echo "")/bin/lore" \
  "/opt/homebrew/bin/lore" \
  "/usr/local/bin/lore" \
  "$HOME/.local/bin/lore" \
  "$HOME/Library/Python/3.12/bin/lore" \
  "$HOME/Library/Python/3.11/bin/lore" \
  "$HOME/Library/Python/3.10/bin/lore"; do
  if [ -n "$candidate" ] && [ -f "$candidate" ] && [ -x "$candidate" ]; then
    LORE_BIN="$candidate"
    break
  fi
done

# Fallback: use python -m lore
if [ -z "$LORE_BIN" ]; then
  echo "⚠️  lore binary not found in PATH, will use: $PY_BIN -m lore"
  LORE_BIN=""
fi

# Build the command to run lore
if [ -n "$LORE_BIN" ]; then
  LORE_CMD="$LORE_BIN"
  echo "  Lore binary: $LORE_BIN"
else
  LORE_CMD="$PY_BIN -m lore"
  echo "  Lore command: $LORE_CMD"
fi

# Verify lore serve works before we create a LaunchAgent for it
echo "  Testing 'lore serve --help'..."
if ! $LORE_CMD serve --help > /dev/null 2>&1; then
  echo "❌ 'lore serve --help' failed"
  echo "   Output:"
  $LORE_CMD serve --help 2>&1 || true
  exit 1
fi
echo "✅ 'lore serve' command works"

# ── 7. Claude Max Proxy (optional) ────────────────────────────────
if [ "$USE_MAX_PROXY" = true ]; then
  echo ""
  echo "📦 Installing Claude Max API Proxy..."
  if ! command -v node &>/dev/null; then
    echo "📦 Installing Node.js..."
    brew install node
  fi
  npm install -g claude-max-api-proxy 2>&1

  # Find the binary
  NPM_PREFIX="$(npm prefix -g 2>&1)"
  NPM_BIN_DIR="$NPM_PREFIX/bin"
  MAX_PROXY_BIN="$NPM_BIN_DIR/claude-max-api-proxy"

  if [ ! -f "$MAX_PROXY_BIN" ]; then
    echo "❌ claude-max-api-proxy not found at $MAX_PROXY_BIN"
    exit 1
  fi

  # Ensure npm global bin is in PATH persistently
  if ! echo "$PATH" | tr ':' '\n' | grep -qx "$NPM_BIN_DIR"; then
    SHELL_RC="$HOME/.zshrc"
    if [ ! -f "$SHELL_RC" ]; then
      SHELL_RC="$HOME/.bashrc"
    fi
    if [ -f "$SHELL_RC" ] && ! grep -q 'npm prefix -g' "$SHELL_RC" 2>&1; then
      echo 'export PATH="$(npm prefix -g)/bin:$PATH"' >> "$SHELL_RC"
      echo "  Added npm global bin to PATH in $SHELL_RC"
    fi
    export PATH="$NPM_BIN_DIR:$PATH"
  fi

  # Create LaunchAgent for the proxy
  PROXY_PLIST="$HOME/Library/LaunchAgents/com.claude-max-api-proxy.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PROXY_PLIST" <<PXML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude-max-api-proxy</string>
    <key>ProgramArguments</key>
    <array>
        <string>${MAX_PROXY_BIN}</string>
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
  launchctl unload "$PROXY_PLIST" 2>&1 || true
  launchctl load "$PROXY_PLIST"

  echo -n "⏳ Waiting for Max proxy..."
  PROXY_READY=false
  for i in $(seq 1 15); do
    if curl -sf "http://localhost:$MAX_PROXY_PORT/v1/models" > /dev/null 2>&1; then
      PROXY_READY=true
      break
    fi
    sleep 1
    echo -n "."
  done
  echo ""

  if [ "$PROXY_READY" = true ]; then
    echo "✅ Claude Max API Proxy running on port $MAX_PROXY_PORT"
  else
    echo "⚠️  Proxy didn't respond within 15s. Check /tmp/claude-max-proxy.log"
  fi

  LLM_PROVIDER="openai"
fi

# ── 8. Environment file ───────────────────────────────────────────
LORE_ENV="$HOME/.lore/env"
mkdir -p "$HOME/.lore"

cat > "$LORE_ENV" <<ENVEOF
# Lore server configuration
# Generated by install-mac.sh on $(date -Iseconds)
LORE_STORE=postgres
LORE_PG_URL=postgresql://localhost/lore
LORE_API_KEY=${LORE_API_KEY}
LORE_PORT=${LORE_PORT}

# Enrichment pipeline
LORE_ENRICHMENT_ENABLED=true
LORE_CLASSIFY=true
LORE_FACT_EXTRACTION=true
LORE_KNOWLEDGE_GRAPH=true
ENVEOF

if [ -n "$LLM_PROVIDER" ]; then
  cat >> "$LORE_ENV" <<LLMEOF

# LLM provider
LORE_LLM_PROVIDER=${LLM_PROVIDER}
LORE_ENRICHMENT_MODEL=${ENRICHMENT_MODEL}
LLMEOF

  if [ "$USE_MAX_PROXY" = true ]; then
    echo "OPENAI_API_BASE=http://localhost:${MAX_PROXY_PORT}/v1" >> "$LORE_ENV"
  elif [ "$LLM_PROVIDER" = "anthropic" ] && [ -n "$LLM_KEY" ]; then
    echo "ANTHROPIC_API_KEY=${LLM_KEY}" >> "$LORE_ENV"
  elif [ "$LLM_PROVIDER" = "openai" ] && [ -n "$LLM_KEY" ]; then
    echo "OPENAI_API_KEY=${LLM_KEY}" >> "$LORE_ENV"
  fi
fi

echo "✅ Config saved to $LORE_ENV"

# ── 9. LaunchAgent for Lore server ────────────────────────────────
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/com.lore.server.plist"
mkdir -p "$PLIST_DIR"

# Build ProgramArguments XML fragment (plain string, no bash arrays in heredocs)
if [ -n "$LORE_BIN" ]; then
  PROG_ARGS="        <string>${LORE_BIN}</string>
        <string>serve</string>
        <string>--port</string>
        <string>${LORE_PORT}</string>"
else
  PROG_ARGS="        <string>${PY_BIN}</string>
        <string>-m</string>
        <string>lore</string>
        <string>serve</string>
        <string>--port</string>
        <string>${LORE_PORT}</string>"
fi

# Build EnvironmentVariables XML fragment from env file
ENV_DICT=""
while IFS='=' read -r key val; do
  # Skip empty lines and comments
  [[ -z "$key" || "$key" == \#* ]] && continue
  ENV_DICT="${ENV_DICT}        <key>${key}</key>
        <string>${val}</string>
"
done < "$LORE_ENV"

cat > "$PLIST_FILE" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.lore.server</string>
    <key>ProgramArguments</key>
    <array>
${PROG_ARGS}
    </array>
    <key>EnvironmentVariables</key>
    <dict>
${ENV_DICT}    </dict>
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
PLISTEOF

echo "  LaunchAgent written to $PLIST_FILE"

# Load (or reload) the service
launchctl unload "$PLIST_FILE" 2>&1 || true
launchctl load "$PLIST_FILE"
echo "✅ Lore server LaunchAgent loaded"

# ── 10. Wait for health ───────────────────────────────────────────
echo -n "⏳ Waiting for server health..."
SERVER_READY=false
for i in $(seq 1 30); do
  if curl -sf "http://localhost:$LORE_PORT/health" > /dev/null 2>&1; then
    SERVER_READY=true
    break
  fi
  sleep 1
  echo -n "."
done
echo ""

if [ "$SERVER_READY" = true ]; then
  echo "✅ Server healthy at http://localhost:$LORE_PORT"
else
  echo "❌ Server didn't start after 30s"
  echo ""
  echo "  Log output (last 30 lines):"
  tail -30 /tmp/lore-server.log 2>&1 || echo "  (no log file found)"
  echo ""
  echo "  Plist contents:"
  cat "$PLIST_FILE"
  echo ""
  echo "  Try starting manually:"
  echo "    source ~/.lore/env && $LORE_CMD serve --port $LORE_PORT"
  exit 1
fi

# ── 11. Agent hooks ───────────────────────────────────────────────
echo ""
echo "📎 Setting up agent hooks..."

HOOK_ARGS="--server-url http://localhost:$LORE_PORT --api-key $LORE_API_KEY"

echo "  Installing Claude Code hook..."
if $LORE_CMD setup claude-code $HOOK_ARGS 2>&1; then
  echo "  ✅ Claude Code hook installed"
else
  echo "  ❌ Claude Code setup failed (see error above)"
fi

echo "  Installing Codex CLI hook..."
if $LORE_CMD setup codex $HOOK_ARGS 2>&1; then
  echo "  ✅ Codex CLI hook installed"
else
  echo "  ❌ Codex setup failed (see error above)"
fi

echo "  Installing Cursor hook..."
if $LORE_CMD setup cursor $HOOK_ARGS 2>&1; then
  echo "  ✅ Cursor hook installed"
else
  echo "  ❌ Cursor setup failed (see error above)"
fi

# ── 12. Verify ────────────────────────────────────────────────────
echo ""
if [ -f "$HOME/.claude/settings.json" ]; then
  if $PY_BIN -c "
import json, sys
s = json.load(open('$HOME/.claude/settings.json'))
hooks = s.get('hooks', {})
if 'UserPromptSubmit' in hooks:
    print('  ✅ Verified: Claude Code UserPromptSubmit hook registered')
else:
    print('  ⚠️  settings.json exists but UserPromptSubmit hook not found')
    sys.exit(1)
" 2>&1; then
    true
  else
    echo "  ⚠️  Hook verification failed — you may need to run setup manually"
  fi
else
  echo "  ⚠️  ~/.claude/settings.json not found — Claude Code may not be installed yet"
fi

# ── Done ──────────────────────────────────────────────────────────
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
if [ "$USE_MAX_PROXY" = true ]; then
  echo "  Max Proxy: http://localhost:$MAX_PROXY_PORT"
  echo "  Proxy Log: /tmp/claude-max-proxy.log"
  echo ""
elif [ -z "$LLM_PROVIDER" ]; then
  echo "⚠️  No LLM provider configured. Enrichment will skip LLM features."
  echo "   Re-run with --max-proxy (free with Max subscription) or add to ~/.lore/env:"
  echo "     LORE_LLM_PROVIDER=anthropic"
  echo "     ANTHROPIC_API_KEY=sk-ant-..."
  echo "   Then reload: launchctl unload ~/Library/LaunchAgents/com.lore.server.plist"
  echo "                launchctl load ~/Library/LaunchAgents/com.lore.server.plist"
  echo ""
fi
echo "Done!"
