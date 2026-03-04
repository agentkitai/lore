# Product Brief — Open Brain: Universal AI Memory Layer

**Author:** Mary (Business Analyst, BMAD v6.0.4) | **Date:** 2026-03-03
**Status:** Draft — Pivot brief (Lore → Open Brain)
**Predecessor:** [Lore Product Brief](../../product-brief.md) (2026-02-12)

---

## 1. Product Name & Positioning

### Name: **Open Brain**

| Candidate | Rationale | Verdict |
|-----------|-----------|---------|
| **Open Brain** | Evocative ("brain for your AI"), open-source signal, memorable, `.ai` domain feasible | ✅ **Recommended** |
| **Engram** | Neuroscience metaphor, technical crowd loves it | Runner-up — too niche for mainstream devs |
| **Recall** | Simple verb, developer-friendly | Trademark minefield, too generic |
| **Lore** | Current name, established on PyPI/npm | Tied to "lessons learned" — too narrow for the pivot |
| **MCP Memory** | Descriptive, SEO-friendly for the MCP wave | Too literal, no brand equity potential |

**Why "Open Brain" wins:**
- **"Open"** signals self-hostable, open-source, transparent — the values that matter to the target audience
- **"Brain"** is immediately understood: this is where your AI stores and retrieves knowledge
- **Pairs naturally with MCP:** "Give your AI a brain" is a one-sentence pitch
- **Differentiation:** Every competitor uses clinical/technical names (Mem0, Zep, Qdrant). "Open Brain" is approachable and evocative
- **Domain availability:** openbrain.dev, openbrain.ai worth investigating

**Positioning statement:**
> Open Brain is the open-source memory layer for AI. Any AI that speaks MCP can remember, recall, and forget — backed by your own Postgres. Your data, your infrastructure, your brain.

### How this differs from Lore's positioning
Lore was "cross-agent lesson sharing with redaction." Open Brain is "universal memory for any AI." The shift is from a **narrow SDK for agent teams** to a **fundamental infrastructure utility** — like Redis for caching, but for AI memory.

---

## 2. Vision & Mission

**Vision:** Every AI — from a $20/month ChatGPT subscription to a fleet of enterprise agents — deserves persistent memory. Open Brain makes that a one-command setup.

**Mission:** Provide the simplest, cheapest, most portable memory layer for AI systems, starting with MCP as the universal interface.

### Why now?
1. **MCP is hitting critical mass.** Anthropic's Model Context Protocol is becoming the USB-C of AI tool integration. Claude, Cursor, Windsurf, and dozens of others support it. But MCP is a *protocol*, not a *product*. Someone needs to build the products on top.
2. **The Nate B Jones gap.** His video (90K+ views) taught people the *architecture* for AI memory with Postgres + MCP. Thousands of developers now know what to build but have no turnkey product. Open Brain IS that product.
3. **Self-hosted is having a moment.** Post-AI-hype, developers are skeptical of cloud vendor lock-in. "Run it yourself for $0.10/month" is a compelling pitch when competitors charge $49-199/month.

---

## 3. Target Users & Personas

### Primary: **The MCP Power User** (TAM: ~50,000-100,000 today, growing fast)

- Uses Claude Desktop, Cursor, Windsurf, or Copilot daily
- Has seen the Nate B Jones video or similar content
- Wants persistent memory across AI conversations
- Technical enough to run `docker compose up` but doesn't want to build from scratch
- **Pain:** "I explained my project architecture to Claude yesterday. Today it forgot everything."
- **Willingness to pay:** Low for software ($0-10/mo), but will self-host eagerly if the setup is <10 minutes

**This is the launch persona.** They're actively looking for this product RIGHT NOW.

### Secondary: **The AI Agent Builder** (TAM: ~10,000-30,000)

- Building agents with LangChain, CrewAI, AutoGen, or custom code
- Needs persistent memory across agent runs
- Currently hacking together vector DB + custom schemas
- **Pain:** "Every agent run starts from zero. I need state that persists."
- **Willingness to pay:** $20-50/mo for a managed solution

