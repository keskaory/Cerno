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
- **The canonical hash ignores row order** because it sorts rows. That's right for set identity. Where order matters (top-N), the ORDER BY must be total, which the compiler enforces.
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

  The better of the two counts for the gate, and the choice is reported. *Clarified after review:* "better" means the schema that passes the RESOLVE gate. If both or neither pass, it's the one with more correct answers, then fewer substitutions.
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
   - **and zero substitutions**: a known metric given for an unknown or unspecified one, or a period invented for an unsupported one. *v2 counts this whatever the resulting status. An invented month that lands on a partial or out-of-range month is still a substitution.*

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

**Status: FROZEN on 2026-11-15, together with S2L.** v1 was frozen in `1b96f3c`. The independent review found that VERIFY was too lax, so VERIFY v2 (below) replaces it before any results were read (`reviews/2026-11-15-m1-spikes-review.md`). The pass criteria are unchanged.

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
  4. direction words match the sign: each signed slot is checked against the nearest direction word, so the clauses in "{a} fell while {b} rose" are checked separately; the word lists include above/below
  5. **coverage:** every confirmatory segment with q < 0.05 is referenced
  6. **driver wording** ("drove", "driver", "driving", "responsible for", matched as whole words) appears only in a claim whose `label` slots all name segments with q < 0.05, so null reports can't contain any
  7. no URLs, Markdown, "significant" or causal words ("caused", "because", "due to", "led to", "as a result")
  8. *(v2)* segment names never appear in template text; they come from `label` slots
  9. *(v2)* each slot's format fits its field (percent fields → pct, money fields → money)
  10. *(v2)* no `share_of_change` slot from a decomposition whose movements offset (gross/|net| > 3, ARCH §6)
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

---

# M1 results (2026-11-16)

The criteria above are unchanged. Everything below was measured after they were frozen.

## S7 results: FAILED (original criteria)

Run: `spikes/s7_calibration/run.py` → `results/s7.json` (10 s, CPU). It reproduces byte for byte on a re-run, apart from the timing field.

| Criterion | Result (levels, as ARCH §6 specifies) | |
|---|---|---|
| 1. any-driver rate ≤ 0.069, `null_flat` / `null_trend` | 0.040 / **0.107** | ✗ |
| 2. total PI coverage in [0.932, 0.968], flat / trend | 0.955 / **0.925** | ✗ |
| 3. power ≥ 0.80 at d = 3 | **0.37** | ✗ |
| 4. non-planted region flag rate ≤ 0.05 | max 0.022 | ✓ |
| 5. formula = statsmodels within 1e-9 | 7e-15 | ✓ |

Reported only: `seasonal_null` any-driver 0.018; `null_month_ar` (ρ = 0.4) 0.052; BY any-driver 0.018 flat / 0.042 trend.

**S7 stays failed.** The work below investigates a revision. It doesn't change this verdict.

### Investigation: log-level modeling (adopted by you as D11 on 2026-11-16; S7 itself stays failed)

**Same scenarios, log scale** (`run.py --log` → `results/s7_log.json`): criteria 1, 2, 4 and 5 pass (trend any-driver 0.051, coverage 0.948). Criterion 3 fails: power 0.49 at d = 3.

**Extended conditions** (`extended.py` → `results/s7_extended.json`, 1,000 null seeds per condition, 300 seeds per power point; random stream separate from the original):

| Condition | Levels FP / coverage | Log FP [Wilson 95%] / coverage | Log passes 1 & 2? |
|---|---|---|---|
| flat | 0.039 / 0.955 | 0.040 [0.030, 0.054] / 0.959 | yes |
| linear growth 1%/mo | 0.067 / 0.930 | 0.051 / 0.941 | yes |
| linear growth 3%/mo | 0.115 / 0.900 | 0.047 / 0.945 | yes |
| linear growth 5%/mo | 0.168 / 0.881 | 0.067 / **0.928** | **no** (coverage, borderline: 0.004 under the floor) |
| linear growth 8%/mo | 0.195 / 0.863 | **0.080** / **0.875** | **no** |
| compounding 3%/mo | 0.180 / 0.877 | 0.040 / 0.959 | yes |
| linear decline 3%/mo* | 0.014 / 0.989 | **0.272** / **0.764** | **no** |
| seasonal ±5 / 10 / 20% | ≤ 0.040 / ≈0.957 | 0.037 / 0.033 / 0.019, coverage ≈0.957 | yes (conservative) |
| monthly AR(1) ρ = 0.4 / 0.7 | 0.048 / 0.053 | 0.050 / 0.058, coverage 0.941 / 0.933 | yes (0.7 is borderline: 0.001 above the floor) |
| shocks ×2 | 0.048 / 0.944 | 0.041 / 0.944 | yes |
| heavy-tailed shocks (t₃) | 0.052 / 0.957 | 0.054 / 0.956 | yes |
| overdispersion (NB shape 1) | 0.048 / 0.955 | 0.046 / 0.958 | yes |
| 40 / 8 orders per day | 0.061 / 0.045 | 0.062 / 0.056 | yes |
| 3 orders per day (63% of segments gated out) | 0.039 / 0.950 | 0.046 / 0.954 | yes |
| new region launches mid-history (zeros before) | 0.043 / 0.967 | 0.036 / **0.974** [0.962, 0.982] | **no** (too wide: conservative) |

