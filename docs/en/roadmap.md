# PyClaw Development Roadmap

> Last updated: 2026-05-01
> Source of truth: `openspec/changes/pyclaw-architecture/tasks.md`

## Current Status

| Metric | Value |
|--------|-------|
| Total Roadmap Tasks | 90 |
| Completed | 69 (77%) |
| Remaining | 21 (23%) |
| Test Coverage | 599 passed, 19 skipped |
| Delivered Changes | 10 (all complete) |

## Module Completion

```
§1  Project Scaffolding       ██████████████████████████████ 6/6   ✅
§2  Storage Layer             ████████████████████████░░░░░░ 4/5   🟡
§3  Session Store             ████████████████████████░░░░░░ 6/7   🟡
§4  Agent Core                ██████████████████████████████ 10/10 ✅
§5  Skill Hub                 ██████████████████████████████ 9/9   ✅
§6  Channel System            ██████████████████████████████ 4/4   ✅
§7  Feishu Channel            ██████████████████████████████ 10/10 ✅
§8  Web Channel               ██████████████████████████████ 11/11 ✅
§9  Memory Store              ███░░░░░░░░░░░░░░░░░░░░░░░░░░░ 1/9   🔲
§10 Dreaming Engine           ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ 0/6   🔲
§11 App Orchestration         ██████████████████████████░░░░ 5/7   🟡
§12 Documentation             ████████████████████░░░░░░░░░░ 4/6   🟡
```

## Phased Plan

### Phase 1: Demonstrable ✅ COMPLETE

**Goal**: Anyone can experience PyClaw through a browser — no Feishu account required.

| Task | Description | Effort |
|------|-------------|--------|
| §8.1 | HTTP API routes: POST /api/chat/send, GET /api/sessions | 1 day |
| §8.2 | WebSocket endpoint: streaming TextChunk as WS frames | 1 day |
| §8.3 | Bearer token authentication middleware | 2 hrs |
| §8.4 | Wire web routes into app.py | 2 hrs |
| §8.5 | Tests for HTTP + WebSocket flow | half day |
| §11.3 | Graceful shutdown handler (SIGTERM drain) | 2 hrs |
| §11.6 | Dockerfile for production deployment | 2 hrs |
| §11.7 | E2E: HTTP → agent → reply integration test | half day |

**Deliverables**:
- `http://localhost:8000` accessible chat API backend
- `docker compose up` one-command startup (with Redis)
- WebSocket streaming responses
- Bearer token auth

**Unlocks**:
- Public demo deployment
- WeChat article publication with live demo link
- GitHub README can point to a running instance

---

### Phase 2: Memory (2 weeks)

**Goal**: Agent remembers users across sessions — transforms from "chat tool" to "personal assistant".

| Task | Description | Effort |
|------|-------------|--------|
| §9.1 | Memory entry model + MemoryStore Protocol | half day |
| §9.2 | SQLite + sqlite-vec implementation | 1 day |
| §9.3 | Text chunking (400 tokens, 80 overlap) | half day |
| §9.4 | Embedding generation via litellm | 1 day |
| §9.5 | Hybrid search (vector + BM25 text) | 1 day |
| §9.7 | Tests for store, chunking, search ranking | 1 day |
| §9.9 | Mem0ContextEngine: retrieval → prompt, ingest() | 2 days |
| §9.6 | PostgreSQL + pgvector (production) | 1 day (optional) |

**Deliverables**:
- Cross-session memory ("My name is Alice" → remembered next session)
- Memory retrieval injected into system prompt (RAG pattern)
- `memory_backend: sqlite / postgres` config switch
- Development mode: zero external dependencies (sqlite-vec)

**Key decisions**:
- Embedding model: litellm unified interface (same provider as LLM)
- Dev: sqlite-vec (zero deps), Prod: pgvector (scalable)
- Chunking: 400 tokens aligns with typical context window budgets

---

### Phase 3: Self-Organization (2 weeks)

**Goal**: Agent autonomously organizes memories during idle time — distills knowledge from conversations.

| Task | Description | Effort |
|------|-------------|--------|
| §10.1 | APScheduler + Redis job store | 1 day |
| §10.2 | Leader election (multi-instance: only one dreams) | half day |
| §10.3 | Light dreaming: deduplication + candidate staging | 1 day |
| §10.4 | Deep dreaming: LLM-powered promotion to long-term | 2 days |
| §10.5 | REM dreaming: cross-memory pattern discovery | 2 days |
| §10.6 | Tests for scheduler, leader, phases | 1 day |

**Deliverables**:
- Background memory consolidation pipeline
- Short-term conversations → long-term knowledge (automatic)
- Cross-session pattern discovery ("user always asks about X on Mondays")
- Leader election ensures only one instance dreams (multi-instance safe)

---

### Phase 4: Enterprise-Ready (1 week)

**Goal**: Horizontal scaling for production multi-instance deployments.

