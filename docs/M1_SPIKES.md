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

**Status: FROZEN on 2026-11-15, before any S2L call.** The question set, expected answers, scoring code and these criteria are committed together. Their SHA-256 hashes are in the journal. After the freeze, a harness bug may be fixed only if it's documented with the commit that fixed it. Thresholds, questions and expected answers don't change. If a model fails, the failure is reported and a way forward recommended (your requirement 9).

**Question:** which free local model, if any, runs well enough on the dev machine to drive RESOLVE, PLAN, ASSESS and SYNTHESIZE?

**Dev machine:**
- RTX 4050 Laptop, 6 GB VRAM, NVIDIA driver 617.14
- 15 GB RAM
- Ryzen 7 7445HS
- Windows 11 build 26200
- Ollama 0.40.16

**Candidates.** All three stay in until S2L decides: `gemma3:4b`, `qwen3:8b` and `gemma3:12b`. Each is pinned by digest. No other model is downloaded without your OK.

**Inference settings (identical for every model and stage):**
- `temperature=0`, `seed=1`, `num_ctx=8192`, `keep_alive=30m`
- `think=false`. For Qwen3, thinking-on is measured separately on PLAN and SYNTHESIZE, and is recorded only.
- `num_predict`: RESOLVE 256, PLAN 768, ASSESS 768, SYNTHESIZE 1536
- `format` = the stage's JSON schema
- one retry on a validation failure, with the error appended

**Setup** (`spikes/s2l_models/`):
- **RESOLVE:** 40 newly written questions (`questions.json`) over 3 dataset contexts:
  - context A ends on a complete month
  - context B ends right after a **year transition**
  - context C ends **mid-month**, so its current month is partial

  The 12 probe questions shaped the schema, so they're excluded.

  | Category | n | What it tests |
  |---|---|---|
  | clear | 5 | explicit metric and months |
  | relative | 5 | "last month", "two months ago", "same month last year" |
  | date boundary | 5 | months with no year, the month right after the data ends, partial months |
  | year transition | 5 | December/January questions in context B |
  | ambiguous | 4 | "sales", "basket size", no metric at all |
  | unknown metric | 8 | metrics the dataset doesn't define |
  | unsupported period | 4 | quarters, weeks, years, "last 90 days" |
  | invalid / out of range / partial | 4 | off-topic requests, months outside the data, the current partial month |

  Each question lists every answer that's acceptable. Code turns the model's output into one status: `ok` with metric, comparison month and baseline month; `unknown_metric`; `needs_metric`; `unsupported_period`; `out_of_range`; `partial_period`; or `invalid`. Two output schemas are scored for each model:
  - **months:** the model writes `YYYY-MM`
  - **transcribe:** the model copies the period, and code resolves it

  The better of the two counts for the gate, and the choice is reported.
- **PLAN:** 10 calls (5 dataset contexts × 2 variants). The schema is a step union whose enums cover metrics, dimensions, step kinds and segments: 16 values in the smallest context and 42 in the two largest.
- **ASSESS:** 10 calls on the same contexts, with hand-written evidence summaries.
- **SYNTHESIZE:** S6's 20 reports.

**What's measured. Each is reported separately (your requirement 3):**

| Dimension | Measures |
|---|---|
| Structured output | Share of calls that parse as JSON; share that pass schema validation, first try and after the retry |
| Semantic correctness | RESOLVE status and months against the accepted answers; PLAN and ASSESS semantic validity (only in-scope dimensions, a permutation, no duplicate playbook steps); S6 VERIFY results |
| Latency | Per-call wall time, prompt-processing and decode tokens/s, p50 and p90 per stage, cold-load time |
| Memory | Model size, share in VRAM at `num_ctx=8192`, GPU memory used, free system RAM before and after loading |
| Failures | Each failure classified as `transport`, `invalid_json`, `schema_violation`, `semantic_error`, `context_overflow` or `truncated` |

**Pass criteria (per model). All must hold:**
1. **RESOLVE:**
   - **at least 36 of 40** correct with the better schema (the same 90% bar as the earlier 27/30, set before any run)
   - **and zero substitutions**: a known metric given for an unknown or unspecified one, or a period invented for an unsupported one

   The Wilson 95% interval is reported. At n = 40, 36/40 has a lower bound of about 77%.
