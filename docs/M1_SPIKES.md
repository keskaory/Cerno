# M1 — Spikes

The pass/fail criteria below were fixed on 2026-11-15, before any spike code was written. If a spike fails, we investigate and propose alternatives; the criteria don't get loosened after the fact. Results are added under each spike as they come in.

Spike code lives in `spikes/<name>/`. It's throwaway, but it's tested enough that the results can be trusted. Every number reported here comes from a run, along with the command that produced it.

S4 (hosting) is dropped: under D9 there is no hosted service. S2 and S6 now run on **local models via Ollama** (D9). The S2 and S6 criteria below were rewritten on 2026-11-15, after D9 and before either spike ran. A 12-question probe ran first to shape the RESOLVE schema, so S2L uses a newly written question set (see S2L).

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

Also recorded, but not part of pass/fail:
- how fast `interrupt()` stops each attack
- whether spill works on read-only files
- whether opening the same file with two different configs in one process conflicts
- (v2.0) whether the API process in WSL2 can reach Ollama on the Windows host (mirrored networking vs. the host gateway address), while a worker in its network namespace cannot

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

### S3 results (2026-11-15): **PASS** on native Windows; WSL2 re-run pending

Command, from `backend/`: `uv run python ../spikes/s3_repro/run.py` (2,000,000 rows, 50 reps, DuckDB 2.0.0, Windows 11 native, Ryzen 7 7445HS). Report: `spikes/s3_repro/out/report.json`.

| Criterion | Result |
|---|---|
| 1. Identical canonical hash over 50 runs, all shapes, `threads=1` | pass (1 distinct hash per shape; raw hashes also stable) |
| 2. Identical after rebuilding the DB from the same Parquet | pass |
| 3. Identical across two separate processes | pass |

Distinct hashes over 50 runs at `threads=4`:

| Shape | canonical | raw |
|---|---|---|
| SUM over DOUBLE | 1 | 50 |
| SUM over DECIMAL(18,2) | 1 | 23 |
| AVG | 1 | 50 |
| ORDER BY with ties + LIMIT | **50** | 50 |
| ORDER BY total order + LIMIT | 1 | 1 |
| COUNT DISTINCT | 1 | 35 |
| ratio of sums | 1 | 31 |

Findings:
- **Rounding works.** Canonical rounding absorbs the float noise from parallel aggregation. Raw output at 4 threads varies on almost every run, even for DECIMAL sums, where only row order changed.
- **Ties with LIMIT break reproducibility, even within one engine version.** At 4 threads, the ties-plus-LIMIT query returned a different set of rows every time. That confirms the compiler rule that every ORDER BY must be total (ARCH §3.4).
- **File bytes aren't a stable hash.** Two DuckDB files built from the same Parquet are not byte-identical, so a version's content hash must be computed over the logical data (ARCH §3.1).
- **Config deviation:** `TimeZone` can't go in `connect(config=...)` when autoload or external access is off. It's set right after connect and then locked (ARCH §4.2).
- **Not yet done:** a re-run under WSL2 Ubuntu (D5) before M1 closes. The criteria stay the same.

Tests: `spikes/s3_repro/test_canon.py`, 8 tests on the canonical hash, all passing.

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

## S2L — Local model selection (replaces S2)

**Question:** which free local model, if any, runs well enough on the dev machine to drive RESOLVE, PLAN, ASSESS and SYNTHESIZE?

**Dev machine:**
- RTX 4050 Laptop, 6 GB VRAM
- 15 GB RAM
- Ryzen 7 7445HS
- Ollama 0.40.16

**Candidates:**
- Already installed: `gemma3:4b`, `gemma3:12b`, `qwen3:8b`.
- Other models get added only with your OK to download them.
- Each model is pinned by digest.
- Thinking is off unless stated. For Qwen3, thinking-on is measured separately on PLAN and SYNTHESIZE.

