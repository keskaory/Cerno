# Cerno — Architecture

Status: **v2.1 draft** (2026-11-16: `baseline_compare` on log levels, D11). v2.0 (2026-11-15). v1.1 came out of the six-reviewer architecture review (`reviews/2026-11-15-architecture-review.md`). v2.0 applies the **$0 / local-first requirement (D9)**:
- the LLM runs locally through Ollama behind a provider interface (§5.2)
- there is no hosted service (§11)
- budgets are measured in compute, not dollars (§5.3)
- the public demo is a static replay showcase (§8)

Every other part of v1.1 is unchanged unless a section says otherwise. The review of v2.0 is in `reviews/2026-11-15-local-first-review.md`.

How to read the references in this doc:
- Review finding IDs are C# (critical), H# (high) and ML# (medium/low).
- Milestones are M0–M9.
- **[D#]** marks something that waits on a pending decision in `DECISIONS.md`.
- **[S#]** marks something that waits on a spike in `RISKS.md`.

## 1. Shape of the system

```
 Browser on the same machine (React SPA, served from the same origin)
     │  http://127.0.0.1:<port>, JSON + SSE, per-launch session token
     ▼
┌──────────────────────── your machine (Linux / WSL2) ───────────────────────────────┐
│ supervisor (root, ~100 LOC): starts API, spawns/kills/respawns workers             │
│ API process (FastAPI, asyncio), uid cerno-api  Worker processes (pool of 2),       │
│  routes · run orchestrator · event bus ─pipe─▶ uid cerno-worker                    │
│  LLM provider interface                        DuckDB + pandas + SciPy/statsmodels  │
│  never imports duckdb                          empty env · rlimits · no network     │
│        │                                       one dataset file, read-only         │
│        ▼                                              ▼                            │
│  SQLite app DB (WAL), mode 0600                data/datasets/<version>.duckdb      │
└────────┬───────────────────────────────────────────────────────────────────────────┘
         │ HTTP to one configured base URL (default localhost)
         ▼
   Ollama server (local, GPU) ── or ── ScriptedProvider (tests, replays): no network

 Static showcase (GitHub Pages): the same SPA built in replay-only mode with exported
 investigation JSON. No backend and no model calls.
```

Everything runs on one machine, with one API process and a small worker pool. Nothing leaves the machine: the data, the prompts and the model all stay local. That privacy is a real product property, not just a way to keep costs down. If Cerno is ever hosted, the escape routes in §2.6 still apply.

## 2. Backend service

### 2.1 API (FastAPI + Pydantic v2)

| Method | Path | Purpose |
|---|---|---|
| POST | `/session` | exchange the per-launch token (printed by `cerno serve`) for an HttpOnly session cookie |
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
- **Concurrency.** One investigation runs at a time; a second request gets a 429. There is one GPU, so LLM calls are serialized through a single-slot queue (H9, v2.0).
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
- **Backups:** `cerno backup` runs `VACUUM INTO` to a file. Bundled datasets can always be rebuilt from their generator seed.
- **Tables:** `sessions`, `datasets`, `dataset_versions`, `tables`, `columns`, `dimensions`, `metrics`, `relationships`, `investigations`, `question_specs`, `steps`, `evidence`, `claims`, `events`, `llm_calls`.
- **Ownership and deletes:** every user-owned table has an `owner_id`. Deletes cascade from `dataset_versions` and from `investigations` (ML5).

### 2.5 Local access control (v2.0, replaces invite codes)

Even a server that only listens on localhost can be reached by any web page open in your browser, through CSRF or DNS rebinding. So:
- The server binds to `127.0.0.1` only. Binding to any other address needs an explicit flag, and the server prints a warning.
- `cerno serve` prints a random per-launch token, the way Jupyter does. `POST /session` exchanges it for a cookie (`HttpOnly; SameSite=Strict`). No credential ever goes in a URL after the first exchange (H10).
- A `Host` header allowlist (`127.0.0.1:<port>`, `localhost:<port>`) blocks DNS rebinding.
- POSTs must have `Content-Type: application/json` (`text/plain` is rejected; it's the classic way around a JSON-only rule) and an `Origin` that exactly matches the server's own origin. `SameSite` alone isn't enough, because other apps on localhost count as same-site (review, low).
- **Ownership:** `owner_id` stays on every table, set to the single local principal. `get_owned()` stays as the one lookup helper, so adding real multi-user auth later doesn't touch the routes.

### 2.6 Escape routes we keep cheap

1. `owner_id` is on every table, so real auth only means replacing the local principal in one dependency.
2. The runner takes nothing but an ID and reads its state from the DB, so moving to a job queue only means swapping the notifier.
3. SQLAlchemy stays portable, so moving to Postgres is a connection string plus a data copy.

### 2.7 Configuration and secrets

- Settings come from environment variables via `pydantic-settings`. `.env.example` lists all of them.
- There are no paid API keys. The only setting that touches the network is `CERNO_LLM_BASE_URL`, which defaults to the local Ollama server. It's parsed strictly: the scheme must be `http`, the host must be exactly `localhost`, `127.0.0.1` or `::1` (or the one WSL gateway IP computed at startup; see §11), with no userinfo and no redirects followed. Anything else stops startup unless `CERNO_ALLOW_REMOTE_LLM=1` is set. The server prints a warning when that override is on, and runs made with it are stamped and excluded from eval and showcase exports.
- **Local base URL doesn't mean local model.** Ollama can serve `*-cloud` models from localhost while proxying them to ollama.com, which is paid and sends the data off the machine. So at startup `OllamaProvider` calls `/api/show` and refuses any model whose `remote_host` is set or whose tag ends in `-cloud`. It also checks that the digest matches the pinned one (v2.0, review H1).
- gitleaks runs in CI.
- The session token and the app DB stay with the API process, mode 0600 and owned by `cerno-api`. Workers run as a different uid (§4.1).

## 3. Data layer

### 3.1 Datasets, versions, retention

- **Versions are immutable DuckDB files.** A file is written once: it's built at `tmp/<uuid>.duckdb`, closed, moved into place with `os.replace()`, and from then on only opened read-only.
- **A version's content hash is computed over its logical content**, meaning each table's schema plus the canonical hash of its rows, **not over the file's bytes**. S3 found that two DuckDB files built from the same Parquet return identical query results but aren't byte-identical.
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

`compile(metric, dimensions, filters, window) → sqlglot.Expression`. It uses SQLGlot's expression builder and quotes every identifier. The output has to stay readable, because people see it in the evidence drawer. Every query gets a total `ORDER BY`: every sort ends with the grouping keys, so no two rows tie. Without that, LIMIT can return different rows on different runs, which S3 saw at `threads=4`. Golden tests compare both the compiled SQL and its results against hand-written reference SQL.

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
  
  **[S1]** We still need to verify that `unshare` and the uid switch work under WSL2, which is now the only host. If `unshare` isn't allowed, the network control falls back to DuckDB's `enable_external_access=false` plus the fact that the worker contains no code that touches the network. That fallback will be documented as a residual risk.
- **Messages.** Requests are typed: `RunQuery(version_id, sql, limits)` and `RunTool(version_id, tool, params)`. The worker resolves `version_id` to a path itself. Paths never come from the client or the model.
- **Results** come back over the pipe as Arrow or JSON, with a byte cap per cell and in total.
- **Timeouts** escalate in steps: the API sends a cancel message (which makes the worker call `interrupt()`), then after a grace period the supervisor sends **SIGKILL**, then respawns the worker (ML4).
- **Stats.** Statistics tools also run in the worker.

### 4.2 DuckDB configuration

Everything except two settings goes into `duckdb.connect(path, read_only=True, config={...})` (ML7):

```
enable_external_access=false   autoload_known_extensions=false
autoinstall_known_extensions=false   allow_community_extensions=false
allow_persistent_secrets=false   memory_limit=<below RLIMIT_AS>   threads=1
max_temp_directory_size=512MB   temp_directory=<worker tmp>   max_expression_depth=<cap>
```

Then, before any query runs: `SET TimeZone='UTC'` followed by `SET lock_configuration=true`.

**Why the change (measured in S3, DuckDB 2.0.0):** `TimeZone` is a setting of the built-in ICU extension. DuckDB rejects it in `connect(config=...)` when autoloading or external access is off ("options were not recognized"). Setting it right after connect works. Once the lock is set, every later `SET`/`RESET`, including `TimeZone`, `threads` and `enable_external_access`, fails. The worker opens the connection itself, so no SQL can run between connect and lock.

`threads=1` keeps float sums deterministic and costs little at our data sizes. S3 measured this on 2M rows (DuckDB 2.0.0): at `threads=1`, raw and canonical hashes were identical across 50 runs. At `threads=4`, raw hashes changed between runs for 5 of 7 query shapes, while canonical hashes stayed stable, except for ORDER BY with ties plus LIMIT, which returned a different set of rows on every run. **[S1]** will verify the whole config, including spill behavior on read-only files.

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
question ─▶ RESOLVE (LLM) ─▶ QuestionSpec ─▶ user confirms/edits ─▶ run:

  PLAN (LLM: playbook params + dimensions) ─▶ EXECUTE (code, worker)
        ▲                                         │
        └── ASSESS (LLM: add ≤3 steps or stop) ◀──┘   at most 2 ASSESS rounds
  ─▶ MULTIPLICITY (code) ─▶ SYNTHESIZE (LLM → slot claims) ─▶ VERIFY (code)
  ─▶ [one repair: SYNTHESIZE + VERIFY] ─▶ RENDER (code) ─▶ terminal state (§5.4)
```

- **QuestionSpec (ML1):** `{question_type, metric, comparison_window, baseline_window, dimensions_in_scope, filters}`.
  - **The model names periods, and code does the date arithmetic.** RESOLVE transcribes each period as one variant of a union:
    - `named_month`: a month, plus a year only if the question gives one
    - `relative`: `months_ago`
    - `previous_month`: the default baseline
    - `unsupported`: quarters, weeks, years and so on, which the chips form then asks about

    Code resolves the transcription against the **latest complete month in the dataset**, not the calendar: "last month" on Olist data means 2018-08, whatever today's date is. A month with no year means the most recent such month. Then `period_check` runs.
    - **Evidence so far:** asking for half-open windows directly scored 2–8/12 on the probe. A first transcribe schema that let several fields be filled at once scored 4/12 on `gemma3:4b`. For the union version, see the journal (2026-11-15). S2L decides between transcribe and months with fresh questions.
  - **No-LLM path.** The QuestionSpec chips are a complete form on their own: metric, months and dimensions are dropdowns. Free-text RESOLVE is a convenience on top of the form, not a requirement.
  - The user sees the spec as editable chips. The run never blocks waiting for input.
  - The eval harness accepts RESOLVE's spec as is, including a day-aligned alternative when there's a partial period, and scores the spec as one more output.
- **Playbook (H3).** v1 has one, for period changes. It runs:
  1. `describe_change` on the total
  2. `baseline_compare` on the total
  3. `decompose_change` over each dimension in scope
  4. `baseline_compare` for every segment of each dimension in scope (these p-values feed MULTIPLICITY)
  5. a mix/rate split for ratio metrics
  6. data-quality caveats
  
  **The playbook is fixed and confirmatory.** It always runs over every dimension in the confirmed QuestionSpec, in both modes. PLAN cannot drop a dimension. It only:
  - orders the dimensions
  - picks the crosstab pair
  - proposes **exploratory** extra steps, with reasons

  So a weak model can't lose recall by pruning, and the confirmatory evidence is the same in Full and Guided mode (review H4).
- **ASSESS** can add typed steps (`add_step` with a reason, at most 3 per round, at most 2 rounds) or stop. Added steps, and PLAN's proposed extras, are flagged **exploratory** (H4). They get their own BH family (§6), and code rejects duplicates.
- **A hypothesis** is a planned or added step together with its stated reason. One that's planned but never executed is reported as **Untested**. One whose result is within normal variation shows in the timeline as "checked: within normal variation".
- **Stopping** is deterministic. Once the playbook steps are done, the run moves to MULTIPLICITY when any of these holds:
  - ASSESS returns `stop`
  - both ASSESS rounds are used
  - the budget reserve has been reached
  
  We dropped the earlier share-based rule. Shares can be negative, can exceed 100%, and are suppressed when movements offset, so they don't work as a stopping signal.

### 5.2 How the model is called (H2)

- **The model never calls tools.** PLAN and ASSESS return typed step lists as structured output. The output is a discriminated union of step types, with enums for metric and dimension names. Code validates and executes the steps. The design was right for a frontier model, and it matters even more for a small local one: small models are far worse at multi-turn tool loops than at filling one constrained JSON object.
- **Every LLM call is single-turn** and built from state, with no history replayed.
- **Provider interface (v2.0, ADR-013).** Stages depend on one small protocol, never on a vendor SDK:

  ```python
  class LLMProvider(Protocol):
      def generate(self, req: StageRequest[T]) -> StageResult[T]: ...
  # StageRequest: stage, system, user, output model (Pydantic class), max_output_tokens, timeout_s
  # StageResult:  parsed object | failure kind (invalid_json, schema_violation, timeout,
  #               truncated, refusal), raw text, input/output tokens, latency, model id + digest
  ```

  v1 ships two implementations:
  - **`OllamaProvider`.** Calls `POST /api/chat` with:
    - `format=<JSON schema from the Pydantic model>`
    - `temperature=0` (greedy decoding, so the seed has no effect; it's recorded anyway)
    - `num_predict=max_output_tokens`
    - **one fixed `num_ctx` for every stage**, because changing it between stages forces a model reload

    If the counted prompt tokens plus `max_output_tokens` would exceed `num_ctx`, the call fails up front with a typed `context_overflow`, rather than letting Ollama silently drop the start of the prompt (review H3). The provider records the model **digest**, not just the tag, so an evaluation pins the exact weights.
  - **`ScriptedProvider`.** Returns hand-written or recorded outputs. It's used by every unit and state-machine test, by CI, and by replays.

  A cloud provider (Anthropic, or any OpenAI-compatible endpoint) would be a third class of about 50 lines. None is built or configured in v1, so there's no paid fallback to switch on by accident (D9).
- **Constrained decoding helps, but it isn't validation.** Ollama turns the JSON schema into a grammar, so the output always *parses*. The probe showed the grammar does **not** enforce `pattern`: a model returned `2025-10-31` against `^\d{4}-\d{2}-01$`. We don't know which other JSON-schema keywords it ignores. So every result goes through Pydantic validation plus semantic checks (enums exist, months fall inside the data range). On failure, the call is retried once with the validation error appended. A second failure ends the stage with a typed failure, and the terminal-state rules in §5.4 take over.
- **Keeping the job small enough for a small model.** Prompts carry short, canonical JSON context, and enums stay small (only the dimensions in scope). The model never does arithmetic, dates included, and never writes SQL. Ollama reuses the KV cache for an identical prompt prefix, so the prompt order stays fixed: system, then dataset context, then evidence, then the instruction. Nothing depends on that cache, though.
- **Model choice is measured, not assumed (spike S2L, D4).** Candidates must fit the dev machine: RTX 4050 Laptop with 6 GB VRAM, 15 GB RAM, Ryzen 7 7445HS. One model is pinned by digest for all stages. A different model per stage is allowed only if S2L shows it's needed.
- **Thinking.** A model with a thinking mode (e.g. Qwen3) runs with thinking off by default, because thinking tokens multiply latency on this hardware. S2L measures whether turning it on improves PLAN or SYNTHESIZE enough to pay for the time. S2L also measures with realistic prompt sizes (several thousand tokens for PLAN and SYNTHESIZE, not the probe's ~300), because prompt processing and a larger KV cache change both speed and VRAM fit.
- **Untrusted strings** (column names and values) are cut to 64 characters and stripped of control and markup characters before they go into a prompt (H8).

### 5.3 Budgets (H1, revised for v2.0)

Money is no longer the constraint; **time on one GPU** is. The H1 reservation mechanism stays, but its unit changes from dollars to **LLM seconds and output tokens**.
- **Reservation.** Before each call, the stage's worst case (`max_output_tokens` ÷ measured decode speed, plus prompt time) is reserved against the run's LLM-time budget. Afterwards it's settled to the measured latency. A reserve big enough for SYNTHESIZE plus one repair is held back from the start, so a run that's cut short still produces a report.
- **Per-stage `max_output_tokens`** (Ollama `num_predict`), set from S2L measurements.
- **Run limits:**
  - at most 5 LLM calls per run (PLAN 1, ASSESS ≤ 2, SYNTHESIZE 1, repair ≤ 1), plus 1 RESOLVE before the run
  - 40 executed steps (per-segment baseline checks count as one step for each dimension)
  - an LLM-time budget and a total wall-clock cap, both set from S2L. Provisional targets: LLM time p50 ≤ 180 s and p90 ≤ 300 s on the dev machine
  - 3 consecutive worker errors
- **Dollar cost** is recorded as $0.00 so the `llm_calls` schema stays provider-neutral. If a paid provider is ever added, it brings its own dollar reservation back (D9).
- **Invite-code and global daily budgets** are removed, because there's no public live service.

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

### 5.5 Modes: what works without a capable model (v2.0)

The LLM is never on the path to a number, so Cerno has an honest fallback if no local model passes S2L, or if Ollama isn't running:

| Mode | Question input | Plan | Report text | When |
|---|---|---|---|---|
| **Full** | free text → RESOLVE → editable chips | PLAN + ASSESS choose dimensions and extra steps | SYNTHESIZE slot claims, VERIFY | a model passed S2L |
| **Guided** | chips only (form) | the full playbook over every dimension (the M3 no-LLM baseline) | **code templates**: fixed sentences per evidence type, filled by the same slot renderer and checked by the same VERIFY | no passing model, Ollama down, or `--no-llm` |

For the same confirmed QuestionSpec, Guided and Full mode produce **identical confirmatory evidence and q-values**, because both run the full playbook and the same confirmatory BH family (§6). Full mode can add labeled exploratory steps on top. Guided mode loses natural-language input, judgment about extra checks worth running, and readable prose. The UI shows a mode badge, and the README says plainly what each mode can do. If S2L fails, v1 ships Guided as the default and Full as experimental; it doesn't pretend.

## 6. Statistics (deterministic, in the worker)

Every inferential output includes:
- the effect in metric units, with an interval
- the p-value and the adjusted q-value
- `k`, the number of historical periods used
- the MDE at 80% power, on every `baseline_compare` result (D12); an inconclusive outcome states it in words
- the `exploratory` flag
- `interpretation_limits`

Seeds are fixed and recorded.

| Tool | Method |
|---|---|
| `describe_change` | Exact Δ and Δ%, per-day values, and `period_check` flags. No inference. |
| `baseline_compare` (C3, revised by D11) | **One-step-ahead prediction interval on log levels, not on differences.** Fit OLS `log(level_per_day) ~ a + b·t` on the k ≥ 12 periods before the comparison period (the baseline period is the last of them). Predict the comparison period. The test statistic is `(observed − predicted) / SE_pred`, with `SE_pred = s·√(1 + 1/k + (t₀ − t̄)² / Sₜₜ)` and a t-distribution with **k − 2** df. All of this is on the log scale. Reports the unexpected change in metric units (observed − exp(predicted)) and as a percentage (exp(observed − predicted) − 1), with the prediction interval transformed back to metric units (so it's asymmetric), the p-value, the descriptive change from the baseline period, and the empirical percentile of past residuals (display only). Working on levels rather than differences sidesteps the MA(1) correlation that consecutive differences carry. Logs make noise that scales with the level roughly constant, which S7 showed is what breaks the test on raw levels in growing data. "Periods" are calendar months. For a day-aligned month-to-date comparison, each period is the first *d* days of its month. Gates: k < 12 → `not_estimable`; a segment that is zero in any of the k periods, is zero in the comparison period (log undefined), or averages under 30 orders per period → `not_estimable` for that segment. A metric that can be negative gets descriptive output only. Runs on the total and on each segment. |
| `decompose_change` (H6) | **Additive metrics:** segment contributions plus gross positive and negative movement. **Ratio metrics:** symmetric Kitagawa (midpoint weights) into rate and mix effects, with no residual. Both add an entry/exit bucket for segments that appear or disappear. Share of change is suppressed when gross/|net| > 3; "offsetting movements" is reported instead. Flags a Simpson sign reversal and asserts that contributions sum to the total. Works on one dimension at a time. If the top segment in each of two dimensions has a share above 50%, a 2-way crosstab is added. Descriptive only, so it produces no p-values. |
| **MULTIPLICITY** (H4, v2.0) | **Two families.** The *confirmatory* family is BH at α = 0.05 over the playbook's p-values: the total plus every segment `baseline_compare` across the confirmed dimensions. It's identical in both modes, and only it can earn "Beyond normal variation" and the outcome label. The *exploratory* family is BH over p-values from PLAN or ASSESS extras. Those claims always carry the exploratory flag and never change the outcome. Within the confirmatory family: Segments from different dimensions overlap and share period shocks, so BH's dependence assumption isn't guaranteed. The calibration suite measures the "any driver" false-positive rate under exactly that structure. If it lands outside the band, we switch to BY. A segment picked out by `decompose_change` never gets its own fresh test; its inference is its `baseline_compare` row, corrected along with the rest. **MDE** is reported at the conservative Bonferroni threshold α/m: the smallest unexpected change that test could have flagged. |

**Deferred from v1:** `compare_groups` and `scan_segments` (cluster bootstrap and score tests at the analysis-unit level; their design is in the review log under C4 and ML12–ML13), anomaly detection, cohort retention, and decomposition across more than one factor at a time (ML10, ML11).

**Known limits:**
- With 12–23 monthly points, prediction intervals are wide. That's honest, and the report says so.
- A linear trend on log levels handles compounding growth, but not steep *linear* growth or decline (≥ 5%/month), which curves in log space. S7 extended measured this: false positives 0.067–0.27 there. It also can't model annual seasonality, which is what Olist-style data has. The calibration suite includes an annual-seasonality scenario, so this failure gets *measured and reported* rather than assumed away. Seasonal adjustment needs at least 2–3 years of history and is deferred.
- Residual autocorrelation between months would make the intervals too narrow. The generator includes per-period shocks, and calibration checks for this.
- Calibration on our generator doesn't guarantee calibration on real data.
- Power is limited. With 12–23 monthly points and about 8 confirmatory tests, a region must fall about 28% (about 38% with ±10% seasonality) to be flagged 80% of the time. The report shows the MDE so a "no meaningful change" outcome isn't over-read.

**Calibration suite (H18):**
- The data generator includes customer heterogeneity (gamma–Poisson order frequency, lognormal order values), weekly seasonality, AR(1) daily shocks, and a shared shock per period.
- History length is drawn from 13–24 months per seed, so both short and long histories get calibrated.
- `baseline_compare` and the full BH pipeline (several dimensions with overlapping segments) run on null seeds. The "any driver" rate and prediction-interval coverage must fall inside a binomial band around their nominal values.
- An annual-seasonality scenario is reported but not asserted (see known limits).
- **Power (D12):** in `null_flat`-shaped data with a planted regional drop, empirical power at d = 2, 3, 4 and 5 residual SDs must be at least the power of an ideal single prediction-interval test at α/m for the same mix of k, minus 0.05. This gate replaces the original "80% at d = 3", which no test can reach with 12–23 points. It runs nightly and at milestone sign-off, not per PR.
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
   - **90–120 s:** the eval results, the repo link and "run it on your machine" instructions.
2. **Live run view (local app only):**
   - the plan as a checklist
   - streamed steps with their SQL and results
   - a heartbeat status line with elapsed time, the model and LLM seconds used
   - a cancel button
3. **QuestionSpec chips.**
4. **Upload** (milestone M8).

### 8.1 Static showcase (v2.0)

This is the public, $0 demo. `cerno export-showcase` writes each pinned demo investigation to one JSON file: spec, plan, steps, evidence with SQL and canonical previews, claims, report, timings and model digest. It serializes through an **allowlisted export schema** (a separate Pydantic model, never a dump of DB rows), and a test fails the export if any string looks like an absolute path (`C:\`, `/home/`), a hostname, a token, a raw prompt or a debug field (review M4). The repo is public, so Pages is free (checked 2026-11-15); a private repo would need a paid plan. The SPA builds in `replay-only` mode, reading those files instead of the API, and is published to GitHub Pages by a workflow.

What it shows:
- 3–5 real investigations from the local model, including a null run that correctly reports "no meaningful change"
- the evidence drawer for every number
- the eval table
- a short recorded video of a live local run

What it doesn't do:
- run anything
- accept questions

The page says so plainly. "Interactive" means you can explore the recorded evidence, not ask new questions.

Constraints on Pages:
- It's a static site of 1 GB max, and the exported JSON is a few MB.
- GitHub Pages can't set response headers, so the CSP goes in a `<meta http-equiv>` tag, and security group 13 tests the meta tag on the showcase build. `frame-ancestors` can't be set that way, which we accept: there's nothing on the page worth clickjacking.
- Exported reports contain only bundled, synthetic or Olist-derived data. The Olist subset carries its CC BY-NC-SA 4.0 notice.

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
| Other web pages driving the local server (CSRF, DNS rebinding) | bind to 127.0.0.1; `Host` allowlist; per-launch token → SameSite=Strict cookie; JSON-only POSTs (§2.5) |
| Data leaving the machine | the LLM base URL must be localhost unless explicitly overridden; workers have no network; no telemetry |
| Local resource exhaustion (GPU, disk) | one run at a time; LLM-time budget per run; temp caps; rlimits |
| Data retention | uploads stay on your disk; `cerno delete-dataset` cascades; values redacted from logs |

The full test list is in `EVALUATION.md` → "Security tests" (13 groups).

## 10. Observability

- **Traces.** The event log and `llm_calls` are the trace. `llm_calls` records the provider, model tag and digest, tokens in and out, latency, decode speed, stage, failure kind and retries. Cost is recorded as $0.00 for local providers.
- **Logs.** Structured JSON logs record error classes and SQL hashes, never data values (ML5).
- **Debugging.** A dev-only debug view shows the prompts for each stage.
- **OpenTelemetry** is deferred until there's a backend to send traces to.

## 11. Deployment

There is no hosted service (D9). Everything listed here is free; see the license table in `DECISIONS.md` → D9.
- **Local app.** Run `cerno serve` in WSL2 Ubuntu (D5). It starts the supervisor, the API, the workers and the built SPA. The supervisor needs root to switch uids, so it runs under `sudo`. `cerno serve --no-isolation` exists for machines without root. It prints a red warning, every run it makes is stamped `isolation=off`, and the eval and showcase exporters refuse stamped runs. Security groups 1–3 don't count as passed under it.
- **Ollama** runs on the Windows host so it can use the GPU, and **stays bound to `127.0.0.1`**. Never set `OLLAMA_HOST=0.0.0.0`: its API is unauthenticated and can pull, delete and run models.
  - **Required:** WSL2 **mirrored networking** (`networkingMode=mirrored` in `.wslconfig`, which needs Windows 11 22H2+). With it, WSL reaches Windows' `127.0.0.1` directly.
  - **Fallback for NAT-mode WSL:** bind Ollama to the WSL virtual interface's IP only, and add a Windows firewall rule scoped to that interface. `CERNO_LLM_BASE_URL` may then name exactly the computed gateway IP, never a general override.
  - **[S1]** verifies three things: the API reaches Ollama, a worker in its own network namespace can't reach anything (including Windows loopback under mirrored mode), and Ollama isn't reachable from the LAN (review H2).
- **Static showcase.** GitHub Pages, published from a workflow (§8.1).
- **Optional container.** A Dockerfile may come later for people who want one. Docker isn't required, and nothing depends on it.
- **CI:** GitHub Actions standard runners, free on public repos. It runs:
  - ruff, mypy and pytest, including the per-PR calibration seeds and the security suite. The LLM is always `ScriptedProvider` in CI, because CI has no GPU and no model.
  - the TypeScript type check
  - the stale-client check
  - gitleaks (free on a personal account; an organization would need a license key)
  
  A separate workflow runs the nightly calibration. Live-model evals run by hand on the dev machine.

## 12. Evaluation

See `EVALUATION.md`.
