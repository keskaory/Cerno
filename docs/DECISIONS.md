# Cerno — Decision Records

Short ADRs. Status: **proposed**, **accepted**, **superseded**, or **needs decision** (your call). For the longer story, see `DEVELOPMENT_JOURNAL.md`. For the review that drove v1, see `reviews/2026-11-15-architecture-review.md`.

On 2026-11-15 you approved M0–M1 and accepted D5 and D8. The ADRs stay **proposed** until the spikes either confirm them or force a change.

## Pending decisions (yours)

| ID | Decision | Recommendation | Why it matters |
|---|---|---|---|
| D1 | Timeline and weekly hours | — | Sets milestone sizing and what gets cut |
| D2 | App DB: SQLite on the volume vs. Postgres (Neon) | **SQLite** (WAL), portable SQLAlchemy | One process and one volume already; Neon adds a vendor, cold starts and latency for no scaling benefit. Postgres is a later migration, and doing it cleanly is a better story than having it on day one. |
| D3 | Frontend: Vite React SPA served by FastAPI vs. Next.js on Vercel | **Vite SPA, same origin** | One deploy, cookie auth works with SSE, no Vercel 4.5 MB body limit, no CORS. Next.js's SSR/SEO buys nothing here. You still use React, TypeScript, Tailwind and shadcn/ui. |
| D4 | Model use in development: Opus 5.5 throughout, or a cheaper model (Sonnet 5.5 / Haiku 5.5) for dev-loop evals, confirmed on Opus 5.5 at milestones | Opus 5.5 everywhere until S2 measures cost; then decide with the numbers | Cost per eval run vs. results that may not transfer between models |
| D6 | Monthly API budget (dev) and daily cap (prod) | Pick after S2; placeholder $50/month dev, $5/day prod | Sets eval cadence and invite-code budgets. One full milestone suite (M7) costs roughly $180–400 for Cerno plus ReAct, which a $50/month dev budget can't cover. Each full suite needs its own one-off approval. |
| D7 | Where `/ponytail` comes from, if you want it used | — | Not installed and not in the plugin catalog |

## Accepted decisions

### D5 Develop in WSL2 (accepted 2026-11-15)
Ubuntu 24.04 under WSL2 is the dev environment, and CI runs on Ubuntu. Production is Linux, and the worker's isolation features (rlimits, uid switching, network namespaces) only exist on Linux.

### D8 Git workflow (accepted 2026-11-15)
- **Code:** a feature branch per milestone or spike (`m0-tooling`, `m1-s1-sandbox`, …), merged into `main` through a PR.
- **Before a PR:** tests, ruff and mypy pass locally and in CI, and an independent review subagent has gone over the diff. Significant findings get fixed before the merge.
- **Commits:** conventional style (`feat:`, `fix:`, `test:`, `docs:`, `chore:`), each one focused on a single change.
- **Planning docs** can be committed directly to `main`.
- **Forbidden without explicit approval:** force-pushing, rewriting shared history, or deleting branches.

## ADRs

### ADR-001 DuckDB for analytical queries (proposed)
Data arrives as files and the queries are read-heavy, so an embedded columnar engine avoids running a separate warehouse. Each dataset version is one immutable DuckDB file.

### ADR-002 Plain Python state machine, not LangGraph (proposed)
After the review the graph has no tool loop and no mid-run human input (H2, ML1), which removes LangGraph's main selling points. The orchestrator is a pure `transition()` function so it can be tested with scripted fakes.

### ADR-003 Statistics as deterministic tools; no agent-written code (proposed)
They're correct by construction, testable, and calibrated in CI. The cost is that questions outside the toolset can't be answered in v1.

### ADR-004 Narrow hand-rolled semantic layer (proposed)
- **Chosen:** MetricFlow-style metric types (v1: simple and ratio; derived and cumulative types come later if needed), N:1 star joins only, compiled with SQLGlot.
- **Alternatives:**
  - *MetricFlow* (Apache 2.0, has a DuckDB renderer): built around a dbt project and produces verbose SQL.
  - *Cube*: a separate Node service.
  - *Boring Semantic Layer*: early (0.3.x).
  - *DuckDB's `semantic_layer` community extension*: right semantics, but it conflicts with `allow_community_extensions=false`.
- **Why chosen:** readable SQL is a product feature, because users see it in the evidence drawer.
- **Revisit when:** we need multi-fact, cumulative or conversion metrics.

### ADR-005 Ground truth from synthetic and semi-synthetic data (proposed)
Planted effects in a realistic data generator, plus injected effects in real public data (Olist). The limits are written down in `EVALUATION.md`.

### ADR-006 Security boundary is process isolation (proposed; supersedes v0 ADR-006)
DuckDB's own docs say its settings aren't a complete sandbox for untrusted SQL. All DuckDB, ingestion and statistics work runs in worker processes with an empty environment, rlimits and SIGKILL. The locked DuckDB config is the second layer and the SQLGlot validator the third. Prompts are not a control.

### ADR-007 Claude Opus 5.5 with explicit per-stage effort (proposed)
`low` for RESOLVE, `medium` elsewhere, refusal fallbacks on. Cheaper models only with eval evidence and your sign-off (D4).

### ADR-008 Plan-as-data; the model never calls tools (proposed)
The model returns typed step lists via structured outputs and code executes them. Every LLM call is single-turn and rebuilt from state, which keeps caching predictable and avoids the preserved-thinking constraint Opus 5.5 places on multi-turn history.

### ADR-009 Slot-based claims; numbers rendered by code (proposed)
The model writes templates that reference evidence fields, and code renders the numbers. This makes "every number traces to evidence" true by construction, and VERIFY checks everything else (coverage, direction, wording).

### ADR-010 Period changes are judged against a historical baseline, not row-level tests (proposed)
With full-population data, row-level p-values measure nothing useful. "Is this change unusual compared with recent comparable periods?" is the honest question. Fit a linear trend to the k ≥ 12 months before the comparison month and ask whether the comparison month falls outside the one-step-ahead prediction interval (t with k−2 df). Run this for the total and for every segment, and apply BH across all of them. With fewer than 12 months, the result is "not estimable". Group comparisons at the customer level are deferred.

### ADR-011 Single machine, single origin (proposed; depends on D2 and D3)
One Fly machine runs the API, the worker pool, the SQLite app DB, the DuckDB files and the built SPA. Escape routes stay cheap: `owner_id` everywhere, an ID-only runner, and portable SQLAlchemy.

### ADR-012 Walking skeleton before horizontal layers (proposed)
The first build milestone goes end to end, from question to rendered report, using the narrowest version of each layer. Each layer is widened only when the eval or the UI needs it.
