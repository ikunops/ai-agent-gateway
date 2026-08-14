<!-- SEO keywords: AI网关, 大模型网关, LLM路由, OpenAI兼容代理, 智能路由, 模型网关, AI中间件, Agent网关 -->
# AI Gateway — The Routing & Navigation Layer Between Agents and LLMs

**AI Gateway（AI 网关）** is a routing/navigation layer between AI agents and large language models (LLM). It does **not** think, remember, or execute — it only handles the stretch between "a user session arrives" and "the request is handed to a model": request cleaning, context, routing, System-prompt construction, and cache-hit-rate optimization.

[English](README.md) | [中文](README.zh.md)

> **Status**: Phase 1–3 complete, Phase 4 partially complete (permission tiers + routing ledger). 137 tests passing.
> Detailed architecture: [docs/architecture.md](docs/architecture.md)

## What It Does

- **Transparent proxy mode (primary, in use)** — `POST /v1/chat/completions`: routes by model name to the matching upstream (`config.yaml` ships with `zen-go` / `zen-free` pools), applies request enhancement + permission gating + response caching, then forwards with **full field passthrough** (`tools` / `tool_choice` / `stream_options` etc.) — no whitelist trimming.
- **Refine mode (alternate)** — `POST /v1/refine`: returns an "enhanced request" (`refined.messages`) plus routing metadata (`meta`), which the agent side forwards to whatever model the user currently selected.
- **No model keys held by the gateway** — models are chosen on the agent side; the gateway is model-agnostic.
- **Classifier pool (round-robin)** — `gateway.classifiers` hits the local pool one classifier at a time (first success wins, with failover), falling back to a remote classifier only if all local ones fail; if none are configured it uses pure local rule routing. See `app/upstream/classifier.py`.

## Architecture

```
┌─────────────┐
│   Agent     │ opencode / other
└──────┬──────┘
       │ POST /v1/chat/completions
       ▼
┌────────────────────────────────────────────────────────────────┐
│                        AI Gateway                              │
│                                                                │
│  L1 Preprocess ──► Exact Cache ──► L2 Routing ──► L3 Cache     │
│  auth/clean     (request hash)    tri-route     Tier1-4        │
│  session/       HIT → return      vector+KB+    project/       │
│  project parse                     LLM          session ID     │
│                                                                │
│  L4 System Build ──► L5 Egress                                  │
│  prefix stable     forward / stats / audit                      │
└────────────────────────────────────┬───────────────────────────┘
                                     │ forward
                                     ▼
                          DeepSeek / any upstream model
```

### Request Pipeline

1. **L1 Preprocess**: API-key auth → text normalization → parse `X-Project-Id` / `X-Session-Id`
2. **Exact cache**: request-body hash hit → return cached response directly (stream/non-stream share, SSE assembled) — zero upstream cost
3. **L2 Routing**: tri-route arbitration (technical-term fast path + semantic vector + lightweight LLM classifier), long-text segmented with max aggregation; ambiguous dev requests ("build a phone cleaner") skip routing into **clarification mode** (one round per session, question injected at the tail of the user message); routing summaries follow a **fidelity contract** (extractive, negative/conjunction/symbol segments forced-kept, hallucination-checked); every decision writes to the **Routing Ledger** (aggregate at `/v1/stats/routing`)
4. **L3 Four-tier cache fallback (never misses)**:
   | Tier | Source | Key |
   |---|---|---|
   | Tier1 | global tech-stack common rules | routing tag (e.g. java/mysql/k8s) |
   | Tier2 | project AGENTS.md (registry mapping) | project_id |
   | Tier3 | session summary (backfilled each round) | session_id |
   | Tier4 | empty System fallback | - |
5. **L4 System construction (prefix stabilization)**: System holds only stable content (anchor protocol + tier-hit product + routing context); dynamic noise (timestamps/random numbers) sinks to the tail of the user message → higher model-side prompt-cache hit rate
6. **L5 Egress**: stream/non-stream forward → buffer backfill exact cache → session-summary backfill Tier3 → stats + audit to disk

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start (default forwarding mode: opencode Zen free endpoint, no key needed)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8901

# 3. Verify
curl http://127.0.0.1:8901/v1/health
```

On Windows you can also use the helper script:

```powershell
scripts\gateway.ps1 start|stop|restart|status
```

## Connecting opencode

```bash
# Use /v1/chat/completions directly (forwarding mode)
curl -X POST http://127.0.0.1:8901/v1/chat/completions \
  -H "X-API-Key: gateway-dev-key" \
  -H "X-Project-Id: myproj" -H "X-Session-Id: sess1" \
  -d '{"messages": [{"role": "user", "content": "build a phone cleaner"}], "model": "deepseek-v4-flash"}'

# Or /v1/refine to get an enhanced request + routing metadata,
# then forward refined.messages to your currently-selected model.
```

Response shape: `refined.messages` (includes the gateway-built System prefix) + `meta` (routing tag / tier / clarification mode / fidelity stats).

## API Overview

| Endpoint | Method | Description |
|---|---|---|
| `/v1/refine` | POST | Refine: request shaping (clean/route/System build), zero model dependency |
| `/v1/chat/completions` | POST | Transparent proxy (configurable upstreams, OpenAI-compatible, stream + non-stream) |
| `/v1/models` | GET | Model list |
| `/v1/health` | GET | Health check |
| `/v1/projects` | POST/GET | Register / list projects (project_id → AGENTS.md path) |
| `/v1/projects/{id}` | DELETE | Unregister project |
| `/v1/cache/clear` | POST | Clear exact response cache |
| `/v1/stats/hits` | GET | Cache hit rate + prefix overlap + tier distribution |
| `/v1/stats/routing` | GET | Routing ledger summary (source/tag/tier + top tech terms + avg latency) |

Headers: `X-API-Key` (auth, required), `X-Project-Id`, `X-Session-Id` (cache keys).

## Tests

```bash
python -m pytest tests -q
```

Covers: cleaning, prefix stabilization, dynamic-noise sinking, four-tier cache fallback, exact cache (stream/non-stream shared hit), SSE assembly, project registry persistence, auth, stats/audit, clarification mode (one-shot), long-text segmented routing, and Routing Ledger. **137 tests passing.**

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| Phase 1 | OpenAI-compatible forwarding + auth + state-first protocol injection + session mgmt | ✅ done |
| Phase 2 | Exact cache + four-tier fallback + prefix stabilization + project registry + session-summary backfill | ✅ done |
| Phase 3 | Tri-route arbitration (vector + KB + lightweight LLM) + heavy/light model selection | ✅ done |
| Phase 4 | Permission tiers L0/L1/L2 + egress danger interception | 🟡 partial (stats/audit/project registration done, rate limiting pending) |

---

Built with Python 3.11 + FastAPI. License: see repo. Contributions welcome.