**Totals: log levels pass criteria 1 and 2 in 15 of 19 conditions; levels pass in 12.** The 4 log failures are listed above, and two passes (AR 0.7, and growth 5% on false positives) sit right at a threshold.

\* Cells decline 1.5–4.5% of their *starting* level per month. A decline that's linear in dollars is strongly concave in logs: at 3% and month 20, the drop is about 7.5% of the *current* level per month and still speeding up. The straight log-line can't follow it. The 5% floor clip touches the final month in only about 12% of seeds, so it isn't the cause. A compounding decline wasn't tested; that's the shape log modeling handles, as `compound_3pct` shows for growth.

**Why the launch case is conservative:** the launch is a step inside the history of the total and every channel. That inflates the residual SD, which widens the interval (coverage up) and costs power on those segments.

**What this says:**
- Log levels fix the heteroskedasticity failure for moderate and compounding growth, and they don't break under seasonality, autocorrelation, heavy tails, overdispersion or small segments.
- They fail when the trend is strongly curved on the log scale: steep linear growth or decline.
- Zero-valued segments are handled by the existing gates (zero in any prior period → `not_estimable`). A segment that appears mid-history is gated out, and the totals stay calibrated (conservative).

### Power and minimum detectable effect

Power is the share of seeds where the planted region is flagged with q < 0.05 in the planted direction. d is in units of the region's in-sample level residual SD, which is about 7% of the region's revenue.

| Condition | MDE80 as a % drop of the region's month (log) | MDE80 in flat-condition SDs (log / levels) |
|---|---|---|
| flat | **28%** | 4.1 / 4.6 |
| growth 3%/mo | **27%** | 5.9 / 5.7 |
| seasonal ±10% | **38%** | 5.9 / 6.8 |
| monthly AR(1) 0.4 | **28%** | 3.9 / 4.4 |
| heavy tails t₃ | **27%** | 4.0 / 4.5 |

The % column is the one to read. The SD units are the *flat* condition's residual SD for every row. So with growth, where the final level is larger, "SDs" overstates the effect needed; in % terms, growth costs nothing. Seasonality is a real loss, because the seasonal swing inflates the residual SD.

Power at d = 1, 2, 3, 4, 5, 6, 8 (flat, log): 0.04, 0.16, 0.43, 0.79, 0.95, 0.99, 1.00. Non-planted regions were flagged at ≤ 3.4% in every power run.

### Was "80% power at d = 3" realistic?

**No, not under the spike's own assumptions.** Take an ideal test: correct model, iid normal residuals, known df, Bonferroni α/8 (what BH asks of a lone discovery among 8 tests). Its power is set by the noncentral t-distribution (`extended.analytic_power`):

| k (prior months) | d = 3, no correction | d = 3, α/8 |
|---|---|---|
| 12 | 0.64 | 0.26 |
| 18 | 0.72 | 0.36 |
| 23 | 0.75 | 0.41 |
| average over 12–23 | — | 0.35 |

**The clearest proof is the uncorrected column:** even one test with no multiplicity correction reaches only 0.64–0.75 at d = 3. So 0.80 is out of reach before BH is even applied. At α/8, 80% needs between d = 4 (0.65 average) and d = 5 (0.87 average); BH power sits between those two bounds. The measured log-level power at d = 3 (0.43 extended, 0.49 in the frozen `s7_log.json` run) is *above* the ideal lone-test figure, because BH gains a little when the channel and total also move. The method isn't wasting power. The bar asked for more than 12–23 monthly points and 8 tests can give.

### Proposed revised criterion (needs your approval; not applied)

