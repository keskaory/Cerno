# Cerno — Implementation Plan

Status: **v2.0 draft** (2026-11-15). Revised for D9 ($0, local-first). M0 is implemented and pushed on `m0-tooling`. M1 is paused until you've reviewed v2.0.

## Principles

- **Walking skeleton first (ADR-012).** M2 runs end to end using the narrowest working version of each layer. After that, each milestone widens one layer, and only when the eval or the UI needs it.
- **Measure before depending on a guess.** The M1 spikes settle sandbox behavior, which local model to use and how fast it is, hashing, and calibration. Each spike uses hand-written inputs, so none of them waits on later code.
- **Every milestone ends the same way.** Tests, lint, and type checks pass → independent code review → fix what it finds → update the docs, the journal and `LEARNING_GUIDE.md` → commit. Push once tests and the review pass.
- **$0 (D9).** No step needs a paid service. CI uses scripted LLM outputs only. Live LLM work runs on the dev machine through Ollama.

## Proposed repository layout

```
backend/
  cerno/
    api/            FastAPI routes, auth dependency, SSE
    orchestrator/   transition(), runner, budgets, events
    llm/            LLMProvider protocol, Ollama + scripted providers, stage interfaces, prompts
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
| M1 | Spikes S1, S3, S7, S2L, S6 | M0 | Every spike has measured results and a pass/fail outcome against the criteria in `M1_SPIKES.md`, and the model choice (D4) is made. Failures come with an investigation and proposed alternatives. |
| M2 | Walking skeleton (CLI) | M1 | `cerno run` on the seeded dataset names the planted driver. It renders a static HTML report where every number is filled into a slot and each one links to its SQL. VERIFY passes, and every LLM call is logged with its model digest, tokens and latency. It works with `OllamaProvider` and with `ScriptedProvider`, and S2b is done. |
| M3 | Eval v0 + calibration + no-LLM baseline | M2 | `evals/run.py --subset dev` writes scored results with CIs on the local model in under 45 minutes. The calibration suite runs per PR and in a nightly workflow. The no-LLM baseline is scored, and it doubles as Guided mode (ARCH §5.5). |
| M4 | Agent v1 | M3 | PLAN and ASSESS are in, with typed added steps, reservation budgets, MULTIPLICITY, and all terminal states. On the dev set plus 20 null runs (local, about 1–2 hours), driver recall is at least the skeleton's and the no-LLM baseline's (the baselines' scores come from M3), and null specificity is ≥ 90%. Paired results are reported as directional. The statistically meaningful comparison is in M7. |
| M5 | Local service + replay UI + static showcase | M4, D2, D3 | Storage moves to the app DB. `cerno serve` runs a live local investigation that streams to completion. `cerno export-showcase` plus a Pages workflow publish 3 replays that make zero network calls beyond the static files. Playwright passes on the replay, on a live run with `ScriptedProvider`, and on the Guided mode path. **Security groups 1–5, 7–10, and 13 (local-app versions, EVALUATION.md) pass in CI before the showcase is published.** |
| M6 | Hardening | M5 | Security group 12 passes. A test proves the LLM-time budget stops a run. All loading, empty and error states are in. 2–3 people complete the 2-minute replay path without help. |
| M7 | Credible eval (local benchmark) | M6 | 50–80 questions × 3 data seeds, the ReAct baseline on the same local model, Olist semi-synthetic data, and paired CIs, all run overnight on the dev machine with the model pinned by digest. A failure taxonomy with ≥ 10 categorized misses is committed. |
| M8 | Bring your own data | M6 | Upload the Olist CSVs (excluding the geolocation file, which is over the 50 MB cap), review types, confirm keys, joins, and 2 metrics, then run an investigation end to end. Security groups 6 and 11 pass. Retention is enforced. |
| M9 | Case study | M7 | README with a GIF or video, an architecture diagram, the eval table with hardware and model stated, a "what it can't do" section, and a write-up of one bug the eval found. `LEARNING_GUIDE.md` is complete. |

M7 and M8 can run in either order.

## M0 — Tooling

| Task | What and why | Files | Tests | Done when |
|---|---|---|---|---|
| 0.1 Dev environment | WSL2 (D5), uv, and Python 3.12, picked for the most mature wheel support. Node LTS gets installed later for the frontend. Ollama runs on the Windows host for GPU access. | — | — | `uv --version` and `python --version` work in WSL |
| 0.2 Backend skeleton | `pyproject.toml` with ruff, mypy (strict), and pytest, plus an empty `cerno` package | `backend/pyproject.toml`, `backend/cerno/__init__.py` | One trivial import test | `uv run pytest`, `ruff check`, and `mypy` all pass |
| 0.3 CI | GitHub Actions on Ubuntu running ruff, mypy, pytest, and gitleaks | `.github/workflows/ci.yml` | A deliberate lint error fails CI on a throwaway branch | Green run on a PR |
| 0.4 Repo hygiene | `.gitignore`, `.env.example`, README re-saved as UTF-8, real commands added to `CLAUDE.md` | root | gitleaks passes | Diff reviewed; no secrets |

## M1 — Spikes

Criteria and results are in `M1_SPIKES.md`. They run in this order:
1. **S3** (done on Windows; needs a WSL re-run) and **S7**. Neither needs Linux-only features.
2. **S2L and S6** on local models. They need no key and no budget, only GPU time.
3. **S1** once WSL is installed. It also checks that WSL can reach Ollama.

Each spike gets its own branch and PR.

## M2 — Walking skeleton

Python only: no web server, no app DB. Runs are saved as JSON files under `runs/<id>/`, which is enough for replay and LLM-call logs at this stage. The SQLite/SQLAlchemy storage arrives in M5, once D2 is decided. The LLM stages are just RESOLVE and SYNTHESIZE, and the playbook is fixed.

| Task | What and why | Files | Tests | Done when |
|---|---|---|---|---|
| 2.1 Seeded generator v0 | E-commerce data with `customers` (region) and `orders` (channel, status, amount), 24 months of daily history, using the realistic generator (EVALUATION §2). One planted effect: in the final month, fewer orders per active customer in the West region. Writes Parquet plus a ground-truth manifest. | `evals/generators/ecommerce.py` | Same seed gives identical output. The planted effect shows up in the totals. The null seed has no effect. | Generator runs from one command |
| 2.2 Dataset build | Parquet → an immutable DuckDB file: write to a temp file, rename atomically, never write again | `cerno/compute/build.py` | Opens read-only. Rebuilding from the same input gives the same content hash. | Bundled dataset builds |
| 2.3 Semantic models + YAML | `revenue` and `orders_count` (simple), `aov` (ratio), and the dimensions `region` and `channel` | `cerno/semantic/models.py`, `datasets/ecommerce/semantic.yaml` | Bad YAML is rejected. An ambiguous join path is rejected. | Loads and validates |
| 2.4 Compiler + validator | Metric × dimensions × window → SQLGlot AST. N:1 joins only, half-open time windows, a total ORDER BY. The validator (ARCH §4.4) checks the compiled output. | `cerno/semantic/compiler.py`, `cerno/compute/validator.py` | Golden SQL and results match hand-written queries. Security group 5. | Simple and ratio metrics both compile and validate |
| 2.5 Supervisor + worker | Supervisor (spawn, kill, respawn) and a pool of 1 worker with the S1 isolation setup and locked config. Handles `RunQuery` and `RunTool` messages and kills on timeout. | `cerno/compute/supervisor.py`, `cerno/compute/worker.py` | Worker-side parts of security groups 1–4, run against the worker directly. Groups 2–3 are fully tested at M5. | Queries run through the pipe with no API process involved |
| 2.6 Stats core | `describe_change`, `baseline_compare`, `decompose_change` (additive and Kitagawa), MULTIPLICITY (BH), `period_check`, and the per-version data-quality checks | `cerno/stats/` | Hand-computed fixtures. Contributions sum to the total. Kitagawa leaves no residual. Entry/exit bucket. `period_check` cases. | Unit tests pass |
| 2.7 Evidence, slots, derive, VERIFY | Evidence and Claim models, canonical hash, slot renderer with the whitelisted derive expressions, strength computation, and every VERIFY rule | `cerno/evidence/` | Each VERIFY rule has a passing and a failing case, and each strength label has a case | Unit tests pass |
| 2.8 LLM provider + stages | The `LLMProvider` protocol, `OllamaProvider`, `ScriptedProvider`. RESOLVE (transcribe schema + code resolution) produces a QuestionSpec; SYNTHESIZE produces slot claims. Includes Pydantic validation with one retry, LLM-time reservations, and logging of every call to the run file. | `cerno/llm/` | `ScriptedProvider` for everything, including adversarial outputs. One live local smoke test (`-m live`, needs Ollama). | A live local run stays inside its LLM-time budget |
| 2.9 Runner + render + CLI | Fixed playbook → evidence → MULTIPLICITY → SYNTHESIZE → VERIFY → one repair → HTML. Handles the terminal states. | `cerno/orchestrator/`, `cerno/render/html.py`, `cerno/cli.py` | End-to-end test with the fake LLM, including the null seed (expects status `completed`, outcome `no_meaningful_change`, no driver claims) | M2 acceptance criteria met |
| 2.10 S2b | Re-measure latency and tokens on real local runs | journal | — | Time limits in ARCH §5.3 confirmed or revised |

## Status

- **M0 done, not merged.** Branch `m0-tooling` is pushed and reviewed. You'll open its PR in the GitHub web interface. Nothing here assumes it's merged.
- **M1 at checkpoint** (2026-11-16). Spike code and results live on `m1-spikes`.
  - Done: S3 (Windows), S7 (failed; log-level revision investigated), S2L v2 (no model passes → Guided default), S6 (fails for every model; Guided passes).
  - Blocked: S1 and the S3 re-run need WSL2, which isn't installed. You need to install it.
  - Decided: D11 (`baseline_compare` on log levels, 2026-11-16).
  - Waiting for you: D12 (power criterion), D13 (S2L v3 or Guided default), D4 (model), D1 (timeline).
  - **Branch dependency:** `m1-spikes` is branched from `m0-tooling`, because it needs the `spikes` dependency group and the CI config. Merge `m0-tooling` first. If M0 changes during review, `m1-spikes` gets those changes by a merge, not a rebase.
  - **Planning docs** go directly to `main` (D8) and are merged into the feature branches.
- **M2 not approved.** It waits for the M1 checkpoint (results, remaining risks and an updated recommendation) and your approval.
- **Needed before scheduling M2 onward:** D1 (timeline).
- **Needed before M5:** D2 (app DB) and D3 (frontend).
- **Decided by S2L:** D4 (which local model). D6 is superseded by D9.
- **Optional:** D7 (`/ponytail`).
