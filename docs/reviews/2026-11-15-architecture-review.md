# Architecture Review — 2026-11-15

Six independent reviewer subagents (backend, AI engineering, data engineering, statistics, security, product engineering) reviewed the v0 planning docs. Each was told to look for weaknesses rather than confirm the design. The backend, data, security and AI reviewers checked claims against current documentation (DuckDB 2.0, SQLGlot, Fly/Render/Railway/Neon/Vercel pricing pages, the Claude API reference). The statistics reviewer cited the methods literature. The product reviewer did no web checks, and its list of comparable products is unverified.

This file merges overlapping findings, ranks them, and records how each was resolved. Reviewer tags: **BE** backend, **AI** AI engineering, **DE** data engineering, **ST** statistics, **SEC** security, **PE** product engineering.

Status values: **resolved** (changed in v1 docs), **spike** (resolution depends on a measurement), **needs decision** (your call), **deferred** (explicitly out of v1 scope).

## Critical

| ID | Finding | Raised by | Resolution | Status |
|---|---|---|---|---|
| C1 | Untrusted SQL, CSV/Parquet parsing and stats run in the API process. DuckDB's own docs say its settings aren't a complete sandbox. A past bypass (CVE-2024-41672) read `/proc/self/environ` with external access disabled. The work also blocks the asyncio loop and freezes every SSE stream. | SEC F1, BE F1, DE F12 | All DuckDB, ingest and stats work runs in a **worker subprocess** with an empty environment, rlimits, a per-worker temp dir, a byte-capped result pipe, and SIGKILL on timeout. The API process never imports DuckDB. ARCH §4. | resolved |
| C2 | VERIFY's "numbers in text match evidence" can't be built reliably: derived numbers, formatting, false matches against unrelated cells, wrong entity or direction. | AI F1, PE F9 | Claims are **templates with typed slots** that point at evidence fields. Derived numbers come from a deterministic `derive` op. Code renders every number. VERIFY checks slot resolution, no stray digits, direction words vs. sign, and driver coverage. ARCH §7. | resolved |
| C3 | No sampling model. Row-level tests comparing two periods of full-population data give p≈0 for almost any change ("tested" becomes theater). | ST F1 | Period-over-period changes are judged against a **historical baseline of same-length changes** (`baseline_compare`). If history is too short, the result is "not estimable". No row-level period tests. ARCH §6. | resolved |
| C4 | Wrong unit of analysis: orders cluster within customers and sessions within users, so naive standard errors are too small. | ST F2 | Metrics declare an `analysis_unit`. Tests aggregate to that unit (cluster bootstrap, BCa, or the delta method for ratios). n-gates count clusters. With no unit key, results are descriptive only. | deferred (follow-up) |
| C5 | The metric schema can't express ratio metrics (conversion, AOV) or distinct counts. Free-text filters reopen the freehand-SQL hole. | DE F1, DE F9 | Metric types `simple / ratio / derived`; structured filters; `entity_key`, `additive` and `analysis_unit` fields; a dimension registry; star-schema N:1 join paths only. ARCH §3.3. | resolved |
| C6 | The plan is built bottom-up even though RISKS claims vertical slices; nothing runs end to end until M5–M8. | PE F1 | Milestones re-ordered around a **walking skeleton** (M2) that goes from question to a rendered report. IMPLEMENTATION_PLAN. | resolved |

## High

