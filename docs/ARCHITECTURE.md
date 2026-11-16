# Cerno — Architecture

Status: **v1.1 draft** (2026-11-15). Revised after the six-reviewer architecture review and the cross-document consistency check (`reviews/2026-11-15-architecture-review.md`).

How to read the references in this doc:
- Review finding IDs are C# (critical), H# (high) and ML# (medium/low).
- Milestones are M0–M9.
- **[D#]** marks something that waits on a pending decision in `DECISIONS.md`.
- **[S#]** marks something that waits on a spike in `RISKS.md`.

## 1. Shape of the system

```
 Browser (React SPA, served from the same origin)
     │  HTTPS JSON + SSE, HttpOnly session cookie
     ▼
┌──────────────────────────── one container, one machine ─────────────────────────────┐
│ supervisor (root, ~100 LOC): starts API, spawns/kills/respawns workers               │
│ API process (FastAPI, asyncio), uid cerno-api  Worker processes (pool of 2),         │
│  routes · auth · run orchestrator ──pipe──▶   uid cerno-worker                       │
│  LLM client (AsyncAnthropic) · event bus       DuckDB + pandas + SciPy/statsmodels    │
│  never imports duckdb                          empty env · rlimits · no network [S1] │
│        │                                       one dataset file, read-only           │
│        ▼                                              ▼                              │
│  SQLite app DB (WAL) [D2], mode 0600           /data/datasets/<version>.duckdb       │
│  on persistent volume                          on persistent volume (immutable)      │
└──────────────────────────────────────────────────────────────────────────────────────┘
                 │
                 ▼
            Claude API
```

The design is one machine, one volume, one API process and a small worker pool. That's not a shortcut. DuckDB files on a volume pin us to one machine anyway (H11), so the design accepts that and keeps the escape routes cheap (§2.6).

## 2. Backend service

### 2.1 API (FastAPI + Pydantic v2)

| Method | Path | Purpose |
|---|---|---|
| POST | `/session` | exchange an invite code for an HttpOnly session cookie |
| GET | `/healthz` | liveness, free disk, worker pool status |
| GET | `/datasets` | list bundled (later also uploaded) datasets |
| GET | `/datasets/{id}` | tables, profile, metrics, dimensions |
| POST | `/questions/resolve` | turn question text into an editable `QuestionSpec`; no run starts |
| POST | `/investigations` | start a run from a confirmed `QuestionSpec` |
| GET | `/investigations/{id}` | full state: spec, plan, steps, evidence, claims, report |
| GET | `/investigations/{id}/events` | SSE stream: typed events, heartbeat, `Last-Event-ID` replay |
| POST | `/investigations/{id}/cancel` | cooperative cancel |
| GET | `/evidence/{id}` | SQL, params, canonical result preview, provenance |
| GET | `/public/investigations/{id}` | read-only replay of demo investigations (no session needed) |
| POST | `/datasets` (milestone M8) | upload |

The Pydantic models are the API contract. SSE event payloads are a discriminated union that gets exported into the OpenAPI schema (H13). TypeScript types are generated from that schema with `openapi-typescript`, and CI fails if they're out of date.

### 2.2 Runs

- **The orchestrator only does I/O.** It's an asyncio task that makes LLM calls, sends requests to workers over a pipe, and writes to the DB. Every CPU-bound or DuckDB call goes to a worker (§4), so the event loop never blocks (C1).
- **State lives in the DB.** The runner is `run(investigation_id)`. It rebuilds all state from the DB and never depends on request objects. Every LLM output is saved before it's used, so replaying a run never calls the model again (ML14).
- **Concurrency.** A global semaphore allows 2 investigations at once; anything beyond that gets a 429. Each invite code gets 1 concurrent run (H9).
- **Restarts and shutdown.** On startup, any `running` investigation is marked `interrupted`. On SIGTERM, the server stops accepting new runs, cancels active ones cooperatively, and writes a terminal event. v1 offers **re-run**, not resume (H12).
- **Cancel** sets a DB flag that's checked between steps. If a query is running when the flag is set, its worker is killed.

### 2.3 Events

1. Each event is written to the DB first, then published to an in-process channel for that investigation.
2. SSE subscribers backfill from the DB starting after `Last-Event-ID` (a monotonic `seq`), then follow the channel.
3. A `: ping` heartbeat goes out every 15 s so proxies don't drop the stream while the model is thinking (H13).

There's no DB polling.

### 2.4 Persistence

- **Stack:** SQLAlchemy 2 and Alembic. **[D2]** The recommendation is SQLite in WAL mode on the volume, using only portable types (UTC timestamps as ISO text; JSON stored as JSON/TEXT).
- **Migrations** run at app boot.
- **Backups:** Fly volume snapshots (daily by default) plus a nightly `VACUUM INTO` copy.
- **Tables:** `invite_codes`, `sessions`, `datasets`, `dataset_versions`, `tables`, `columns`, `dimensions`, `metrics`, `relationships`, `investigations`, `question_specs`, `steps`, `evidence`, `claims`, `events`, `llm_calls`, `budget_reservations`.
- **Ownership and deletes:** every user-owned table has an `owner_id`. Deletes cascade from `dataset_versions` and from `investigations` (ML5).

### 2.5 Auth (v1)

- **Invite codes.** Each person gets their own code. Only the SHA-256 hash is stored, along with an expiry and a dollar budget. The code is exchanged for a session cookie (`HttpOnly; Secure; SameSite=Strict`). Because the SPA and API share an origin, EventSource works and no credential ever goes in a URL (H10).
- **Ownership checks.** Every resource lookup goes through one helper, `get_owned(model, id, principal)`. A parametrized test calls every route with another owner's IDs and expects a 404. IDs are UUIDv4.
- **Public demos.** A demo investigation is public only if it has `is_public = true`.

### 2.6 Escape routes we keep cheap

1. `owner_id` is on every table, so real auth only means replacing the invite-code principal in one dependency.
2. The runner takes nothing but an ID and reads its state from the DB, so moving to a job queue only means swapping the notifier.
3. SQLAlchemy stays portable, so moving to Postgres is a connection string plus a data copy.

### 2.7 Configuration and secrets

- Settings come from environment variables via `pydantic-settings`. `.env.example` lists all of them.
- Dev, eval and prod each have their own Anthropic API key and console spend limit (ML8).
- gitleaks runs in CI.
- Secrets live only in the API process environment. The app DB and secrets are mode 0600 and owned by `cerno-api`, and workers run as a different uid (§4.1).

## 3. Data layer

### 3.1 Datasets, versions, retention

- **Versions are immutable DuckDB files.** A file is written once: it's built at `tmp/<uuid>.duckdb`, closed, moved into place with `os.replace()`, and from then on only opened read-only.
- **Evidence is tied to a version.** Each evidence row records the version it ran against. "Reproducible" means re-runnable **while that version exists** (ML18).
- **Retention.**
  - Uploaded versions (milestone M8) expire 30 days after their last use, and a nightly deletion job removes the file and every row that depends on it.
  - Evidence from an expired version keeps its stored preview and hash, and the UI says the data is gone.
  - Bundled and demo versions are pinned and never expire.
- **Bundled datasets in v1** (H20, H21):
  - **Seeded e-commerce:** 24 months of daily history, the planted effect in the final month. Used for both the demo and the eval.
  - **Semi-synthetic Olist:** real public data with injected effects.

### 3.2 Upload and ingestion (milestone M8, designed now)

- **Upload limits.** Uploads stream to `<uuid>.upload`, and the size cap (50 MB, 10 tables) is enforced while streaming. Parquet is checked by its magic bytes and its footer is pre-checked (rows × columns, uncompressed size) before loading. CSV is checked to be text and its encoding is detected (ML9).
- **Two-pass typing (H17):**
  1. Detect the encoding and transcode to UTF-8.
  2. Load everything with `all_varchar=true` into a staging table.
  3. Infer each column's type from the whole column using `TRY_CAST` success rates. This handles currency and percent strings, null tokens (`N/A`, `#N/A`, `-`), IDs with leading zeros (kept as text), and date formats (checked against every value).
  4. The user reviews the inferred types.
  5. A typed `CREATE TABLE AS SELECT <casts>` builds the final tables. The cast spec is saved with the version, and money columns become `DECIMAL(18,2)`.
- **Table names** are generated slugs. Original filenames are kept only as display labels.
- **Keys (H15).** A column with a uniqueness ratio of at least 0.99 is a candidate key, and its duplicate count is stored. The user confirms each table's grain key.
- **Joins.** A candidate must point at a confirmed key, have a compatible normalized type, and pass a minimum distinct count. Name similarity only ranks candidates. Containment is measured both ways, orphan counts are reported, and composite keys are tried only when no single-column key exists.

### 3.3 Semantic layer: metrics and dimensions (C5)

The metric types follow MetricFlow's. v1 supports two of them, `simple` and `ratio`. The compiler is our own and deliberately narrow (ML17).

```yaml
metrics:
  - name: revenue
    type: simple
    table: orders
    agg: sum                 # sum | count | count_distinct | avg | min | max
    column: amount
    filters: [{column: status, op: "=", value: paid}]   # structured, never SQL text
    time_column: created_at
    entity_key: order_id     # grain as a column
    analysis_unit: customer_id   # used by group comparisons (deferred); kept in the schema now
    additive: true
    unit: USD
  - name: orders_count
    type: simple
    table: orders
    agg: count_distinct
    column: order_id
    additive: true           # distinct orders across disjoint segments add up; distinct customers would not
    # ...
  - name: aov
    type: ratio
    numerator: revenue
    denominator: orders_count
dimensions:
  - {name: region, table: customers, column: region}
  - {name: channel, table: orders, column: channel}
dataset:
  timezone: UTC
  week_start: monday
```

Rules:
- **Join paths.** The compiler uses the shortest unambiguous **N:1 path** over confirmed relationships, from the metric's base table to the dimension's table. If the path is ambiguous, the dimension is rejected when it's defined.
- **Ratio metrics** aggregate the numerator and the denominator separately, each at its own grain, and divide afterwards.
- **Decomposition.** Non-additive simple metrics can't be decomposed additively. Ratio metrics use the mix/rate method (§6).
- **Time windows** are half-open, `[start, end)`, in the dataset's timezone. Periods are calendar months.
- **New versions.** When a new version arrives (milestone M8), metrics and relationships are copied forward with status `needs_revalidation`. They're only re-confirmed automatically if they still compile and their join cardinality hasn't changed.

### 3.4 Compiler

`compile(metric, dimensions, filters, window) → sqlglot.Expression`. It uses SQLGlot's expression builder and quotes every identifier. The output has to stay readable, because people see it in the evidence drawer. Every query gets a total `ORDER BY`. Golden tests compare both the compiled SQL and its results against hand-written reference SQL.

**The product runs no model-written SQL in v1 (H16).** The only exception is the ReAct eval baseline (`EVALUATION.md`). Its SQL goes through the same worker and the validator in "freehand" mode (§4.4), and it can't be reached from the product.

### 3.5 Data quality and period checks

- **Per version, once:**
  - duplicate keys
  - nulls in metric columns
  - negative amounts
  - future dates
  - gaps in the time series
  - instruction-like text: a string value or identifier that matches a small regex list (e.g. `ignore (all|previous)`, `system:`, `you are`, `instruction`) or is longer than 200 characters. These are flagged as caveats, not removed.
- **Per question (`period_check`, H7):**
  - It flags an incomplete period (the data ends before the period does), unequal day counts, and different weekday mixes.
  - An incomplete period blocks the comparison. RESOLVE then offers a day-aligned alternative instead, such as month-to-date vs. prior month-to-date.
  - Changes are compared per day, so 31-day and 30-day months stay comparable.

## 4. Compute worker (C1)

### 4.1 Process model

- **Pool.** Two long-lived worker processes, started with `multiprocessing` spawn.
- **Supervisor.** The container's entrypoint is a small supervisor (~100 lines). It's the only process that stays root, and it has no network listener and makes no LLM or DB calls. It:
  - starts the API as uid `cerno-api`, passing secrets only to that process
  - spawns each worker by calling `unshare(CLONE_NEWNET)`, applying rlimits, clearing the environment, and then dropping to uid `cerno-worker`
  - kills and respawns workers. The API can't signal a process owned by another uid, so it asks over a control socket that only `cerno-api` can open ("kill worker N"). The supervisor also respawns any worker that dies on its own.
  
  The API talks to workers over per-worker Unix sockets.
- **Isolation.** Each worker gets:
  - an **empty environment**
  - an empty working directory
  - `RLIMIT_AS`, `RLIMIT_CPU` and `RLIMIT_FSIZE` limits
  - a per-worker temp directory that isn't on the dataset volume
  - a network namespace with no interfaces
  
  **[S1/S4]** We still need to verify that `unshare` and the uid switch work on the host. If `unshare` isn't allowed, the network control falls back to DuckDB's `enable_external_access=false` plus the fact that the worker contains no code that touches the network. That fallback will be documented as a residual risk.
- **Messages.** Requests are typed: `RunQuery(version_id, sql, limits)` and `RunTool(version_id, tool, params)`. The worker resolves `version_id` to a path itself. Paths never come from the client or the model.
- **Results** come back over the pipe as Arrow or JSON, with a byte cap per cell and in total.
- **Timeouts** escalate in steps: the API sends a cancel message (which makes the worker call `interrupt()`), then after a grace period the supervisor sends **SIGKILL**, then respawns the worker (ML4).
- **Stats.** Statistics tools also run in the worker.

### 4.2 DuckDB configuration

All of this goes into `duckdb.connect(path, read_only=True, config={...})`. None of it is set with `SET` (ML7).

```
enable_external_access=false   autoload_known_extensions=false
autoinstall_known_extensions=false   allow_community_extensions=false
allow_persistent_secrets=false   memory_limit=<below RLIMIT_AS>   threads=1
max_temp_directory_size=512MB   temp_directory=<worker tmp>   TimeZone=UTC
max_expression_depth=<cap>   lock_configuration=true
```

`threads=1` keeps float sums deterministic **[S3]** and costs little at our data sizes. **[S1]** will verify the whole config, including spill behavior on read-only files.

### 4.3 Limits

- Each query has a 10 s wall-clock limit.
- Every query is wrapped as `SELECT * FROM (<q>) LIMIT 10001`, so we can tell when a result was truncated.
- At most 50 aggregated result rows (or a summary) per step go to the LLM.

### 4.4 Validator

The validator is a correctness layer, not the security boundary. It checks:
- a single `SELECT`
- a **function allowlist**: every `Anonymous` node and every table function is rejected
- that table references are schema identifiers (string-literal tables are rejected)
- that no nondeterministic functions are used

The SQL that actually runs is the regenerated `ast.sql("duckdb")`, not the original text. In the product it checks compiled SQL. "Freehand" mode adds a runtime grain check (`count(*) - count(distinct pk)` for each summed table) and is used only by the ReAct baseline. Metric filters are parsed and validated when the metric is confirmed (ML3).

## 5. Agent

### 5.1 Flow

```
question ─▶ RESOLVE (LLM, low effort) ─▶ QuestionSpec ─▶ user confirms/edits ─▶ run:

  PLAN (LLM: playbook params + dimensions) ─▶ EXECUTE (code, worker)
        ▲                                         │
        └── ASSESS (LLM: add ≤3 steps or stop) ◀──┘   at most 2 ASSESS rounds
  ─▶ MULTIPLICITY (code) ─▶ SYNTHESIZE (LLM → slot claims) ─▶ VERIFY (code)
  ─▶ [one repair: SYNTHESIZE + VERIFY] ─▶ RENDER (code) ─▶ terminal state (§5.4)
```

- **QuestionSpec (ML1):** `{question_type, metric, comparison_window, baseline_window, dimensions_in_scope, filters}`.
  - Code resolves relative dates against the dataset's last date and runs `period_check` here.
  - The user sees the spec as editable chips. The run never blocks waiting for input.
  - The eval harness accepts RESOLVE's spec as is, including a day-aligned alternative when there's a partial period, and scores the spec as one more output.
- **Playbook (H3).** v1 has one, for period changes. It runs:
  1. `describe_change` on the total
  2. `baseline_compare` on the total
  3. `decompose_change` over each dimension in scope
  4. `baseline_compare` for every segment of each dimension in scope (these p-values feed MULTIPLICITY)
  5. a mix/rate split for ratio metrics
  6. data-quality caveats
  
  The LLM picks parameters and which dimensions are worth including. It doesn't invent the procedure.
- **ASSESS** can add typed steps (`add_step` with a reason, at most 3 per round, at most 2 rounds) or stop. Added steps are flagged **exploratory** (H4), and code rejects duplicates.
- **A hypothesis** is a planned or added step together with its stated reason. One that's planned but never executed is reported as **Untested**. One whose result is within normal variation shows in the timeline as "checked: within normal variation".
- **Stopping** is deterministic. Once the playbook steps are done, the run moves to MULTIPLICITY when any of these holds:
  - ASSESS returns `stop`
  - both ASSESS rounds are used
  - the budget reserve has been reached
  
  We dropped the earlier share-based rule. Shares can be negative, can exceed 100%, and are suppressed when movements offset, so they don't work as a stopping signal.

### 5.2 How the model is called (H2)

- **The model never calls tools.** PLAN and ASSESS return typed step lists through structured outputs (`output_config.format` via `client.messages.parse()`). The output is a discriminated union of step types, with enums for metric and dimension names. Code validates and executes the steps.
- **Every LLM call is single-turn** and built from state. No history is replayed, so Opus 5.5's preserved-thinking binding never applies, and we don't send `tools`.
- **Cache layout:**
  1. a constant system prompt (role and rules)
  2. the dataset context (schema, metrics, dimensions and profile, as canonical sorted JSON), with cache breakpoint 1 placed after the system prompt and context
  3. the evidence ledger in append order, with breakpoint 2
  4. the stage instruction, last
  
  **[S2]** checks whether a small dataset context clears the model's minimum cacheable length. If it doesn't, we skip the cache-hit assertion for small datasets rather than padding the prompt.
- **Model settings.** `claude-opus-5-5` with effort set explicitly per stage: `low` for RESOLVE, `medium` for everything else. Server-side refusal fallbacks are on. We check `stop_reason` for `refusal` and `max_tokens` before parsing anything. **[D4]** Whether to use cheaper models during development is your call.
- **Untrusted strings** (column names and values) are cut to 64 characters and stripped of control and markup characters before they go into a prompt (H8).

### 5.3 Budgets (H1)

- **Dollars are the only cap on tokens.** Before each call, its worst-case cost (counted input tokens plus `max_tokens` × price, including cache pricing) is **reserved** atomically against three budgets:
  1. the run (RESOLVE runs before a run exists, so it's reserved against only the other two)
  2. the invite code
  3. a global daily cap
  
  After the call, the reservation is settled to the actual cost across all `usage` fields.
- **Per-stage `max_tokens`.** Each stage has its own limit. A reserve big enough for SYNTHESIZE plus one repair is held back from the start, so a run that's cut short still produces a report.
- **Run limits:**
  - at most 5 LLM calls per run (PLAN 1, ASSESS ≤ 2, SYNTHESIZE 1, repair ≤ 1), plus 1 RESOLVE before the run
  - 40 executed steps (per-segment baseline checks count as one step for each dimension)
  - 4 minutes of wall clock
  - 3 consecutive worker errors
- **Per-run hard cap: $0.75.** The p50 target is $0.50 or less. Both are provisional until **[S2]** and the M2 re-measurement.

### 5.4 Terminal states and outcomes

| Status | Meaning | Report |
|---|---|---|
| `completed` | VERIFY passed, possibly after one repair | full report |
| `incomplete_coverage` | the coverage rule still failed after the repair | report plus a code-generated claim for each uncovered q < 0.05 segment, marked as generated |
| `cut_short` | a limit was hit | report from the evidence so far, stating which limit |
| `cancelled` | user cancelled | evidence so far; SYNTHESIZE doesn't run |
| `interrupted` | process restart | evidence so far; re-run offered |
| `failed` | unrecoverable error, or every claim was dropped | evidence so far plus an error summary |

Reports with status `completed`, `incomplete_coverage` or `cut_short` carry one **outcome**, computed by code from the evidence that exists. `cancelled`, `interrupted` and `failed` carry no outcome.
- `change_beyond_normal_variation`: the total, or at least one segment, has an adjusted q < 0.05.
- `no_meaningful_change`: at least one test was estimable, and none has an adjusted q < 0.05.
- `not_estimable`: neither the total nor any segment had enough history. The report is descriptive only.

## 6. Statistics (deterministic, in the worker)

Every inferential output includes:
- the effect in metric units, with an interval
- the p-value and the adjusted q-value
- `k`, the number of historical periods used
- the MDE, when the result is inconclusive
- the `exploratory` flag
- `interpretation_limits`

Seeds are fixed and recorded.

| Tool | Method |
|---|---|
| `describe_change` | Exact Δ and Δ%, per-day values, and `period_check` flags. No inference. |
| `baseline_compare` (C3) | **One-step-ahead prediction interval on levels, not on differences.** Fit OLS `level_per_day ~ a + b·t` on the k ≥ 12 periods before the comparison period (the baseline period is the last of them). Predict the comparison period. The test statistic is `(observed − predicted) / SE_pred`, with `SE_pred = s·√(1 + 1/k + (t₀ − t̄)² / Sₜₜ)` and a t-distribution with **k − 2** df. Reports the unexpected change (observed − predicted) with its prediction interval in metric units, the p-value, the descriptive change from the baseline period, and the empirical percentile of past residuals (display only). Working on levels sidesteps the MA(1) correlation that consecutive differences carry. "Periods" are calendar months. For a day-aligned month-to-date comparison, each period is the first *d* days of its month. Gates: k < 12 → `not_estimable`; a segment that is zero in any of the k periods, or averages under 30 orders per period → `not_estimable` for that segment. Runs on the total and on each segment. |
| `decompose_change` (H6) | **Additive metrics:** segment contributions plus gross positive and negative movement. **Ratio metrics:** symmetric Kitagawa (midpoint weights) into rate and mix effects, with no residual. Both add an entry/exit bucket for segments that appear or disappear. Share of change is suppressed when gross/|net| > 3; "offsetting movements" is reported instead. Flags a Simpson sign reversal and asserts that contributions sum to the total. Works on one dimension at a time. If the top segment in each of two dimensions has a share above 50%, a 2-way crosstab is added. Descriptive only, so it produces no p-values. |
| **MULTIPLICITY** (H4) | BH at α = 0.05 over every p-value in the ledger: the total plus every segment `baseline_compare`, across all dimensions. Segments from different dimensions overlap and share period shocks, so BH's dependence assumption isn't guaranteed. The calibration suite measures the "any driver" false-positive rate under exactly that structure. If it lands outside the band, we switch to BY. A segment picked out by `decompose_change` never gets its own fresh test; its inference is its `baseline_compare` row, corrected along with the rest. **MDE** is reported at the conservative Bonferroni threshold α/m: the smallest unexpected change that test could have flagged. |

**Deferred from v1:** `compare_groups` and `scan_segments` (cluster bootstrap and score tests at the analysis-unit level; their design is in the review log under C4 and ML12–ML13), anomaly detection, cohort retention, and decomposition across more than one factor at a time (ML10, ML11).

**Known limits:**
- With 12–23 monthly points, prediction intervals are wide. That's honest, and the report says so.
- A linear trend can't model annual seasonality or curved growth, which is what Olist-style data has. The calibration suite includes an annual-seasonality scenario, so this failure gets *measured and reported* rather than assumed away. Seasonal adjustment needs at least 2–3 years of history and is deferred.
- Residual autocorrelation between months would make the intervals too narrow. The generator includes per-period shocks, and calibration checks for this.
- Calibration on our generator doesn't guarantee calibration on real data.

**Calibration suite (H18):**
- The data generator includes customer heterogeneity (gamma–Poisson order frequency, lognormal order values), weekly seasonality, AR(1) daily shocks, and a shared shock per period.
- History length is drawn from 13–24 months per seed, so both short and long histories get calibrated.
- `baseline_compare` and the full BH pipeline (several dimensions with overlapping segments) run on null seeds. The "any driver" rate and prediction-interval coverage must fall inside a binomial band around their nominal values.
- An annual-seasonality scenario is reported but not asserted (see known limits).
- About 200 seeds run per PR, and 1,000 or more run nightly and at milestone sign-off.

## 7. Evidence, claims, VERIFY (C2)

```
Evidence: id, kind (query|tool|derive), step_id, dataset_version_id, sql, params,
          canonical_result (preview ≤ 50 rows + totals + rows_omitted), result_hash,
          seed, engine_versions, truncated, created_at
Claim:    id, template, slots[], strength (computed), exploratory, caveats[]
Slot:     {ref: "ev_12.rows[region=West].delta_pct", fmt: "pct1"}
        | {expr: "share(ev_12.rows[region=West].delta, ev_3.total_delta)", fmt: "pct0"}
        | entity slots (labels from evidence)
```

- **SYNTHESIZE writes no numbers.** It writes templates such as "{ev_12.rows[0].region} accounts for {…|pct0} of the decline in {spec.metric}".
- **Derived values** come from slots holding a whitelisted expression over evidence references: `diff`, `ratio`, `pct_change` and `share`. The renderer evaluates the expression and saves the result as a `derive` evidence row. The model never does the arithmetic.
- **Strength is computed by code, never chosen by the model** (H5):

| Label | When |
|---|---|
| **Measured** | exact query results, or derived values from them |
| **Beyond normal variation** | BH-adjusted q < 0.05 from `baseline_compare`, with its gates passed |
| **Within normal variation** | adjusted q ≥ 0.05, stated with the MDE ("a change smaller than ±8% wouldn't be detectable here") |
| **Not enough history** | `baseline_compare` ran but was gated (`not_estimable`); only Measured numbers are shown for it |
| **Untested** | a hypothesis that was planned but never executed |
| **Exploratory** | a flag that can appear on any of the above |

- **VERIFY** is deterministic. It checks that:
  - every slot resolves
  - the template text has no digits, except dates from an allowlist built from the QuestionSpec
  - direction words match the sign of the value they refer to
  - **coverage:**
    - Every segment with adjusted q < 0.05 is mentioned.
    - When the outcome is `no_meaningful_change`, the report states that, and no claim calls a segment a driver. Segments can still appear as Measured contributions, for example in the decomposition table.
    - A **driver claim** is a claim about a segment whose strength is *Beyond normal variation*. The eval counts only these.
  - there are no URLs, Markdown images or links
  - the word "significant" doesn't appear
  - causal wording doesn't appear (checked against a denylist whose accuracy we measure, ML16)
- **Failure path:** one repair attempt. Claims that still fail are moved to an appendix and are never shown as verified. Report-level failures lead to the terminal states in §5.4.
- **result_hash** is computed on the canonical result: rows sorted, floats rounded to 10 significant digits, types explicit. A replay matches if it agrees within a tolerance (H14, **[S3]**).

## 8. Frontend

**[D3]** The recommended stack is React, TypeScript, Vite, Tailwind and shadcn/ui, built to static files and served by FastAPI. Charts use Recharts with typed data from specs the server builds. The model never writes chart specs (H8).

Order of work:
1. **Replay view** of a stored investigation, built around a scripted first two minutes (H22):
   - **0–10 s:** a three-sentence answer.
   - **10–40 s:** claims with strength badges; clicking a number opens the evidence drawer.
   - **40–90 s:** a "How Cerno got here" timeline, including a hypothesis that turned out to be within normal variation.
   - **90–120 s:** a "run it live" button (needs an invite code), the eval results, and the repo link.
2. **Live run view:**
   - the plan as a checklist
   - streamed steps with their SQL and results
   - a heartbeat status line with elapsed time and cost
   - a cancel button
3. **QuestionSpec chips.**
4. **Upload** (milestone M8).

Hardening rules for every view:
- All model and data strings render as plain text. `dangerouslySetInnerHTML` is banned by a lint rule.
- Chart rows are `Map`s.
- A strict CSP with no inline scripts.
- Every async view has explicit loading, empty and error states, and meets WCAG AA.

## 9. Security summary

| Threat | Control (enforced in code or infrastructure) |
|---|---|
| Sandbox escape or secret theft via SQL or file parsing | Separate worker uid, empty env, rlimits, network namespace [S1], SIGKILL; locked DuckDB config; compiled SQL only |
| Disk or CPU exhaustion | temp-dir cap, rlimits, byte caps, timeouts, global concurrency limits |
| Exfiltration through rendered output | plain-text rendering; no model-written chart specs; VERIFY rejects URLs; CSP |
| Report steering via injected data | filters come only from the spec or metric definitions; truncated, stripped strings; instruction-like values flagged |
| Cross-tenant access | `get_owned()` everywhere; cross-owner route test; UUIDs |
| Denial of wallet | budgets reserved per run, per invite code and globally; per-IP rate limits; separate Anthropic spend limits per environment |
| Data retention | 30-day expiry for uploads; nightly deletion job; cascading deletes; values redacted from logs; privacy notice naming every processor |

The full test list is in `EVALUATION.md` → "Security tests" (13 groups).

## 10. Observability

- **Traces.** The event log and `llm_calls` are the trace. `llm_calls` records the model actually used, tokens by type, cost, latency and stage.
- **Logs.** Structured JSON logs record error classes and SQL hashes, never data values (ML5).
- **Debugging.** A dev-only debug view shows the prompts for each stage.
- **OpenTelemetry** is deferred until there's a backend to send traces to.

## 11. Deployment

- **Host:** one Fly.io machine (1 GB RAM, 3–5 GB volume, auto-stop off), about $7–12 a month. Railway Hobby is the fallback. **[S4]**
- **Container:** one container holds the API, the workers and the built SPA. HTTPS comes from the platform domain. One origin, so no custom domain is needed.
- **CI:** GitHub Actions on Linux runs:
  - ruff, mypy and pytest (including the per-PR calibration seeds and the security suite)
  - the TypeScript type check
  - the stale-client check
  - gitleaks
  
  A separate workflow runs the nightly calibration. Live-model evals are run by hand.

## 12. Evaluation

See `EVALUATION.md`.
