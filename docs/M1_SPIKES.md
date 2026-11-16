# M1 — Spikes

The pass/fail criteria below were fixed on 2026-11-15, before any spike code was written. If a spike fails, we investigate and propose alternatives; the criteria don't get loosened after the fact. Results are added under each spike as they come in.

Spike code lives in `spikes/<name>/`. It's throwaway, but it's tested enough that the results can be trusted. Every number reported here comes from a run, along with the command that produced it.

S4 (hosting) is deferred to before M5 (decided 2026-11-15). S2b re-measures cost on the M2 skeleton.

## S1 — Worker sandbox (Linux / WSL2)

**Question:** do the supervisor and worker design in ARCHITECTURE §4.1–4.2 actually contain hostile SQL?

**Setup:**
- A root supervisor spawns one worker. The worker calls `unshare(CLONE_NEWNET)`, applies rlimits, clears its environment, then drops to uid `cerno-worker`.
- A stub "API" process runs as `cerno-api`, with a fake secret in its env and a 0600 stub DB file.
- The worker opens a hand-built DuckDB file with the locked config.

**Pass criteria. Every item must hold; a single failure fails the spike:**
1. Every attack in security group 1 (EVALUATION.md) is blocked with an error or killed. That includes file reads, string-literal tables, ATTACH, COPY, INSTALL/LOAD, SET/RESET after the lock, `query()`, secrets functions, multi-statement strings, and an outbound TCP connect from DuckDB and from Python inside the worker.
2. Group 2:
   - the worker's `os.environ` is empty
   - its uid is `cerno-worker`
   - it can't read `/proc/<api-pid>/environ`
   - it can't read the stub DB
3. Group 3: the resource attacks (recursive CTE, cross join with ORDER BY that spills, huge `repeat`/`range`, `string_agg` over a cross join) are each killed within 10 s plus a 2 s grace period. After each attack:
   - the supervisor respawns the worker within 2 s
   - the next normal query succeeds
   - free disk on the data path changes by less than 1 MB
   - no temp files are left behind
4. Group 4: a result larger than the byte cap is truncated and marked as truncated.

Also recorded, but not part of pass/fail: how fast `interrupt()` stops each attack, whether spill works on read-only files, and whether opening the same file with two different configs in one process conflicts.

## S3 — Result reproducibility

**Question:** is the canonical result hash (ARCHITECTURE §7) stable?

**Setup:** a generated dataset of at least 1M rows, and six query shapes:
1. GROUP BY with SUM over DOUBLE
2. the same over DECIMAL(18,2)
3. AVG
4. ORDER BY with ties plus LIMIT
5. COUNT DISTINCT
6. a ratio of sums

**Pass criteria, all under the production config (`threads=1`):**
1. The canonical hash is identical across 50 runs for all six shapes.
2. The canonical hash is identical after rebuilding the DuckDB file from the same Parquet.
3. The canonical hash is identical across two separate processes.

Also recorded: whether raw and canonical hashes vary at `threads=4`. If canonical hashes are stable at 4 threads too, `threads=1` could be relaxed later.

## S7 — Statistical calibration of `baseline_compare` + BH

**Question:** is the prediction-interval method plus BH honest on data that behaves like real business data?

**Generator (spike version):**
- 4 regions × 3 channels = 12 cells; segments are regions, channels and the total.
- Daily orders follow a gamma–Poisson (negative binomial) model, which captures customer heterogeneity in aggregate.
- Order values are lognormal. Daily sums use a normal approximation of the sum of n lognormals.
- Weekly seasonality, AR(1) daily shocks, and monthly shocks: one shared across all cells plus one per cell.
- History length is drawn uniformly from 13 to 24 months per seed.
- The metric is revenue per day for each month.

**Tests per seed:** the total plus 4 regions plus 3 channels, for m = 8 p-values. BH is applied at α = 0.05 across all of them.

**Scenarios:**