| Task | Description | Effort |
|------|-------------|--------|
| §11.2 | Worker identity + health registry | 1 day |
| §11.5 | Session affinity gateway (Redis registry + PubSub) | 2 days |
| §12.5 ✅ | Configuration reference documentation ([configuration.md](./configuration.md)) | half day |
| §12.6 ✅ | Deployment guide ([deployment.md](./deployment.md) + [deploy/docker-compose.multi.yml](../../deploy/docker-compose.multi.yml)) | half day |
| §2.4 | File-based lock fallback (dev convenience) | half day |
| §3.3 | File session store (zero-dependency mode) | 1 day |

**Deliverables**:
- Multi-instance deployment with session affinity
- Worker health monitoring
- Complete documentation for ops teams
- Zero-dependency development mode (no Redis required)

---

## Architecture Overview (Post-Phase 4)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Compute Layer (Stateless Workers × N)                                   │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  Agent Runner │ Context Engine │ Tool Registry │ Skill Discovery    │ │
│  │  (LLM loop)  │ (RAG+compact)  │ (bash,rw,ed) │ (5-layer scan)    │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  Channels: Feishu WS │ Web HTTP+WS │ (future: WeChat, DingTalk)   │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  Dreaming Engine (leader-elected, scheduled)                       │ │
│  │  Light → Deep → REM memory consolidation                           │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Storage Layer (Shared State)                                            │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐  │
│  │  Redis   │  │ WorkspaceStore│  │ Memory Store  │  │ Skill Hub    │  │
│  │ Sessions │  │ (File/Redis) │  │ (sqlite-vec/  │  │ (~/.openclaw │  │
│  │ Locks    │  │ AGENTS.md    │  │  pgvector)    │  │  /skills/)   │  │
│  │ Affinity │  │ Bootstrap    │  │ Embeddings    │  │ ClawHub API  │  │
│  └──────────┘  └──────────────┘  └───────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## Decisions Log

### Deferred (intentionally out of scope for now)

| Item | Reason | Revisit When |
|------|--------|--------------|
| Session Affinity Gateway (35 tasks) | Single instance + Redis sufficient; zero ROI at current scale | Actual multi-instance deployment needed |
| File session store (§3.3) | InMemory (dev) + Redis (prod) covers all current needs | Users request zero-dependency mode |
| File lock fallback (§2.4) | Redis lock works everywhere; file lock only for edge dev cases | Phase 4 |
| `requires.config` eligibility | PyClaw has no per-skill config schema yet | Per-skill settings designed |
| Skill `skillKey` override | Low impact, no real-world skills depend solely on it | Full OpenClaw parity needed |
| Nested skill root heuristic | Edge case in discovery (dir/skills/*/SKILL.md) | Reported as issue |

### Key Technical Decisions (this session)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Skill discovery timing | Per-request in runner (not factory) | workspace_path only known per-request |
| Prompt rendering | `PromptInputs.skills_prompt: str` (prompt.py owns) | Budget/compact logic can't fit SkillSummary interface |
| Eligibility order | OS → always → bins → anyBins → env | Matches OpenClaw exactly; OS is absolute |
| ClawHub field mapping | displayName/summary/latestVersion.version | Adapted to actual API response (not documented API) |
| CLI framework | argparse (stdlib) | No new dependency for 4 commands |

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| ClawHub API schema changes | Skill install/search breaks | Unit tests with mocked responses; field mapping isolated in one function |
| sqlite-vec Python 3.12 compat | Memory store won't start | Fallback: hnswlib or numpy cosine similarity |
| Embedding API costs during dev | Budget overrun | litellm + mock embedding for tests; real embedding only in E2E |
| Single-instance Redis SPOF | All sessions lost if Redis dies | Redis persistence (RDB/AOF); file session fallback in Phase 4 |
| Web Channel security | Unauthorized access | Bearer token + rate limiting from day 1 |

## UI/UX Improvements (Next Sprint)

Identified during Web Channel v1 verification. Reference: DeepSeek chat interface.

| Item | Description | Effort |
|------|-------------|--------|
| Chat area centering | `max-w-3xl mx-auto` on message container + input box | 15min |
| Simplify message bubbles | Remove blue fill on user messages; use right-align + subtle border only | 30min |
| Session title from content | First user message as title (not session ID hash) | 30min |
| Session time grouping | Group sidebar by "Today" / "Last 7 days" / "Earlier" | 1h |
| Cluster status bar | Bottom bar showing worker dots + "Session on Worker X" (admin only) | 2h |
| Markdown rendering | Proper heading sizes, lists, inline code, code blocks with copy button | 2h |
| Input box refinement | Rounded corners, subtle shadow, centered with chat area | 15min |
| Light mode polish | White background, subtle borders, match DeepSeek's clean aesthetic | 1h |

**Design reference**: DeepSeek chat UI — centered conversation max-width ~800px, clean white, grouped sessions, no heavy bubbles.

## Publication Schedule

| Week | Article | Prerequisite |
|------|---------|-------------|
| Week 1 | "从 TypeScript 单体到存算分离" | Web Channel complete (demo link) |
| Week 2 | "为什么 pyclaw 选了 Python 而不是 Go" | None |
| Week 4 | "Agent 的上下文引擎该怎么设计" | Memory Store complete |
| Week 5 | "让 openclaw 的 skill 零改动跑在 pyclaw 上" | None (already done) |