Replace criterion 3 with two parts, both stated before any new run:
- **3a, efficiency (gated).** In `null_flat`-shaped data, empirical power at d = 2, 3, 4, 5 is at least the ideal-test power at α/8 for the same k mix, minus 0.05. That's a lower bound the method must not fall below.
- **3b, MDE disclosure (gated as a product rule, not a number).** Every `baseline_compare` result reports its MDE at 80% power. A "no meaningful change" outcome shows the smallest change that could have been detected.

On the extended data, 3a would pass (0.16 / 0.43 / 0.79 / 0.95 against 0.12 / 0.35 / 0.65 / 0.87). That's stated for transparency. The criterion should be checked on a fresh run with a new seed stream if you approve it.

## S2L v2 results: NO MODEL PASSES

Harness v2 (`5850571`); `run.py` hash `ac2aa3e6` recorded in every results file. Ollama 0.40.16, NVIDIA driver 617.14, all models Q4_K_M, `remote_host` null, temperature 0, seed 1, num_ctx 8192, think off (qwen3 also run with think on, below). num_predict: RESOLVE 256, PLAN 768, ASSESS 768, SYNTH 1536. Results: `spikes/s2l_models/results/v2/` (moved there when v3 started). Run time: 20 min (4b), 139 min (qwen3:8b), 118 min (12b).

### Gate results

| Criterion | gemma3:4b (`68ceab7e`) | qwen3:8b (`500a1f06`) | gemma3:12b (`b92b2185`) |
|---|---|---|---|
| 1. RESOLVE ≥ 36/40, 0 substitutions | ✗ 1/40 (months) | ✗ 22/40, 1 subst. (transcribe) | ✗ 2/40 (transcribe) |
| 2. PLAN 10/10 and ASSESS 10/10 | ✗ 0/10, 10/10 | ✗ 7/10, 9/10 | ✗ 8/10, 6/10 |
| 3. latency p50 ≤ 180 s, p90 ≤ 300 s | ✓ 71 / 90 s | ✓ 178 / 255 s | ✗ 444 / 568 s |
| 4. S6 | ✗ 0/20 completed | ✗ 0/20 | ✗ 8/20 |
| Guided mode VERIFY | 20/20 | 20/20 | 20/20 |

Per the pre-registered rule, **v1 ships Guided mode as the default and Full mode as experimental** (ARCH §5.5).

### Measured separately

**Structured output.** With thinking off, all 420 calls across the three models parsed as JSON and passed schema validation on the first try, in every stage. Constrained decoding does what it promises. Every failure was semantic. With qwen3's thinking on, 16 of 30 first attempts failed to parse, because the reasoning text used up the output budget (`truncated`).

**Semantic correctness.**

| | 4b | qwen3:8b | 12b |
|---|---|---|---|
| RESOLVE months / transcribe | 1 / 1 | 21 / 22 | 1 / 2 |
| `intent: "other"` given (of 40), months / transcribe | 39 / 40 | 18 / 16 | 40 / 39 |
| PLAN semantic retries; final valid | 10; 0 | 3; 7 | 7; 8 |
| ASSESS semantic retries; final valid | 7; 10 | 1; 9 | 4; 6 |
| S6: completed / repaired / planted covered | 0 / 0 / – | 0 / 0 / – | 8 / 8 / 5 of 5 |
| S6: null driver claims before → after VERIFY | 5 → 0 | 0 → 0 | 0 → 0 |
| S6: drafts with digits before VERIFY | 20 | 20 | 13 |

Most frequent first-draft VERIFY errors: segment names written in template text (4b: 91, 12b: 30, qwen3: 10), digits in text (34 / 27 / 17), a slot format that doesn't fit its field (e.g. `delta` as `number`), stray braces, and for qwen3, causal words (13) and "significant" (12).

**Latency** (p50 per call; decode speed):

| Stage | 4b | qwen3:8b | 12b |
|---|---|---|---|
| RESOLVE | 3.2 s | 7.0 s | 10.1 s |
| PLAN | 14.1 s | 14.8 s | 78.8 s |
| ASSESS | 11.4 s | 6.7 s | 41.1 s |
| SYNTH | 16.9 s | 74.7 s | 141.2 s |
| decode tok/s | 48–54 | 12–16 | 6–8 |

The latency projection includes the measured S6 repair rate, which was 1.0 for all three models. With no repairs, 12b would still project about 302 s at p50.

**Memory** (num_ctx 8192):