### Tertiary: **The AI-Native Startup** (TAM: ~2,000-5,000)

- Building products where AI memory is a core feature
- Needs multi-tenant memory, API access, scale
- **Pain:** "We need to give each user's AI a persistent brain, and we don't want to build that infrastructure."
- **Willingness to pay:** $100-500/mo

### Who this is NOT for
- **Enterprise knowledge management buyers** — they want Confluence with AI, not a developer tool
- **RAG-only teams** — they want document retrieval, not memory/state management
- **Non-technical users** — Open Brain requires Docker or cloud setup. This is a developer product.
- **People who want a UI** — V1 is headless. MCP tools are the interface. A dashboard can come later.

---

## 4. Competitive Landscape

### Direct competitors

| Product | What | Funding | Users | How Open Brain differs |
|---------|------|---------|-------|----------------------|
| **Mem0** | User/conversation memory | $7M+ seed | ~10K devs | User-centric ("John likes dark mode"). Not general-purpose memory. Python SDK-first, not MCP-native. Cloud-dependent. |
| **Zep** | Conversation memory server | ~$3M | ~5K devs | Tied to conversation/chat use case. Not flexible schema. Heavy runtime. |
| **LangMem** | LangChain long-term memory | LangChain internal | LangChain users only | Platform-locked. Only works within LangChain ecosystem. |

### Indirect competitors

| Product | Threat level | Notes |
|---------|-------------|-------|
| **Pinecone/Weaviate/Qdrant** | LOW | Raw infrastructure. You still need schema, MCP tools, embedding pipeline. Like comparing MySQL to WordPress. |
| **Claude Projects/Custom GPTs** | MEDIUM | Platform-native memory (attached files, conversation history). Good enough for casual users. But not portable, not programmable, not cross-platform. |
| **DIY Postgres + pgvector** | HIGH | This is actually the biggest "competitor" — developers who follow the Nate B Jones tutorial and build their own. Open Brain's answer: "We already built it. `docker compose up` and you're done." |

### Honest competitive assessment

**The uncomfortable truth:** No one has product-market fit in "universal AI memory" yet. Mem0 is closest but pivoting toward enterprise/conversation memory. The market is nascent and fragmented.

**The opportunity:** Being first to own "MCP-native memory" as a category is genuinely valuable. MCP adoption is accelerating, and the gap between "I know I need AI memory" and "I have AI memory working" is large. Open Brain can be the default answer.

**The risk:** If Anthropic, OpenAI, or another platform builds native persistent memory, the market for third-party memory layers shrinks dramatically. This is a real threat with a ~12-18 month window.

---

## 5. Unique Differentiators

### 1. MCP-Native from Day One
Not a REST API with an MCP wrapper bolted on — the MCP server IS the primary interface. `remember`, `recall`, `forget`, `list` — four tools, dead simple. Any MCP-compatible AI (Claude, Cursor, Copilot, custom agents) gets memory instantly.

**Why this matters:** MCP is becoming the standard. Being MCP-first means Open Brain works everywhere MCP works, today and tomorrow, without adapters or SDKs.

### 2. Self-Hosted, Dirt Cheap
Postgres + pgvector on a $5/mo VPS. Total cost: ~$0.10-0.30/month for most users. Compare to Mem0 Pro at $99/mo or Zep Cloud at custom pricing. The cost argument is devastating.

**Why this matters:** Developers are allergic to recurring SaaS costs for infrastructure. "Own your data, pay pennies" is a message that resonates deeply in the current climate.

