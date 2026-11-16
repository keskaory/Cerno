# Cerno — Decision Records

Short ADRs. Status: **proposed**, **accepted**, **superseded**, or **needs decision** (your call). For the longer story, see `DEVELOPMENT_JOURNAL.md`. For the review that drove v1, see `reviews/2026-11-15-architecture-review.md`.

On 2026-11-15 you approved M0–M1 and accepted D5 and D8. Later that day you set D9 ($0, local-first), which supersedes D6 and ADR-007 and changes ADR-011. Implementation is paused until you've reviewed the revised plan. The ADRs stay **proposed** until the spikes either confirm them or force a change.

## Pending decisions (yours)

| ID | Decision | Recommendation | Why it matters |
|---|---|---|---|
| D1 | Timeline and weekly hours | — | Sets milestone sizing and what gets cut |
| D2 | App DB: SQLite on local disk vs. Postgres | **SQLite** (WAL), portable SQLAlchemy | After D9 there's no hosted DB to consider. SQLite is a file, needs no server, and costs nothing. |
| D3 | Frontend: Vite React SPA served by FastAPI | **Vite SPA, same origin** | It also builds the static showcase for GitHub Pages (ARCH §8.1). Next.js was mainly a Vercel argument, and Vercel is out under D9. |
| D4 | Which local model, at what settings | Pick by spike S2L (criteria in `M1_SPIKES.md`) | Replaces "Opus vs. a cheaper model". Preliminary probe results are in the journal, 2026-11-15. |
| D6 | ~~Monthly API budget and daily cap~~ | **Superseded by D9** | There's no money to budget. Run limits are now LLM time (ARCH §5.3). |
| D10 | Free hosted LLM tiers (e.g. free API keys from model vendors) as an optional provider | **No.** Local only. | They need sign-ups, change terms without notice, often train on your data, and break the "nothing leaves the machine" property. The provider interface keeps the option open if you ever want it. |
| D7 | Where `/ponytail` comes from, if you want it used | — | Not installed and not in the plugin catalog |

## Accepted decisions

### D5 Develop in WSL2 (accepted 2026-11-15)
Ubuntu 24.04 under WSL2 is the dev environment, and CI runs on Ubuntu. Production is Linux, and the worker's isolation features (rlimits, uid switching, network namespaces) only exist on Linux.

### D9 $0 budget, local-first (set by you, 2026-11-15)
Cerno is built and demonstrated for $0 in additional spending. That means no paid APIs, hosting, databases or infrastructure, no billable sign-ups, and no paid fallbacks. The LLM runs locally through Ollama behind a provider interface (ADR-013). Tests use scripted LLM outputs. Live evals use local models only. The public demo is a static replay site on GitHub Pages.

Correctness and security requirements don't change: the worker sandbox, calibration, evidence provenance and reviews all stay. If no local model is good enough, Cerno ships Guided mode (ARCH §5.5) with its limits stated, rather than claiming the model works.

**Supersedes:** D6 (dollar budgets), ADR-007 (Claude Opus 5.5), and the Fly.io hosting in ADR-011.

**Verified free on 2026-11-15** (licenses and pricing pages checked by a research subagent; not legal advice):

| Item | License / price | Condition to respect |
|---|---|---|
| DuckDB, SQLGlot, FastAPI, Pydantic, SQLAlchemy, ruff, mypy, pytest, React, Vite, Tailwind, shadcn/ui | MIT | — |
| NumPy, SciPy, statsmodels, pandas, httpx | BSD-style | — |
| pyarrow, Playwright (Python), TypeScript | Apache-2.0 | Browser binaries installed by Playwright weren't checked |
| uv | MIT / Apache-2.0 | — |
| SQLite | public domain | — |
| Ollama | MIT; local models are free and unlimited | Never use `-cloud` models (paid, and they send data off the machine). Enforced in code (ARCH §2.7). |
| Qwen3 models, gpt-oss | Apache-2.0 | — |
| Gemma 3 | Gemma Terms of Use (custom) | Using it locally and publishing its outputs is fine. The pass-through obligations apply if the model is redistributed or hosted for others, which we don't do. We still prefer Apache-2.0 models when results are close. |
| GitHub Actions (standard runners), GitHub Pages | free for **public** repos (this repo is public, checked 2026-11-15) | Pages: 1 GB site, 100 GB/month soft bandwidth, no commercial SaaS. Making the repo private would make Pages a paid feature. |
| gitleaks-action v2 | proprietary EULA, free for personal accounts | An organization account would need a license key |
| Olist dataset | CC BY-NC-SA 4.0 | Non-commercial only. Attribute Olist and Kaggle. A redistributed subset goes in its own folder under the same license. |

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

### ADR-007 Claude Opus 5.5 with explicit per-stage effort (superseded by D9 and ADR-013)
Was: `claude-opus-5-5`, `low` effort for RESOLVE and `medium` elsewhere. Dropped because it costs money.

### ADR-008 Plan-as-data; the model never calls tools (proposed)
The model returns typed step lists via structured outputs and code executes them. Every LLM call is single-turn and rebuilt from state. With a small local model this matters even more: filling one constrained JSON object is the thing small models do reliably, and multi-turn tool loops aren't.

### ADR-009 Slot-based claims; numbers rendered by code (proposed)
The model writes templates that reference evidence fields, and code renders the numbers. This makes "every number traces to evidence" true by construction, and VERIFY checks everything else (coverage, direction, wording).

### ADR-010 Period changes are judged against a historical baseline, not row-level tests (proposed)
With full-population data, row-level p-values measure nothing useful. "Is this change unusual compared with recent comparable periods?" is the honest question. Fit a linear trend to the k ≥ 12 months before the comparison month and ask whether the comparison month falls outside the one-step-ahead prediction interval (t with k−2 df). Run this for the total and for every segment, and apply BH across all of them. With fewer than 12 months, the result is "not estimable". Group comparisons at the customer level are deferred.

### ADR-011 Single local machine, single origin, static showcase (proposed; revised for D9)
Your machine (WSL2) runs the API, the worker pool, the SQLite app DB, the DuckDB files and the built SPA, on 127.0.0.1. The public demo is the same SPA in replay-only mode on GitHub Pages. Escape routes to a hosted version stay cheap: `owner_id` everywhere, an ID-only runner, and portable SQLAlchemy.

### ADR-012 Walking skeleton before horizontal layers (proposed)
The first build milestone goes end to end, from question to rendered report, using the narrowest version of each layer. Each layer is widened only when the eval or the UI needs it.

### ADR-013 Provider-neutral LLM interface; Ollama first (proposed)
Stages depend on one `LLMProvider` protocol that takes a typed request and returns a parsed object or a typed failure, plus usage and latency (ARCH §5.2). v1 implements `OllamaProvider` and `ScriptedProvider`.
- **Alternatives:**
  - *LiteLLM*: many providers behind one interface, but a large dependency for two backends.
  - *The OpenAI-compatible endpoint Ollama exposes*: works, but Ollama's native API exposes `format` (a JSON schema), `seed` and the model digest more directly.
  - *Calling Ollama from each stage directly*: couples every stage to one vendor.
- **Why:** about 100 lines of our own code, fully testable with scripted outputs, and a third provider is one class.
- **Risk:** the native API could change. The adapter is the only place that knows about it.