**Setup:**
- 30 **newly written** RESOLVE questions. The 12 probe questions shaped the schema, so they're excluded.
  - At least 8 are `unknown_metric` cases.
  - At least 4 use periods the schema should mark `unsupported`.
  - Both the transcribe and months schemas are scored, and the better one per model counts.
  - The questions, the expected answers and these criteria are **committed, with their SHA-256 in the journal, before any S2L call runs**.
  - You're invited to write some of the questions, so the schema designer isn't the only author (review M1).
- The PLAN step-union schema with about 30 enum values, on 5 hand-written dataset contexts.
- ASSESS on the same contexts with hand-written evidence.
- SYNTHESIZE comes from S6.
- `temperature=0` with a fixed seed. Every call goes through Pydantic validation, with at most one retry.

**Pass criteria (per model):**
1. **RESOLVE:**
   - at least 27 of 30 exactly right on metric and windows (after code resolution), or correctly reported as `unknown_metric` or `unsupported`
   - **and zero metric substitutions**, i.e. an unknown metric answered as a known one, which is the worst failure because it answers a different question

   The Wilson 95% interval is reported with the score. At n = 30, 27/30 has a lower bound of about 74%, so this gate catches a bad model; it doesn't certify a good one.
2. **Schema:** 10 of 10 PLAN calls produce a valid step list, allowing at most one retry each. ASSESS is held to the same bar. (PLAN can't prune the confirmatory playbook, ARCH §5.1, so its quality affects exploratory extras and ordering, not recall. The share of proposed extras that are sensible is recorded.)
3. **Latency (replaces cost).** Projected LLM time per investigation must come in at p50 ≤ 180 s and p90 ≤ 300 s.
   - Projection: PLAN + 2 × ASSESS + SYNTHESIZE + (S6 repair rate × SYNTHESIZE) + (measured retry rate × each stage).
   - It's measured with the model already loaded, with **realistic prompt sizes** (the evidence ledger included) and one fixed `num_ctx` large enough for the biggest prompt. Cold-load time is reported separately.
4. **S6 passes** on the same model.

**Selection** among the models that pass, in strict order:
1. more S6 reports `completed`
2. more RESOLVE answers correct
3. lower projected p50 latency
4. an Apache-2.0 license over custom terms **If no model passes**, v1 ships Guided mode as the default and Full mode as experimental (ARCH §5.5), and the README says why.

**Also recorded:**
- the share of the model held in VRAM, decode tokens per second, prompt-processing speed and cold-load time, at the chosen `num_ctx`
- the model digest, quantization, Ollama version and NVIDIA driver version
- tokens in and out per stage
- the cost of thinking-on, in time and in quality
- text-to-SQL execution accuracy on 20 questions. This only matters for the M7 ReAct baseline, so it's reported but not gated.

## S6 — Claim templates and VERIFY

**Question:** can the model write slot-template claims that pass a deterministic VERIFY, without being pushed into naming drivers on null data?

**Setup:**
- Hand-written evidence JSON for 3 scenarios:
  - (a) a single regional driver, with q < 0.05
  - (b) offsetting movements plus an AOV mix shift
  - (c) null: no q < 0.05
- A minimal slot renderer and VERIFY that follow ARCHITECTURE §7. Only one repair attempt is allowed.
- SYNTHESIZE runs on each S2L candidate through Ollama, with schema-constrained output and Pydantic validation.
- 20 reports: 7 for (a), 7 for (b), 6 for (c).

**Pass criteria:**
1. At least 18 of 20 reports reach `completed`, meaning VERIFY passes with at most one repair.
2. The null reports contain zero driver claims.
3. Every completed report for scenario (a) covers the planted driver.
4. No digits appear in template text outside the allowlist. VERIFY enforces this, but the rate before VERIFY is recorded.

**Also recorded:**
- invalid-reference rate before repair
- repair rate
- latency and tokens per call
- two rendered reports, included here so you can judge readability yourself
- for comparison, a naive regex number check run on the same reports, with its false-pass and false-fail rates

## S2 — superseded

The original S2, which measured Claude API cost, was replaced by S2L on 2026-11-15 under D9.