| ID | Finding | Raised by | Resolution | Status |
|---|---|---|---|---|
| H1 | Budget numbers contradict each other: 300K+40K tokens ≈ $2.00, the cap is $0.75, the target is $0.50. About 15 Opus calls per run is probably several times over budget. Thinking tokens are billed as output. | AI F2, BE F11, PE F2 | Dollars are the only token-type cap. A **reservation before each call** (input + `max_tokens` × price). Per-stage `max_tokens`. A reserve held back for SYNTHESIZE. Target 4–6 LLM calls per run. Final numbers come from spike S2. | spike |
| H2 | It's unclear whether the model calls tools. Multi-turn tool use with prompts rebuilt each turn collides with Opus 5.5's preserved-thinking check and breaks caching. | AI F3, AI F7 | **Plan-as-data**: the model emits typed step lists through structured outputs, code executes them, and every LLM call is single-turn. Fixed cache layout (constant system prompt → dataset context → evidence ledger → stage instruction). | resolved |
| H3 | Open-ended hypotheses are too loose for well-known question types; REVISE and stopping are undefined. | AI F4 | **Playbooks** per question type are the primary path. ASSESS can add at most 3 typed steps. Stop rules are deterministic. A "no meaningful change" outcome exists. | resolved |
| H4 | Multiple testing is only corrected inside one scan. Adaptive revisions and tests on segments chosen after looking at the data are double dipping. | ST F4, AI F5 | **BH (or BY) over every p-value in the investigation**, applied at the MULTIPLICITY stage before SYNTHESIZE. Steps added in ASSESS are flagged *exploratory*. No fresh test on a segment chosen by decomposition. Strength is computed by code. | resolved |
| H5 | The claim taxonomy ranks evidence wrongly (exact measurements labeled weaker than "tested") and has no "no change" class. | ST F6, PE F9 | Labels: **Measured / Beyond normal variation / Within normal variation (with detectable size) / Untested**, plus an *exploratory* flag. "Significant" is banned in report text. p-values appear only in the evidence drawer. | resolved |
| H6 | Mix/rate decomposition is underspecified (order dependence, residual, segments that enter or exit, offsetting movements). | ST F3 | **Symmetric Kitagawa** (midpoint weights), an entry/exit bucket, gross +/− movement, share of change suppressed when movements offset, a Simpson sign-reversal flag. One dimension at a time. | resolved |
| H7 | Period comparability: partial periods, day counts, weekday mix. The most common false "decline". | ST F5, DE F6, AI F9 | `period_check` runs in QuestionSpec resolution. Half-open `[start, end)` intervals. A dataset timezone (UTC default). Partial periods block the comparison unless day-aligned. | resolved |
| H8 | The injection impact is understated: rendered Markdown, images or links can exfiltrate data, and model-authored chart specs can fetch URLs. | SEC F3, AI F8 | Model prose renders as plain text; no model-authored chart specs; VERIFY rejects URLs; a CSP; data-derived strings rendered as text; filters only from the QuestionSpec or metric definitions. | resolved |
| H9 | Denial of wallet: anonymous workspaces bypass per-workspace caps, and caps checked after the spend race each other. | SEC F4, BE F11 | **Per-person invite codes** (stored hashed) with their own budget and concurrency limit of 1; atomic reservation; a global concurrency semaphore; per-IP rate limits; separate Anthropic keys for dev, eval and prod. | resolved |
| H10 | Workspace auth and IDOR checks are unspecified; SSE pushes tokens into URLs. | SEC F5, BE F4 | One `get_owned()` lookup helper with a cross-tenant test over every route; UUIDs; **frontend served from the same origin as the API**, so an HttpOnly cookie works with EventSource; a public `is_public` flag for demos. | needs decision (tied to D3) |
| H11 | Hosting assumption wrong: Render free has no disks, Fly has no free tier, and a 512 MB box is too small. | BE F2 | One Fly machine (1 GB, 3–5 GB volume, auto-stop off), about $7–12/mo; Railway Hobby is the alternative. Spike S4. | spike |
| H12 | Deploys and auto-stop kill in-process runs; "resume" is underspecified. | BE F3 | Reconcile at startup (`running → interrupted`), handle SIGTERM, **re-run instead of resume** in v1, auto-stop off. | resolved |
| H13 | SSE proxy idle timeouts and event typing are missing. | BE F4, BE F5 | 15-second heartbeat, `seq` used as the event id with Last-Event-ID replay, in-process notify with DB backfill on reconnect, events as a Pydantic discriminated union exported to OpenAPI. | resolved |
| H14 | `result_hash` will flake: row order, parallel float sums, ties, time functions, versions, seeds. | DE F4, BE F7 | Canonicalize before hashing; `threads=1` on evidence queries; money cast to DECIMAL; `TimeZone=UTC`; ban nondeterministic functions; record engine versions and seeds; compare with tolerance. | spike |
| H15 | Key inference requires exact uniqueness, which breaks on the duplicate trap and on real exports. | DE F2 | Uniqueness ratio ≥ 0.99 means "key with violations"; the user confirms the grain key; data-quality checks run against confirmed keys. (Applies once upload ships.) | resolved |
| H16 | Static fan-out detection from the AST isn't feasible; chasm traps aren't covered. | DE F3 | Compiler allows N:1 star traversal only; separate facts are aggregated separately. **Freehand SQL removed from the MVP.** If it returns later, it uses a runtime grain check. | resolved |
| H17 | Ingestion trusts the sniffer (20K-row sample, cp1252, currency strings, leading zeros, DD/MM ties). | DE F5 | Two-pass load: everything read as VARCHAR first, then full-column TRY_CAST inference, user type review, then a typed CTAS. Encoding detection. (Applies once upload ships.) | resolved |
| H18 | The eval can't detect miscalibration (iid data generators), effects aren't calibrated to noise, there are too few null runs, and results have no confidence intervals. | ST F7, PE F4 | Realistic data generator (customer heterogeneity, seasonality, period shocks); **stats calibration suite in CI** (≥1,000 null seeds per tool); agent eval with seeds and Wilson CIs. | resolved |
| H19 | Two of three headline metrics hold by construction (VERIFY enforces them); the baseline is a straw man. | PE F3, PE F4, AI F11 | Measure the unsupported-claim rate **before VERIFY**; driver precision and recall; specificity on null scenarios; the **unconstrained ReAct agent** is the main baseline; paired comparisons. | resolved |
| H20 | Synthetic-only eval is circular; the traps test the profiler, not the agent. | PE F5 | Add a semi-synthetic set: real public data (Olist) with injected effects. Each trap is labeled with the layer it tests. | resolved |
| H21 | The MVP is far too large for one developer. | PE F6 | One domain (e-commerce), one question type (period change and its drivers), bundled data with YAML metrics; upload moved to M8. | resolved |
| H22 | The real first user is a reviewer, and replay is scheduled last. | PE F7 | The replay of a stored investigation is the first UI built; the first 2 minutes are scripted. | resolved |
| H23 | Postgres (Neon) isn't justified with one process and one volume. | BE F6, DE, PE F12 | SQLite (WAL) on the volume through portable SQLAlchemy and Alembic. | needs decision (D2) |
| H24 | Next.js on Vercel plus a separate API means cross-origin cookies, a 4.5 MB upload limit, and two deploys. | PE F12, BE F4, SEC F5 | Vite + React + TypeScript SPA served by FastAPI from one container. | needs decision (D3) |