| Scenario | Seeds | What's planted |
|---|---|---|
| `null_flat` | 1,000 | nothing |
| `null_trend` | 1,000 | linear growth of 1–3% per month, varying by cell |
| `shift_d{1,2,3,4}` | 300 each | the final month of one region drops by d × that region's residual SD (estimated from null runs) |
| `seasonal_null` | 500 | an annual cycle with ±10% amplitude, no shift |

**Pass criteria:**
1. **False positives.** For both `null_flat` and `null_trend`, the rate of "any driver reported" (any q < 0.05) is ≤ 0.069. That's the upper end of the 99% binomial band around 0.05 at n = 1,000. Being more conservative than that is allowed.
2. **Coverage.** For both null scenarios, the 95% prediction interval for the total covers the observed value in [0.932, 0.968] of seeds.
3. **Power.** In `shift_d3`, the planted region is flagged in ≥ 80% of seeds.
4. **Precision in the planted dimension.** In every `shift_d*` scenario, each non-planted region is flagged in ≤ 5% of seeds. Channels and the total really do change when one region drops, so they don't count as false positives here.
5. **Formula check.** On 100 random series, the prediction SE and p-value match statsmodels' `OLS.get_prediction(...)` for a new observation to within 1e-9.

**Reported, not gated:**
- the power curve for d = 1–4
- the `seasonal_null` any-driver rate, which we expect to exceed α. That's the known limitation in ARCHITECTURE §6, and this measures how bad it is.

## S6 — Claim templates and VERIFY

**Question:** can the model write slot-template claims that pass a deterministic VERIFY, without being pushed into naming drivers on null data?

**Setup:**
- Hand-written evidence JSON for 3 scenarios:
  - (a) a single regional driver, with q < 0.05
  - (b) offsetting movements plus an AOV mix shift
  - (c) null: no q < 0.05
- A minimal slot renderer and VERIFY that follow ARCHITECTURE §7. Only one repair attempt is allowed.
- SYNTHESIZE runs on `claude-opus-5-5` at effort `medium` with structured outputs.
- 20 reports: 7 for (a), 7 for (b), 6 for (c).

**Pass criteria:**
1. At least 18 of 20 reports reach `completed`, meaning VERIFY passes with at most one repair.
2. The null reports contain zero driver claims.
3. Every completed report for scenario (a) covers the planted driver.
4. No digits appear in template text outside the allowlist. VERIFY enforces this, but the rate before VERIFY is recorded.

**Also recorded:**
- invalid-reference rate before repair
- repair rate
- cost and latency per call
- two rendered reports, included here so you can judge readability yourself
- for comparison, a naive regex number check run on the same reports, with its false-pass and false-fail rates

## S2 — Real LLM cost, latency, schema, cache

**Question:** what does each LLM stage actually cost and how long does it take, and does the planned structured-output design work?

**Setup:** `claude-opus-5-5`, with a hard spend guard in the harness. S2 and S6 together stop at $12, counted from the real `usage` fields.

**Pass criteria:**
1. **RESOLVE** at `low` effort, on 30 hand-written questions (relative dates, partial months, ambiguous wording, unknown metrics): ≥ 27 of 30 are exactly correct on metric and windows, or correctly report `unknown_metric`.
2. **Schema:** the PLAN step-union schema with about 30 enum values is accepted, and 10 of 10 calls parse into valid steps.
3. **Cost.** The projected cost per investigation must come in at p50 ≤ $0.50 and p90 ≤ $0.75.
   - Projection: PLAN + 2 × ASSESS + SYNTHESIZE + (repair rate from S6 × SYNTHESIZE).
   - Each stage is measured on hand-written context at the effort ARCHITECTURE §5.2 specifies.
4. **Latency.** The projected p50 for LLM time per investigation is ≤ 150 s, which leaves room inside the 3-minute target.

**Also recorded:** token counts by type for every stage, the dataset-context token count compared with the model's minimum cacheable length, and `cache_read_input_tokens` on repeated calls.
