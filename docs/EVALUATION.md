# Cerno — Evaluation

Status: **v1.1 draft** (2026-11-15). Revised after the architecture review (H18–H20, ML14, ML20) and the consistency check.

Evaluation happens in three layers, ordered from cheapest and most deterministic to most expensive:

| Layer | What it checks | LLM? | Runs |
|---|---|---|---|
| 1. Tests | Code correctness, contracts, security boundaries | Scripted fakes | Every PR (CI) |
| 2. Stats calibration | Whether the statistics are honest | No | ~200 seeds per PR; ≥ 1,000 nightly and at milestone sign-off |
| 3. Agent eval | Whether investigations reach the right answer | Yes (live) | Manual; dev subset per change, full suite per milestone |

## 1. Tests

- **Unit:**
  - compiler output vs. golden SQL
  - decomposition identities (contributions sum to the total; Kitagawa leaves no residual)
  - `period_check`
  - slot rendering and derive expressions
  - each VERIFY rule
  - budget reservation math
  - terminal-state transitions (ARCHITECTURE §5.4)
- **State machine:**
  - The orchestrator is a pure `transition(state, event) -> (state, effects)`. Each LLM stage sits behind a typed interface (`resolve`, `plan`, `assess`, `synthesize`).
  - Tests use **hand-scripted fakes** that return Pydantic objects. These include adversarial outputs: an unknown metric, a bad slot reference, repeated steps, a refusal, and a `max_tokens` truncation.
  - A few recorded real payloads cover only the parse and refusal paths.
- **Integration:** API + SQLite + worker pool. Covers start, stream, cancel, restart → `interrupted`, and SSE reconnect with `Last-Event-ID`.
- **End to end (Playwright):**
  - a seeded replay with no LLM, asserting that no outside network requests are made
  - a live run with `FAKE_LLM=1` on the backend

### Security tests

Groups 1–5, 7–10 and 13 must pass before the first public deploy (milestone M5). Group 12 is added in M6, and groups 6 and 11 in M8, when upload and retention exist. In M2 only the worker-side parts of groups 1–4 can run, because there's no API yet.

1. **Sandbox:** inside the worker, file and network access fail or get killed. Covers:
   - `read_csv`, `read_text`, `read_blob`, `sniff_csv`, `parquet_metadata`
   - string-literal tables, `ATTACH`, `COPY`, `INSTALL`/`LOAD`
   - `SET`/`RESET` after the config lock
   - `query()`/`query_table()`, secrets functions
   - multi-statement strings
   - an outbound network connection attempt
2. **Worker isolation:**
   - `os.environ` is empty.
   - The uid is `cerno-worker`.
   - The worker cannot read `/proc/<api-pid>/environ` or the app DB file.
3. **Resources:** a recursive CTE, a cross join with ORDER BY (forcing a spill), huge `repeat`/`range` calls, and `string_agg` over a cross join are all killed within the limit. Meanwhile `/healthz` answers in under 200 ms, the volume's free space doesn't change, and no `.tmp` files are left behind.
4. **Result byte cap:** a result of 10k rows × 10 MB is truncated.
5. **Validator:** rejects string-literal tables, `Anonymous` functions, FROM-first syntax, `PIVOT`/`SUMMARIZE`, lambdas and filter injection. Freehand mode's runtime grain check catches fan-out and chasm joins.
6. **Uploads (milestone M8):**
   - Rejected: oversize streams with a false Content-Length, Parquet bombs, 100k-column CSVs, a single 150 MB line, mismatched file types, and filenames with path traversal or quotes.
   - Displayed safely: column names like `__proto__` and bidi characters.
7. **Cross-owner access:** every route returns 404 when given another owner's IDs.
8. **Sessions:** no token ever appears in a URL, invite codes are stored only as hashes, and a bad credential gets a 401 that reveals nothing.
9. **Injection end to end:** a fixture dataset whose values contain Markdown images, HTML, `javascript:` links and instruction text. The rendered report makes no outside request and runs no script, VERIFY rejects any URLs, and the values are flagged as caveats.
10. **Budget races:** 10 concurrent runs against a $1 cap never exceed $1 plus one reservation, and an invite code with no budget left can't start a run.
11. **Retention:** after the deletion job runs, no rows or files reference the deleted version.
12. **Logs:** a cell value that appears in a conversion error does not show up in the logs.
13. **CSP:** the header is on every route, with no inline scripts.

## 2. Stats calibration (H18)

This is the cheapest high-value check we have. It stops the agent eval from signing off on a stats design that is wrong.

- **Data generator:**
  - Customer heterogeneity: gamma–Poisson order frequency and lognormal order values.
  - Weekly seasonality, AR(1) daily shocks, and a shared shock per period. This makes period-to-period variance larger than row-level variance, as it is in real data.
  - Each seed gets between 13 and 24 months of daily history, so short and long histories are both calibrated. The demo seed uses 24.
  - An extra annual-seasonality scenario is reported but not asserted. It measures the failure mode that ARCHITECTURE §6 lists as a known limit.