| | 4b | qwen3:8b | 12b |
|---|---|---|---|
| model size | 2.9 GB | 6.6 GB | 9.0 GB |
| share in VRAM | 100% | 64% | 35% |
| GPU memory in use | 4.4 GB | 4.7 GB | 4.7 GB |
| cold load | 7.5 s | 9.8 s | 21.3 s |
| free system RAM during run | ~0.8 GB | ~0.8 GB | ~0.6–1.0 GB |

Free RAM stayed under about 1 GB of 15 GB while the larger models ran, so the system was close to swapping. The "before" RAM readings are unreliable, because the previous model may still have been unloading.

**Failures by class (thinking off):** no `transport`, `invalid_json`, `schema_violation`, `context_overflow` or `truncated` failures in any stage. All failures were `semantic_error` (RESOLVE retries, PLAN/ASSESS checks) or VERIFY rejections in S6. With thinking on (qwen3): 21 `truncated` (PLAN 7, ASSESS 3, SYNTH 11) and 8 `semantic_error`.

**qwen3:8b with thinking on:** PLAN 4/10, ASSESS 8/10, S6 0/6. p50 per call was 98 s for PLAN and 277 s for SYNTH. Thinking made it slower and no better.

### Harness defects found after the run (v2 stays the official result)

Reading the raw outputs, I found two defects in my own harness that penalize the models unfairly. **The v2 scores above are unchanged and remain the result.**

1. **The RESOLVE prompt never names `"explain_change"`.** The rule text says when to write `"other"` but never what to write otherwise; that value appears only in the decoding grammar. The Gemma models answered `"other"` while getting the metric and months right. Example: C01 in 12b's raw output is `revenue`, October 2025 vs September 2025, with `intent: "other"`.
   - *Diagnostic re-score, ignoring the `intent` field (not a pass):* 12b transcribe 36/40 with 0 substitutions; qwen3:8b transcribe 35/40 with 2 substitutions; 4b months 25/40 with 3.
   - Ignoring intent also removes the only way to answer I01 ("write a poem"), which is counted wrong. That's the conservative choice, since gemma3:12b gave `"other"` on 39 of 40 questions, so its intent field carried no information.
   - **12b's 36 sits exactly on the threshold and is post hoc. It isn't a pass.**
   - qwen3:8b used `intent` deliberately: it marked 16 refusal-type questions `"other"`. Forcing intent overrides that and *creates* one of its two substitutions (P02). Its own substitution is P03, where "2025 vs 2024" became Dec vs Dec. qwen fails either way.
   - Y03 ("between December 2025 and January 2026") is ambiguous. Both strong models read December as the comparison month. It stays in the frozen set, but a v3 question set should avoid that phrasing.
2. **The SYNTH evidence has no reference for the period label.** The question says "October 2025 compared with September 2025", but no slot can render a month. So a template that names the month must contain digits, which VERIFY rejects. Almost every first-try digit violation, for all three models, was "October/September 2025".
   - The independent reviewer re-verified with the period label stripped. gemma3:12b completes 6/20 on the first try (8–10 after repair), and qwen3:8b 3/20 (5–6). S6 needs 18, so **S6 still fails for every model** after this correction.
3. **The SYNTH prompt offers a `number` format that VERIFY accepts for no field** (found by the reviewer). The evidence also doesn't say that AOV's `rate_effect`/`mix_effect`/`total_effect` are dollars, so models reasonably chose `number` and were rejected. Relaxing this adds 1–2 completed reports. It doesn't change the verdict.

**A VERIFY gap that's too lenient:** the "significant" check is a substring of the rule's own word list and misses "significance". gemma3:12b's "statistical significance of {q}" passed. It didn't affect any outcome, but v3 must close it. The other rejections the reviewer sampled were genuine breaches of rules stated in the prompt.

None of these defects affects the latency verdict for gemma3:12b (302 s at p50 even with zero repairs). They did make every model's repair rate 1.0, so the latency *passes* of gemma3:4b (71 s) and qwen3:8b (178 s) are pessimistic, not optimistic.

### What would be needed to re-test (needs your approval)

A v3 harness that fixes only these three defects and the gap (name `explain_change` in the rule text; add a period-label evidence field; drop `number` or label each field's unit; match `signific*`), plus **a new, held-out set of 40 questions written and frozen before any run**. The current 40 have now shaped a fix, so re-using them would overstate accuracy. Thresholds unchanged. Expected cost: about 4.5 hours of local compute, $0. Even if v3 passed on quality, only qwen3:8b is near the latency bar, at 178 s p50 with a repair rate of 1.0.