## Medium / Low

| ID | Finding | Raised by | Resolution | Status |
|---|---|---|---|---|
| ML1 | Blocking clarification mid-run | PE F8, AI F6 | `POST /questions/resolve` returns an editable QuestionSpec before the run starts; it never blocks mid-run | resolved |
| ML2 | Spill to disk can fill the volume | SEC F2 | `temp_directory` per worker off the volume; `max_temp_directory_size` capped | resolved |
| ML3 | SQLGlot denylist is incomplete | SEC F7, DE F7 | Function allowlist; reject `Anonymous` nodes and string-literal tables; execute the regenerated SQL; metric filters parsed to ASTs. Only needed for compiled SQL in v1 | resolved |
| ML4 | CPU, bytes and concurrency aren't bounded | SEC F8 | Wrap queries in a LIMIT, cap bytes per cell and in total, hard-kill the worker, `max_expression_depth` | resolved |
| ML5 | Privacy wording contradicts the design | SEC F9 | Spec corrected ("up to 50 aggregated rows"); delete cascade; values redacted from logs; privacy notice | resolved |
| ML6 | XSS and prototype pollution | SEC F10 | No `dangerouslySetInnerHTML` (lint rule); `Map`s for chart rows; CSP | resolved |
| ML7 | Missing DuckDB config settings | SEC F11, DE F7 | Full config dict passed at connect (see ARCH §4.2) | resolved |
| ML8 | Secrets separation | SEC F12 | Separate keys and limits per environment; gitleaks in CI | resolved |
| ML9 | Upload limits and disk sizing | BE F10, SEC F6 | 50 MB, 10 tables, chunked streaming upload, magic-byte checks, Parquet footer pre-check, slugged table names | resolved |
| ML10 | Anomaly detection is fragile | ST F10 | Deferred out of v1 | deferred |
| ML11 | Cohort retention ignores censoring | ST F11 | Deferred out of v1 (Kaplan–Meier design noted) | deferred |
| ML12 | Minimum-n gates have no basis | ST F8 | Cluster-based gates: proportions ≥10 events and ≥10 non-events per group; means ≥ max(30, 355·skew²) clusters; baseline ≥ 8 periods (raised to k ≥ 12 by the follow-up) | deferred |
| ML13 | Test procedures are inconsistent | ST F9 | One procedure per tool (BCa cluster bootstrap; score test with Newcombe CI); Fisher dropped | deferred (follow-up) |
| ML14 | Recorded LLM fakes are brittle | PE F11, AI F10 | Pure `transition()` function, typed stage interfaces, hand-scripted fakes; a few recorded payloads only for parse and refusal paths | resolved |
| ML15 | Windows dev vs. Linux prod | BE F9 | WSL2 recommended; CI on Linux | needs decision (D5) |
| ML16 | Causal-word check is noisy and easy to evade | ST F12, AI F12 | Fixed decomposition phrasing; a measured denylist; LLM judge used only in eval | resolved |
| ML17 | Build vs. buy for the semantic layer not weighed | DE F10 | Hand-roll a narrow compiler, borrowing MetricFlow's metric taxonomy; ADR records the alternatives | resolved |
| ML18 | Dataset versioning contradicts retention | DE F11 | "Reproducible while the version exists"; demo versions pinned; metrics carried forward marked `needs_revalidation` | resolved |
| ML19 | Differentiation claim too broad | PE F12 | Narrowed to the combination: LLM-planned investigation, multiple-testing-aware deterministic stats, mechanically verified claims, published numbers against an unconstrained agent. Competitor list still to be checked | resolved |
| ML20 | Eval cost doesn't fit the budget | PE F2 | Dev subset of about 20 questions at ≤ $10 per run; full suite only at milestones; cheaper model for dev iterations is your call | needs decision (D4, D6) |