- **Null calibration:** `baseline_compare` and the full MULTIPLICITY pipeline (the total plus every segment, across dimensions whose segments overlap) are run on null seeds. The rate of "any driver reported" must fall inside the 99% binomial band around α = 0.05, and prediction-interval coverage must fall inside the band around 95% (method from Morris, White & Crowther 2019).
- **Power curves:** effects of 0, 0.5, 1, 2 and 4 baseline SDs. These are reported in the journal, not asserted.

## 3. Agent eval

### Datasets
- **Synthetic e-commerce:** built from the same generator, with planted effects in the final month. The generator also writes a **ground-truth manifest** that lists the planted effects and states that every other segment comes from the same distribution.
- **Semi-synthetic Olist:** real public data with injected effects.
  - The license is believed to be CC BY-NC-SA. It needs to be verified, and if it's ShareAlike, the bundled derivative has to carry the same license.
  - Its roughly 23 months of history, growth trend and annual seasonality sit at the edge of `baseline_compare`'s linear-trend assumption. Reporting how it does there is part of the point.

Each scenario is labeled with the layer it actually tests:

| Scenario | Tests |
|---|---|
| Regional order-rate drop (fewer orders per active customer in one region) | agent: finds the driver |
| AOV mix shift (Simpson's paradox) | Kitagawa + Simpson's flag + wording |
| Offsetting movements | share-of-change suppression + wording |
| Fake signal (a segment with no true effect that looks big) | MULTIPLICITY across segment baselines; agent restraint |
| Null (nothing changed) | specificity |
| Partial current period | QuestionSpec / `period_check` |
| Instruction text in values | injection handling |
| Duplicate rows | data-quality layer (scored separately; tests the profiler, not the agent) |

### Answer keys

The field names match the QuestionSpec (ARCHITECTURE §5.1).

```yaml
question: Why did revenue fall in October compared to September?
expected_spec: {metric: revenue, comparison_window: 2025-10, baseline_window: 2025-09}
expected:
  outcome: change_beyond_normal_variation
  drivers: [{dimension: region, value: West, min_share: 0.7}]
  non_drivers: all_other_segments
  decomposition_method: additive_segment     # symmetric_kitagawa for ratio-metric keys (AOV)
```

For a partial current period, `expected_spec` encodes the day-aligned alternative that RESOLVE should offer. The harness accepts RESOLVE's spec as is.

### Metrics

Each metric is reported with a Wilson or bootstrap 95% CI.
- QuestionSpec accuracy
- Driver recall and **driver precision**. Only *driver claims* count: claims about a segment with strength Beyond normal variation (ARCHITECTURE §7).
- **Specificity** on null and fake-signal scenarios. Target ≥ 90%. MULTIPLICITY controls the "any driver" false-positive rate near α = 0.05, so a calibrated system should land around 95%.
- **Unsupported-claim rate before VERIFY**, plus VERIFY's false-reject rate against hand labels
- Distribution of terminal states and outcomes
- Cost and latency per run (p50, p90)

### Baselines
1. **Unconstrained ReAct agent:** same model, a read-only `run_sql` tool and the same budget. Its SQL runs through the same worker, with the validator in freehand mode. This is the main comparison, built in milestone M7.
2. **Playbook with no LLM:** every dimension, no model choices. It shows what the LLM actually adds. Built in M3 (formerly spike S5).

Comparisons are paired by question, and we report the paired difference with a bootstrap CI. Single-shot text-to-SQL and a "without VERIFY" ablation were dropped: the first is a straw man, and the pre-VERIFY unsupported-claim rate already measures what VERIFY catches.

### Size and cost
- **Dev subset (milestone M3 on):** about 12 questions × 1 seed, Cerno only. Target ≤ $10 per run, to be repriced after spike S2.
- **Milestone suite (milestone M7):** 50–80 questions × 3 seeds, plus null scenarios × at least 30 runs. That's roughly 180–270 runs per system, or about $90–200 for Cerno at $0.50–0.75 per run, and about the same again for ReAct. A $50/month dev budget can't pay for a full suite, so each one needs its own budget approval (D6).
- Every results file records the git SHA, a hash of the prompts, the model and effort settings, and the seeds. Results go in `evals/results/`.

### Regression
Every bug found in an investigation becomes a new scenario or question.

### Known limits (to state in the README)
- The same person designed the generator and the tools. The semi-synthetic set reduces that problem but doesn't remove it.
- The planted effects are the kinds the tools look for.
- In v1 the eval covers one question type.
- Calibration on our generator doesn't guarantee calibration on real data.