### 3. Schema Flexibility (Post-Pivot)
Not locked to "problem/resolution" (Lore's limitation). Open Brain stores any content type: notes, code snippets, conversation summaries, decisions, preferences, facts. Typed but flexible: `content` + `type` + `metadata`.

**Why this matters:** Memory isn't just "lessons learned." It's everything an AI needs to remember. A flexible schema means Open Brain serves every memory use case, not just one.

### 4. Platform-Agnostic
Not tied to LangChain, not tied to OpenClaw, not tied to any framework or platform. Works with anything that supports MCP or can make HTTP calls.

**Why this matters:** Developers are tired of ecosystem lock-in. An independent memory layer that works with everything is more valuable than one locked to a platform.

### 5. Existing Codebase (Head Start)
Lore already has: Postgres + pgvector storage, MCP server, Docker deployment, CDK IaC, semantic search, Python + TypeScript SDKs. The pivot is a rebrand + schema generalization, not a ground-up rewrite.

**Why this matters:** Time-to-market. While competitors are building, Open Brain ships.

---

## 6. Business Model & Pricing

### Model: Open-Core + Optional Cloud

**Open Source (MIT, free forever):**
- Full MCP server (remember, recall, forget, list, stats)
- Postgres + pgvector storage
- Docker Compose one-command deployment
- Semantic search with auto-embedding
- Webhook ingestion endpoint
- Python + TypeScript SDKs
- CDK/IaC templates

**This is the entire product.** The open-source version is not crippled. This is critical for adoption.

**Cloud Hosted (paid, future — NOT in V1):**

| Tier | Price | What you get |
|------|-------|-------------|
| **Free** | $0 | 1,000 memories, 1 project, community support |
| **Pro** | $9/mo | 100K memories, unlimited projects, priority support |
| **Team** | $29/mo | Multi-user, shared workspaces, SSO, audit log |
| **Enterprise** | Custom | On-prem support, SLA, dedicated instance |

### Why these prices are lower than the original Lore brief

The original brief priced Team at $49/mo and Pro at $199/mo. That was for a different product (cross-agent lesson sharing for teams). Open Brain's primary audience is individual developers and small teams who can (and will) self-host. Pricing must be **lower than the effort of self-hosting** for the lazy, not higher than what they'd pay for a VPS.

At $9/mo, the pitch is: "You could self-host for $5/mo and spend an hour setting it up. Or pay us $9/mo and be done in 30 seconds." That's a real value proposition.

### Revenue reality check

**Be honest:** Cloud revenue will be small for the first 6-12 months. The real value of Open Brain is:
- **Distribution/mindshare** for Amit's broader projects
- **GitHub stars/community** that creates consulting/sponsorship opportunities
- **The option value** of having the default MCP memory product if the market explodes

Target: 500 self-host users, 100 cloud users, $2K MRR by month 6. That's realistic for a solo dev. Don't plan for $50K MRR — that requires a team, marketing budget, and enterprise sales.

### Monetization alternatives to consider
- **GitHub Sponsors** for the OSS project
- **Consulting/integration services** for teams that need custom setup
- **Premium adapters** (Slack, Telegram, Notion capture) as paid add-ons
- **Hosted embedding service** (for users who don't want to run their own embedding model)

---

## 7. Go-to-Market Strategy

### Phase 0: Pre-Launch Preparation (Week 1-2)

- [ ] Generalize schema: `content` + `type` + `metadata` (migration from Lore's problem/resolution)
- [ ] Rename MCP tools: `remember`, `recall`, `forget`, `list`, `stats`
- [ ] Webhook ingestion endpoint (POST with auto-embedding)
- [ ] New README with 3-line quickstart
- [ ] Rebrand repo: `openbrain` (or new repo with Lore redirect)
- [ ] Docker image on GHCR/Docker Hub

### Phase 1: Launch — "Give Your AI a Brain" (Week 3-4)

**Primary channel: Nate B Jones community + MCP ecosystem**
- Blog post / tutorial: "I built the product from Nate B Jones' AI memory video"
- Post to: Hacker News, Reddit (r/LocalLLaMA, r/ChatGPT, r/MachineLearning), AI Twitter/X
- Claude Desktop MCP config in README (copy-paste-and-go)
- YouTube video: "5-minute setup: persistent memory for Claude/Cursor"

**Target:** 200 GitHub stars, 50 self-host installs, 5 blog mentions

### Phase 2: Ecosystem Integration (Month 2-3)

- Cursor / Windsurf setup guides
- LangChain / CrewAI integration examples
- CLI capture tool (`openbrain remember "deployment uses port 8080"`)
- SSE transport for MCP (in addition to stdio)
- Community Discord

**Target:** 1,000 GitHub stars, 200 active installs, first cloud beta users

### Phase 3: Cloud + Adapters (Month 4-6)

- Launch hosted cloud (free tier)
- Slack / Telegram / webhook adapters for automatic memory capture
- Dashboard UI for browsing/managing memories
- Publish on MCP server directories (Smithery, etc.)

**Target:** 2,500 GitHub stars, 500 active installs, 100 cloud users, $2K MRR

### Phase 4: Scale & Differentiate (Month 7-12)

- Multi-user shared brains (team memory)
- Memory graph (relationships between memories)
- Memory agents (auto-summarize, auto-prune, detect contradictions)
- Explore partnerships with AI tool companies

### Channel strategy
- **Primary:** Developer content (blog, X/Twitter, HN, Reddit, YouTube)
- **Secondary:** MCP ecosystem (Smithery, Claude Desktop community, Cursor forums)
- **Tertiary:** AI dev communities (Discord servers, Slack groups)
- **NOT:** Enterprise sales, conferences, paid ads. Too early and too expensive for a solo dev.

---

## 8. Risks & Honest Assessment

### Critical Risks

| Risk | Severity | Probability | Mitigation |
|------|----------|------------|------------|
| **Platform-native memory kills the category** — Anthropic/OpenAI build persistent memory into their products | CRITICAL | MEDIUM (12-18mo) | Move fast. Establish Open Brain as the cross-platform, portable option. Platform memory won't be portable — that's the moat. |
| **Solo dev can't sustain OSS + cloud + community** | HIGH | HIGH | Keep scope ruthlessly minimal. V1 is MCP server + Docker + README. No dashboard, no cloud, no adapters. Add only what users scream for. |
| **"Just follow the tutorial" objection** | HIGH | HIGH | The tutorial shows architecture. Open Brain IS the product. Emphasize: migrations, upgrades, security, schema design — all the things a tutorial skips. |
| **MCP adoption stalls** | MEDIUM | LOW | MCP has Anthropic's backing and broad adoption. Low risk, but hedge with REST API. |
| **Name conflict / trademark** | MEDIUM | MEDIUM | Check "Open Brain" trademark before committing. Have "Engram" as backup. |

### Market timing assessment

**Score: 7/10 — Good timing, act fast.**

The previous brief scored 6/10 for Lore's "cross-agent lesson sharing" positioning. The pivot to "universal AI memory" improves timing because:
1. MCP adoption is accelerating (Cursor, Windsurf, Claude Desktop all support it)
2. The Nate B Jones video created demand with no product to satisfy it
3. Self-hosted + open-source is the trend (Supabase, Appwrite, etc.)
4. The "AI memory" search volume is growing rapidly

**But the window is narrow.** If Amit doesn't ship in 2-4 weeks, someone else will. The Nate B Jones video is a starting gun.

### Honest founder assessment

**Strengths:**
- Deep MCP expertise (already runs MCP servers in production)
- 70%+ of the codebase already exists (Lore)
- History of shipping (AgentLens, AgentGate, FormBridge, Lore, OpenClaw)
- Low burn rate (working from home, no team costs)

**Weaknesses:**
- Solo developer — no one to handle community/support while coding
- Previous launch (Lore) got 2 GitHub stars — distribution is the hard part
- No marketing budget
- No design/UI skills (headless-first is a feature AND a limitation)

**The distribution problem is the real challenge.** The product can be great, but if no one finds it, it doesn't matter. The Nate B Jones angle is the best distribution hack available — ride that wave hard.

---

## 9. Technical Foundation

### What exists today (from Lore codebase)

| Component | Status | Effort to adapt |
|-----------|--------|----------------|
| Postgres + pgvector storage | ✅ Working | Low — add flexible schema migration |
| MCP server (stdio) | ✅ Working | Medium — rename tools, generalize schema |
| Docker Compose deployment | ✅ Working | Low — rebrand, update defaults |
| CDK IaC stack | ✅ Working | Low — rename resources |
| Semantic search with embeddings | ✅ Working | None — works as-is |
| Python SDK | ✅ Published (PyPI) | Medium — new package name, generalized API |
| TypeScript SDK | ✅ Published (npm) | Medium — new package name, generalized API |
| Auto-embedding pipeline | ✅ Working | None — works as-is |
| API server (FastAPI) | ✅ Working | Low — add webhook endpoint |
| Multi-org / API keys | ✅ Working | None — works as-is |

### What needs building

| Component | Priority | Effort | Notes |
|-----------|----------|--------|-------|
| Schema migration (problem/resolution → content/type/metadata) | P0 | 1-2 days | Database migration + SDK changes |
| MCP tool rename (save_lesson → remember, etc.) | P0 | 1 day | Straightforward rename |
| Webhook ingestion endpoint | P1 | 1-2 days | POST body → embed → store |
| SSE transport for MCP | P1 | 2-3 days | Required for remote/cloud MCP connections |
| CLI tool (`openbrain remember "..."`) | P1 | 1 day | Thin wrapper around MCP/API |
| New README + docs | P0 | 1-2 days | Critical for launch |
| New Docker image + registry | P0 | 0.5 days | GHCR publish |
| Slack adapter | P2 | 2-3 days | Capture messages as memories |
| Telegram adapter | P2 | 2-3 days | Capture messages as memories |
| Dashboard UI | P3 | 1-2 weeks | Browse/search/manage memories |
| Cloud hosting infrastructure | P3 | 1-2 weeks | Multi-tenant, billing, auth |

### Estimated time to MVP launch: 1-2 weeks

That's the P0 items: schema migration, tool rename, webhook endpoint, README, Docker image. Everything else is Phase 2+.

### Architecture (target state)

```
┌─────────────────────────────────────────────────────┐
│                   AI Clients                         │
│  Claude Desktop │ Cursor │ Copilot │ Custom Agents   │
└────────┬──────────┬──────────┬──────────┬───────────┘
         │          │          │          │
         │    MCP Protocol (stdio/SSE)    │
         │          │          │          │
┌────────▼──────────▼──────────▼──────────▼───────────┐
│              Open Brain MCP Server                    │
│                                                       │
│  Tools: remember │ recall │ forget │ list │ stats     │
│                                                       │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │  Embedding   │  │   Webhook    │  │    CLI     │  │
│  │  Pipeline    │  │   Ingestion  │  │   Client   │  │
│  └──────┬──────┘  └──────┬───────┘  └─────┬──────┘  │
│         │                │                 │          │
│  ┌──────▼────────────────▼─────────────────▼───────┐ │
│  │           Storage Layer (Postgres + pgvector)    │ │
│  │                                                   │ │
│  │  memories: id, content, type, source, metadata,   │ │
│  │           embedding, created_at, expires_at       │ │
│  └───────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────┘
```

### New schema (proposed)

```sql
CREATE TABLE memories (
    id          TEXT PRIMARY KEY,          -- ULID
    content     TEXT NOT NULL,             -- The actual memory content
    type        TEXT DEFAULT 'note',       -- note, lesson, snippet, fact, conversation, decision
    source      TEXT,                      -- Where this came from (agent name, tool, webhook)
    project     TEXT,                      -- Namespace scoping
    tags        JSONB DEFAULT '[]',        -- Filterable tags
    metadata    JSONB DEFAULT '{}',        -- Flexible key-value pairs
    embedding   vector(384),              -- Semantic search vector
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now(),
    expires_at  TIMESTAMPTZ,              -- Optional TTL
    org_id      TEXT REFERENCES orgs(id)   -- Multi-tenant
);
```

---

## 10. Success Metrics

### Launch (Month 1)
| Metric | Target | Why it matters |
|--------|--------|---------------|
| GitHub stars | 200+ | Social proof, discoverability |
| Docker pulls | 100+ | Actual usage signal |
| MCP config shares | 50+ | People adding to Claude Desktop / Cursor |
| Hacker News front page | Yes | Single biggest distribution event for dev tools |

### Traction (Month 3)
| Metric | Target | Why it matters |
|--------|--------|---------------|
| GitHub stars | 1,000+ | Threshold for "real project" perception |
| Weekly active installs | 200+ | Sustained usage, not just star-and-forget |
| Community Discord members | 100+ | Engagement and feedback loop |
| Blog/video mentions by others | 10+ | Organic distribution |
| Contributors | 5+ | Community investment in the project |

### Product-Market Fit (Month 6)
| Metric | Target | Why it matters |
|--------|--------|---------------|
| GitHub stars | 2,500+ | Category-defining level |
| Weekly active installs | 500+ | Real adoption |
| Cloud users (if launched) | 100+ | Revenue viability signal |
| MRR (if cloud launched) | $2,000+ | Business viability |
| Retention (30-day) | 40%+ | People who try it, keep using it |
| NPS from active users | 40+ | Product satisfaction |

### North Star Metric
**Weekly active memories created across all installs.**

This measures both adoption (number of installs) and engagement (people actually using it, not just installing). If this number grows week-over-week, everything else follows.

### Anti-metrics (signals something is wrong)
- Stars grow but Docker pulls don't → people like the idea but don't use it
- Installs grow but memories/week doesn't → setup works but product isn't useful
- Cloud signups but no usage → pricing/value mismatch

---

## Appendix A: Decisions & Rationale

### Decision 1: Open-source everything (not open-core with crippled free tier)
**Rationale:** The target audience (MCP power users, developers) will self-host anyway. Crippling the free version just drives them to build their own or fork. Make the OSS version great, charge for convenience (hosting) and extras (adapters, dashboard).

### Decision 2: MCP-first, REST-second
**Rationale:** MCP is the distribution channel. Every MCP-compatible AI becomes a distribution point. REST API exists for webhooks and programmatic access, but MCP is the headline feature.

### Decision 3: Don't launch cloud in V1
**Rationale:** Cloud requires billing, auth, multi-tenant ops, and support — all of which distract a solo dev from making the core product great. Ship OSS, prove value, then launch cloud when demand justifies it.

### Decision 4: Price low, aim for volume
**Rationale:** Mem0 prices at $99/mo for Pro. They have funding and a team. Amit doesn't. Compete on price and simplicity, not features. $9/mo Pro tier undercuts everyone and is enough for profitability at scale.

### Decision 5: Ride the Nate B Jones wave
**Rationale:** This is the single best distribution opportunity available. The video created demand. Open Brain is the supply. The blog post title writes itself: "I built the product from Nate B Jones' AI memory video." Ship fast, capture the moment.

---

## Appendix B: Open Questions

1. **Trademark check:** Is "Open Brain" available? Need to check USPTO, domain availability (openbrain.dev, openbrain.ai), npm/PyPI package names.
2. **Embedding model:** Lore uses MiniLM-L6 (384 dimensions). Should Open Brain default to this, or use a larger model (e.g., text-embedding-3-small from OpenAI)? Trade-off: local vs API dependency.
3. **Migration path from Lore:** Should existing Lore users get an automatic migration? Or is the user base small enough (2 stars) to not worry about it?
4. **Redaction:** Lore's redaction pipeline is a differentiator. Should Open Brain keep it as a core feature, make it optional, or remove it? Recommendation: keep as opt-in (not default — general memory has different privacy needs than operational lessons).
5. **Mono-repo vs new repo:** Pivot in-place (rename `amitpaz1/lore` → `amitpaz1/openbrain`) or start fresh? Fresh repo loses Git history but starts with clean stars/issues.

---

*This brief replaces the original Lore product brief for the purposes of the Open Brain pivot. The Lore brief remains valid as a historical reference for the original product direction.*