2. **Schema:** 10 of 10 PLAN calls and 10 of 10 ASSESS calls end in a schema-valid and semantically valid output, after at most one retry each.
3. **Latency.** Projected LLM time per investigation at **p50 ≤ 180 s and p90 ≤ 300 s**, with the model loaded and `num_ctx=8192`.
   - Projection: PLAN + 2 × ASSESS + SYNTHESIZE × (1 + S6 repair rate), with each stage's time already including its measured retries.
   - p50 uses each stage's p50, and p90 uses each stage's p90, which is conservative.
4. **S6 passes** on the same model.

**Selection** among the models that pass, in strict order:
1. more S6 reports `completed`
2. more RESOLVE answers correct
3. lower projected p50 latency
4. an Apache-2.0 license over custom terms

If **no model passes**, v1 ships Guided mode as the default and Full mode as experimental (ARCH §5.5). The checkpoint reports why and recommends what to try next.

**Also recorded:**
- the model digest, quantization, Ollama version, driver version and every inference setting, in each results file
- tokens in and out per stage
- thinking-on time and quality for Qwen3
- text-to-SQL accuracy. It's reported from the probe only, because it matters only for the M7 ReAct baseline.

## S6 — Claim templates and VERIFY

**Status: FROZEN on 2026-11-15, together with S2L.**

**Question:** can the model write slot-template claims that pass a deterministic VERIFY, without being pushed into naming drivers on null data? And does Guided mode, with its code-written templates, pass the same VERIFY?

**Setup** (`spikes/s2l_models/synth.py`):
- **Evidence.** Generated, seeded evidence JSON for 3 scenarios. Each report gets a different variant, with a different planted region and different numbers, so the 20 prompts aren't identical:
  - (a) a single regional driver, with confirmatory q < 0.05
  - (b) offsetting regional movements, two with q < 0.05 in opposite directions, on the ratio metric AOV, with a rate/mix decomposition
  - (c) null: no q < 0.05
- **Who writes what:**
  - **Code** writes the outcome sentence, because the outcome label is computed by code (ARCH §5.4).
  - **The model** writes a headline template and up to 6 claim templates. Each slot names an evidence reference and a format.
- **VERIFY** (deterministic) checks:
  1. every placeholder has a slot and every slot is used
  2. every reference resolves
  3. no digits in template text
  4. direction words match the sign of the claim's first change slot
  5. **coverage:** every confirmatory segment with q < 0.05 is referenced
  6. **driver wording** ("drove", "driver", "driving", "behind the", "responsible for") appears only in a claim that references a segment with q < 0.05, so null reports can't contain any
  7. no URLs, Markdown, "significant" or causal words ("caused", "because", "due to", "led to", "as a result")
- **Repair.** At most one: the VERIFY errors go back to the model.
- **Runs.** 20 reports per model: 7 for (a), 7 for (b), 6 for (c).
- **Guided mode.** Code templates for each evidence type are rendered and checked by the same VERIFY on the same 20 variants.

**Pass criteria (per model):**
1. At least 18 of 20 reports reach `completed`, meaning VERIFY passes with at most one repair.
2. The null reports contain **zero driver claims** after VERIFY. The pre-VERIFY rate is reported.
3. Every completed report for scenario (a) covers the planted driver.
4. No completed report has digits in its template text. The pre-VERIFY rate is reported.

**Guided mode criterion:** 20 of 20 Guided reports pass VERIFY with no repair. A failure here is a bug in our own code, and it gets fixed before M1 closes.

**Also recorded:**
- invalid-reference rate before repair
- repair rate
- latency and tokens per call
- two rendered reports per model, so you can judge readability yourself

**Change made before the freeze:** the naive regex number-check comparison is dropped. It would need a second, free-text generation mode plus hand-labeled ground truth. Since slot rendering makes "numbers come from evidence" true by construction, the comparison wouldn't inform any v1 decision.

## S2 — superseded

The original S2, which measured Claude API cost, was replaced by S2L on 2026-11-15 under D9.
