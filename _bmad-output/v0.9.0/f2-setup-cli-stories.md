# F2: Setup CLI — User Stories

## Story 1: `lore setup claude-code`
**As a** Claude Code user
**I want** to run `lore setup claude-code` to install auto-retrieval hooks
**So that** Lore memories are automatically injected into my conversations

### Acceptance Criteria
- [ ] Creates hook script at `~/.claude/hooks/lore-retrieve.sh`
- [ ] Updates `~/.claude/settings.json` to register the UserPromptSubmit hook
- [ ] Hook script calls `lore recall` or the retrieve API
- [ ] Supports `--server-url` flag for custom Lore server
- [ ] Idempotent — running twice doesn't duplicate entries

## Story 2: `lore setup openclaw`
**As an** OpenClaw user
**I want** to run `lore setup openclaw` to install auto-retrieval hooks
**So that** Lore memories flow into my OpenClaw agent context

### Acceptance Criteria
- [ ] Creates hook script in the workspace hooks directory
- [ ] Hook triggers on `message:preprocessed` event
- [ ] Supports `--server-url` flag

## Story 3: `lore setup --status`
**As a** user
**I want** to check which runtimes have Lore hooks installed
**So that** I can verify my setup

### Acceptance Criteria
- [ ] Shows installed status for each supported runtime
- [ ] Shows hook file paths and whether they exist

## Story 4: `lore setup --remove <runtime>`
**As a** user
**I want** to uninstall Lore hooks for a specific runtime
**So that** I can cleanly remove the integration

### Acceptance Criteria
- [ ] Removes hook file
- [ ] Removes hook entry from settings.json (claude-code)
- [ ] Confirms removal to user