## Where reviewers disagreed

- **Claim labels.** PE proposed "Statistically supported", while ST argued against any wording built on significance. We went with ST's wording because the main inference method changed (C3), so "significance" isn't the right frame for most claims anymore.
- **Workspace tokens.** SEC designed bearer tokens with a fetch-based stream; PE proposed dropping anonymous workspaces. Both are satisfied by serving the frontend and API from one origin (cookie auth, EventSource works) and keeping invite codes as the only credential until upload ships.
- **Keeping Postgres.** BE noted that "Postgres on the résumé" is the only real loss. We recommend SQLite but left the choice open (D2).

## Follow-up: cross-document consistency check (2026-11-15)

After the v1 rewrite, one more independent agent checked every planning doc against the others. It found 5 blockers and 21 should-fix issues. All were fixed in the v1.1 docs. The ones that changed the design:

| Issue | Fix |
|---|---|
| Spikes S2, S5 and S6 needed code that M2–M4 build | S2 and S6 now run on hand-written evidence JSON. S2b re-measures cost on the M2 skeleton. S5 became the no-LLM baseline in M3. |
| The planted "conversion drop" had no matching metric, and the answer key used Kitagawa on additive revenue | The effect is now an order-rate drop. Keys use `additive_segment` (Kitagawa stays for AOV). |
| `baseline_compare` at k ≥ 8 can't produce a usable p-value for BH; the playbook produced nothing for MULTIPLICITY to correct; generator history unspecified | k ≥ 12 non-overlapping monthly changes, a z-score with a t(k−1) p-value, and linear detrending at ≥ 18. Runs on the total **and every segment**, and those p-values feed BH. The generator has 24 months of history. "Beyond normal variation" now means BH q < 0.05. |
| Public deploy (M5) came before the security suite (M6) | M5 acceptance now requires security groups 1–5, 7–10 and 13. |
| Eval budgets didn't add up | Dev subset is ~12 questions, Cerno only, ≤ $10. p50 target $0.50 under a $0.75 cap. Full-suite cost noted in D6. |
| `compare_groups` and `scan_segments` were calibrated but never built or used | Deferred from v1. C4, ML12 and ML13 now apply only when they come back. |
| Worker isolation left the worker able to read the API's secrets and the app DB | Separate uids (`cerno-api`, `cerno-worker`), secrets and DB at 0600, network namespace (verified in S1/S4). |
| Undefined: `derive`, terminal states, RESOLVE budget, shares/coverage, hypothesis entity, retention TTL, `/healthz`, `needs_revalidation` | All defined in ARCHITECTURE v1.1 (§2.1, §3.1, §3.3, §5.1, §5.3, §5.4, §7). |
| Review IDs "M#" clashed with milestone IDs | Review medium/low findings renamed to ML#. |

C4, ML12 and ML13 (cluster-level group comparisons) are now **deferred**: they were resolved on paper, but their tools aren't in v1.

## Second follow-up: verification pass (2026-11-15)

A final agent checked that the fixes landed. It found three more design issues, all fixed in ARCHITECTURE v1.1:

| Issue | Fix |
|---|---|
| VERIFY's coverage rule ("any segment with ≥ 50% share must be mentioned") would push null runs into naming a driver, against the ≥ 90% specificity target | Coverage now requires mentioning every segment with q < 0.05. A *driver claim* means strength Beyond normal variation. Null reports state `no_meaningful_change` and show segments only as Measured contributions. |
| "k ≥ 12 non-overlapping changes" left only 11 on 24 months of data. Consecutive differences are MA(1)-correlated, and z against t(k−1) ignored the SE and degrees-of-freedom corrections. | `baseline_compare` is now a one-step-ahead OLS prediction interval on monthly levels: SE includes √(1 + 1/k + leverage), df = k − 2. Calibration draws 13–24 months and adds an annual-seasonality scenario. MDE is taken at α/m. BH across dimensions is justified by calibration; BY is the fallback. |
| The API (unprivileged uid) couldn't respawn workers as a different uid or call `unshare` | A small root supervisor starts the API and spawns, kills and respawns workers. The API asks it over a control socket. Verified in S1/S4. |

It also fixed scheduling and wording: S1/S4 use stubs, M2 runs only the worker-side security tests, group 11 moved to M8, rate limits moved to M5, the M4 acceptance test is explicitly directional, the D6 arithmetic is corrected, a "Not enough history" label was added, and outcomes are defined for every status.
