# Cerno — Implementation Plan

Status: **v1.1 draft** (2026-11-15). **Not approved, and no code exists yet.** Revised after the architecture review (C6, H21, H22, ML20) and the consistency check.

## Principles

- **Walking skeleton first (ADR-012).** M2 runs end to end using the narrowest working version of each layer. After that, each milestone widens one layer, and only when the eval or the UI needs it.
- **Measure before depending on a guess.** The M1 spikes settle sandbox behavior, LLM cost, hashing, and hosting. Each spike uses hand-written inputs, so none of them waits on later code.
- **Every milestone ends the same way.** Tests, lint, and type checks pass → independent code review → fix what it finds → update docs and the journal → commit. Push once tests and the review pass.

## Proposed repository layout

```
backend/
  cerno/
    api/            FastAPI routes, auth dependency, SSE
    orchestrator/   transition(), runner, budgets, events
    llm/            Anthropic client wrapper, stage interfaces, prompts
    semantic/       metric/dimension models, YAML loader, compiler
    compute/        worker pool, worker main, DuckDB config, validator, dataset build
    stats/          describe, baseline, decompose, multiplicity, period_check, data quality
    evidence/       evidence + claim models, slot renderer, derive, VERIFY
    render/         HTML report (M2), later JSON for the SPA
    storage/        SQLAlchemy models, Alembic migrations (M5)
  tests/            unit/, integration/, security/, calibration/
frontend/           React + TS (Vite pending D3)
evals/              generators/, scenarios/, baselines/, run.py, results/
datasets/           bundled seeds + semantic YAML
spikes/             throwaway, not shipped
docs/
```

## Milestones

| | Milestone | Depends on | Acceptance |
|---|---|---|---|
| M0 | Tooling | D5, D8 | CI is green on Linux. `uv run pytest`, ruff, and mypy pass locally. |
| M1 | Spikes S1, S2, S3, S6, S7 (S4 deferred to before M5) | M0 | Every spike has measured results and a pass/fail outcome against the criteria in `M1_SPIKES.md`. Failures come with an investigation and proposed alternatives. |
| M2 | Walking skeleton (CLI) | M1 | `cerno run` on the seeded dataset names the planted driver. It renders a static HTML report where every number is filled into a slot and each one links to its SQL. VERIFY passes, every LLM call is logged with its cost, and S2b is done. |
| M3 | Eval v0 + calibration + no-LLM baseline | M2 | `evals/run.py --subset dev` writes scored results with CIs for ≤ $10. The calibration suite runs per PR and in a nightly workflow. The no-LLM playbook baseline is scored. |
| M4 | Agent v1 | M3 | PLAN and ASSESS are in, with typed added steps, reservation budgets, MULTIPLICITY, and all terminal states. On the dev set plus 20 null runs (≤ $25 total), driver recall is at least the skeleton's and the no-LLM baseline's (the baselines' scores come from M3), and null specificity is ≥ 90%. Paired results are reported as directional. The statistically meaningful comparison is in M7. |
| M5 | Service + replay UI + deploy | M4, D2, D3, S4 | Storage moves to the app DB. The public URL serves 3 replays and makes zero API calls doing it. A live run with an invite code streams to completion. Playwright passes on both the replay and a live run with the fake LLM. Per-IP rate limits are on `/session`, `/questions/resolve` and `/investigations`. **Security groups 1–5, 7–10, and 13 pass in CI before the first deploy.** |
| M6 | Hardening | M5 | Security group 12 passes. A test proves the daily cap stops runs. All loading, empty and error states are in. 2–3 people complete the 2-minute replay path without help. |
| M7 | Credible eval | M6 | 50–80 questions × 3 seeds, the ReAct baseline, Olist semi-synthetic data, and paired CIs. A failure taxonomy with ≥ 10 categorized misses is committed. |
| M8 | Bring your own data | M6 | Upload the Olist CSVs (excluding the geolocation file, which is over the 50 MB cap), review types, confirm keys, joins, and 2 metrics, then run an investigation end to end. Security groups 6 and 11 pass. Retention is enforced. |
| M9 | Case study | M7 | README with a GIF or video, an architecture diagram, the eval table, a "what it can't do" section, and a write-up of one bug the eval found. |

M7 and M8 can run in either order.

## M0 — Tooling

| Task | What and why | Files | Tests | Done when |
|---|---|---|---|---|
| 0.1 Dev environment | WSL2 (D5), uv, and Python 3.12, picked for the most mature wheel support. Node LTS gets installed later for the frontend. | — | — | `uv --version` and `python --version` work in WSL |
| 0.2 Backend skeleton | `pyproject.toml` with ruff, mypy (strict), and pytest, plus an empty `cerno` package | `backend/pyproject.toml`, `backend/cerno/__init__.py` | One trivial import test | `uv run pytest`, `ruff check`, and `mypy` all pass |
| 0.3 CI | GitHub Actions on Ubuntu running ruff, mypy, pytest, and gitleaks | `.github/workflows/ci.yml` | A deliberate lint error fails CI on a throwaway branch | Green run on a PR |
| 0.4 Repo hygiene | `.gitignore`, `.env.example`, README re-saved as UTF-8, real commands added to `CLAUDE.md` | root | gitleaks passes | Diff reviewed; no secrets |

## M1 — Spikes

Criteria and results are in `M1_SPIKES.md`. They run in this order:
1. **S3 and S7** first. They cost nothing and need no Linux-only features.
2. **S1** once WSL is installed.
3. **S6, then S2**, once a dev API key exists. They share a hard $12 spend guard.

Each spike gets its own branch and PR.

## M2 — Walking skeleton

Python only: no web server, no app DB. Runs are saved as JSON files under `runs/<id>/`, which is enough for replay and cost logs at this stage. The SQLite/SQLAlchemy storage arrives in M5, once D2 is decided. The LLM stages are just RESOLVE and SYNTHESIZE, and the playbook is fixed.

| Task | What and why | Files | Tests | Done when |
|---|---|---|---|---|
| 2.1 Seeded generator v0 | E-commerce data with `customers` (region) and `orders` (channel, status, amount), 24 months of daily history, using the realistic generator (EVALUATION §2). One planted effect: in the final month, fewer orders per active customer in the West region. Writes Parquet plus a ground-truth manifest. | `evals/generators/ecommerce.py` | Same seed gives identical output. The planted effect shows up in the totals. The null seed has no effect. | Generator runs from one command |
| 2.2 Dataset build | Parquet → an immutable DuckDB file: write to a temp file, rename atomically, never write again | `cerno/compute/build.py` | Opens read-only. Rebuilding from the same input gives the same content hash. | Bundled dataset builds |
| 2.3 Semantic models + YAML | `revenue` and `orders_count` (simple), `aov` (ratio), and the dimensions `region` and `channel` | `cerno/semantic/models.py`, `datasets/ecommerce/semantic.yaml` | Bad YAML is rejected. An ambiguous join path is rejected. | Loads and validates |
| 2.4 Compiler + validator | Metric × dimensions × window → SQLGlot AST. N:1 joins only, half-open time windows, a total ORDER BY. The validator (ARCH §4.4) checks the compiled output. | `cerno/semantic/compiler.py`, `cerno/compute/validator.py` | Golden SQL and results match hand-written queries. Security group 5. | Simple and ratio metrics both compile and validate |
| 2.5 Supervisor + worker | Supervisor (spawn, kill, respawn) and a pool of 1 worker with the S1 isolation setup and locked config. Handles `RunQuery` and `RunTool` messages and kills on timeout. | `cerno/compute/supervisor.py`, `cerno/compute/worker.py` | Worker-side parts of security groups 1–4, run against the worker directly. Groups 2–3 are fully tested at M5. | Queries run through the pipe with no API process involved |
| 2.6 Stats core | `describe_change`, `baseline_compare`, `decompose_change` (additive and Kitagawa), MULTIPLICITY (BH), `period_check`, and the per-version data-quality checks | `cerno/stats/` | Hand-computed fixtures. Contributions sum to the total. Kitagawa leaves no residual. Entry/exit bucket. `period_check` cases. | Unit tests pass |
| 2.7 Evidence, slots, derive, VERIFY | Evidence and Claim models, canonical hash, slot renderer with the whitelisted derive expressions, strength computation, and every VERIFY rule | `cerno/evidence/` | Each VERIFY rule has a passing and a failing case, and each strength label has a case | Unit tests pass |
| 2.8 LLM stages | RESOLVE produces a QuestionSpec; SYNTHESIZE produces slot claims. Uses structured outputs and budget reservations, and logs every LLM call to the run file. | `cerno/llm/` | Scripted fakes. One live smoke test (`-m live`). | A live run stays under budget |
| 2.9 Runner + render + CLI | Fixed playbook → evidence → MULTIPLICITY → SYNTHESIZE → VERIFY → one repair → HTML. Handles the terminal states. | `cerno/orchestrator/`, `cerno/render/html.py`, `cerno/cli.py` | End-to-end test with the fake LLM, including the null seed (expects status `completed`, outcome `no_meaningful_change`, no driver claims) | M2 acceptance criteria met |
| 2.10 S2b | Re-measure cost and latency on real runs | journal | — | Caps in ARCH §5.3 confirmed or revised |

## Status

- **Active:** M0–M1 (approved 2026-11-15). M2 waits for the M1 checkpoint and your approval.
- **Needed before scheduling M2 onward:** D1 (timeline).
- **Needed before M5:** D2 (app DB) and D3 (frontend).
- **Decided after S2:** D4 (dev model use) and D6 (budgets).
- **Optional:** D7 (`/ponytail`).
