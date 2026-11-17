# Cerno — Development Journal

A running notebook of what we decided, why, and what we learned. Newest entries at the bottom.

---

## 2026-11-15 — Starting point

**Repo state:** one commit with a `README.md` saved as UTF-16 (PowerShell's default `>` encoding). Remote is `github.com/keskaory/Cerno`. Nothing else to preserve.

**Machine:** Windows 11, Python 3.13.6, git. No Node, uv, Docker, or `gh` yet. Node is needed for the frontend; uv is the plan for Python environments. Nothing gets installed until implementation is approved.

**`/ponytail`:** not installed and not in the plugin catalog. Waiting to hear where it comes from; until then, reviews use the built-in subagents plus `/code-review` and `/security-review`.

**Discovery questions sent (unanswered at the time of writing):** timeline, primary user, auth, who pays for LLM calls on the demo, data sensitivity, whether the agent may execute code, metric definitions, demo datasets, stack flexibility, git workflow. The v0 architecture was drafted using the recommended defaults for each one, and each is marked `ASSUMPTION` in the docs.

## 2026-11-15 — The v0 architecture and the ideas behind it

Two framing choices shaped the draft more than any technology choice:

1. **"Why did revenue drop?" is treated as decomposition, not causation.** Observational data can tell you which segments account for a change and whether a difference beats noise. It can't tell you what caused it. Building that limit into the product (tool outputs, claim labels, a VERIFY check on causal wording) is more honest, and it's the accountability story.
2. **Constrain the agent instead of trusting it.** A fixed state machine with deterministic stats tools and compiled metric queries, rather than an open loop where the model writes SQL and Python freely. Less flashy, but testable and much harder to get confidently wrong.

The evaluation harness is scheduled *before* the agent. Without planted-effect datasets we'd have no way to tell whether a prompt change made investigations better or worse.

## 2026-11-15 — Architecture review: what changed and why

Six reviewer subagents (backend, AI, data, statistics, security, product) were each told to attack the v0 docs. The full ranked log, with how each finding was resolved, is in `reviews/2026-11-15-architecture-review.md`. These were the changes that mattered most:

**1. The statistics were answering a question nobody asked.** v0 ran Welch and z-tests comparing October's orders to September's. But that's the *entire population* of orders, not a sample. With 50,000 rows almost any change gets p ≈ 0, so a "tested" label would have meant nothing. Worse, orders cluster within customers, so even the within-period tests had standard errors that were too small. The fix:
- Judge a period change against the history of comparable changes ("−12% is outside the range of the last 12 month-over-month swings").
- Run within-period tests at the customer level with a cluster bootstrap.
- Apply BH correction across every p-value in the investigation, not just inside one scan.

This was the most important catch in the review. It would have been invisible in an eval that used iid synthetic data, which is why the data generator now includes customer heterogeneity and period-level shocks, and why the stats tools get their own calibration suite.

**2. "Every number matches the evidence" can't be checked after the fact.** Pulling numbers out of free text and matching them against result tables fails both ways: "12%" was computed from two totals, and "8" matches some random cell. So the model doesn't write numbers anymore. It writes templates with slots that point at evidence fields, and code renders them. The guarantee now holds by construction, and VERIFY spends its effort on things that are actually hard to check: did the report mention the main driver, and does "fell" match a negative value?

**3. Running DuckDB in the API process was the wrong security boundary.** DuckDB's own docs say its settings aren't a full sandbox for untrusted SQL, and there was a real 2024 bypass (CVE-2024-41672) that read `/proc/self/environ` with external access turned off. In v0 the API key lived in the same process. Now all DuckDB and statistics work runs in worker subprocesses with an empty environment, resource limits and a kill on timeout. That also fixed a separate bug the backend reviewer found: synchronous DuckDB calls would have frozen the async event loop and every live stream with it.

**4. The model stops calling tools.** It returns a typed plan and code runs it. Every LLM call is single-turn. This sidesteps Opus 5.5's rule that thinking blocks are bound to an exact conversation history (rebuilding history each turn would have caused errors), keeps prompt caching predictable, and makes the state machine testable with scripted fakes.

**5. The plan was bottom-up while claiming to be vertical.** v0 had eight horizontal layers before anyone could watch an investigation run. Now M2 is a walking skeleton: a CLI that goes from question to HTML report with the narrowest version of each layer. Scope also shrank to one question type and one domain, with upload moved late.

**6. Hosting and infra were based on free tiers that don't exist.** Render's free tier has no disks and Fly no longer has a free tier, so realistic hosting is about $7–12/month on one machine. Since DuckDB files on a volume pin us to one machine anyway, Postgres on Neon and a separate Vercel frontend added vendors and cross-origin problems for no benefit. The recommendation is SQLite plus a SPA served from the same origin. That's your call (D2, D3), because it changes the stack you originally listed.

**Disagreements between reviewers:** the product reviewer wanted a "Statistically supported" label; the statistician argued that significance wording is the wrong frame once period changes use a historical baseline. We went with the statistician. The security reviewer designed bearer tokens for the event stream; serving everything from one origin made a plain cookie work instead.

**Still unvalidated:** cost per investigation (the v0 numbers contradicted each other: the token caps worked out to about $2 against a $0.75 cap), sandbox behavior under the locked config, hash stability, and the hosting topology. These are spikes S1–S4. They need code, so they wait for approval.

## 2026-11-15 — Consistency check: the stats fix wasn't finished

After the v1 rewrite, a separate agent checked all the docs against each other. Five problems blocked approval. One was the important kind: the historical-baseline method from the review still didn't connect to anything.

- **The baseline method couldn't feed the multiple-testing correction.** With k = 8 historical changes, an empirical percentile can't go below about 1/9. So once BH adjusts it, nothing can ever reach q < 0.05. On top of that, the playbook didn't produce any per-segment p-values for BH to correct, so the "fake signal" eval scenario was testing a mechanism that didn't exist. The fix:
  - Standardize the change against the mean and SD of k ≥ 12 non-overlapping monthly changes and use a t(k−1) p-value.
  - Run that on the total and on every segment.
  - Apply BH across all of those p-values.
  - Generate 24 months of history so k ≥ 12 is actually available.
  
  Under the null, BH holds the "any driver reported" rate near α, which also makes the ≥ 90% specificity target reachable. Before this fix, a correctly calibrated 5–95 rule would have hit 90% only about half the time.
- **Spikes depended on code from later milestones.** Spike S2 was meant to measure the cost of the full agent before any agent code existed. It now runs on hand-written evidence and gets re-measured on the M2 skeleton.
- **The rest:** a planted effect that no defined metric could express, a public deploy scheduled before the security tests, and eval budgets that didn't add up.

Lesson: rewriting six docs from a findings list leaves gaps exactly where two findings meet. The statistician's fix (use a historical baseline) and the AI reviewer's fix (compute strength from q-values) were each fine on their own, but they hadn't been joined up until this check looked at both.

I also cut `compare_groups` and `scan_segments` from v1. Nothing in the playbook called them, and the 355·skew² sample-size gate on lognormal order values would have demanded thousands of customers per segment anyway.

**Later the same day.** A verification pass caught one more statistics bug. "k ≥ 12 non-overlapping month-over-month changes" means disjoint month pairs, and 24 months only gives 11 of those. The looser reading, consecutive changes, has a different problem: each change shares an endpoint with the next, so the changes are correlated, and a t-test that ignores that gets the SE wrong. The cleaner approach is the textbook one. Fit a linear trend to the prior months' levels and ask whether the comparison month falls outside the one-step-ahead prediction interval. Its SE already accounts for estimating the mean and the slope, so no separate correction is needed.

It also caught a quieter conflict. The VERIFY coverage rule required mentioning any segment with a ≥ 50% share. In a null run, some segment always has a big share of the noise, so the rule would have forced every null report to name a "driver". Coverage now keys off q-values, not shares.

## 2026-11-15 — M0–M1 approved

You approved M0 and M1 and accepted WSL2 (D5) and the PR-based git workflow (D8). You also asked for statistical calibration in M1 rather than M3. That's the right call: the review's biggest catch was a statistics bug, and calibration costs nothing to run. It's now spike S7. Hosting (S4) moved to just before M5, since nothing needs it sooner.

The spike criteria went into `M1_SPIKES.md` before any spike code was written, so they can't be tuned to fit the results.

WSL wasn't installed and there's no API key yet. You're installing WSL, and I'm doing the platform-independent spikes (S3, S7) first.

## 2026-11-15 — M0 done, S3 passes, then the plan changed: $0 and local-first

**M0.**
- **Commits:** branch `m0-tooling`, three focused commits plus one commit of review fixes. It's pushed, but the PR isn't open yet: `gh` isn't installed, and CI only runs on PRs.
- **Review (subagent):** no blockers. Medium-severity findings:
  - no `permissions:` block in CI
  - pandas would fail strict mypy
  - `runs/` and `*.parquet` were ignored too broadly
  - spikes sat outside every check

  All fixed. Spike checks join CI on the first spike branch.

**S3 passed on Windows** (WSL2 re-run pending). Three findings mattered more than the pass:
- At 4 threads, a ties-plus-LIMIT query returned different rows on all 50 runs, which confirms the "total ORDER BY" compiler rule.
- DuckDB files built from the same Parquet aren't byte-identical, so the dataset fingerprint has to hash logical content.
- `TimeZone` can't be set in `connect(config=...)` with autoload or external access off, because it's an ICU setting. It's `SET` after connect and then locked. I checked that every later `SET` fails once the lock is on.

**Then you set D9:** $0 additional spending, and local-first. That retires the Claude API, Fly.io and every dollar budget. Implementation stopped so the plan could be revised and reviewed first.

**Hardware:**
- RTX 4050 Laptop, 6 GB VRAM
- 15 GB RAM
- Ryzen 7 7445HS
- Ollama 0.40.16, already installed with `gemma3:4b` and `gemma3:12b`

You approved downloading `qwen3:8b`. A license check by a subagent found everything planned is free. The conditions are recorded in D9: Gemma's custom terms, Olist's NC-SA license, and gitleaks only being free on personal accounts.

**Preliminary probe** (`spikes/s2_local_llm/probe.py`, 12 RESOLVE-style questions plus 6 SQL questions; directional, not a gate):

| Model | Share in VRAM | Decode speed | Windows, model writes dates | Months as `YYYY-MM` | Transcribe, code resolves | SQL |
|---|---|---|---|---|---|---|
| gemma3:4b | 100% | 49 tok/s | 2/12 | 9/12 | TBD | 6/6 |
| qwen3:8b | 70% | 17 tok/s | 3/12 | 9/12 | TBD | 5/6 |
| gemma3:12b | 37% | 7.6 tok/s | 8/12 | 12/12 | TBD | 6/6 |

What I learned:
- **Small models fail at arithmetic, not at reading.** The worst errors were inclusive end dates and picking the wrong year for "August". Moving date arithmetic into code is the same principle as "the model never writes numbers". So RESOLVE now transcribes the question ("month 8, no year", "1 month ago") and code resolves it.
- **Constrained decoding isn't validation.** Ollama's grammar ignored a regex `pattern` in the schema. Every output gets Pydantic validation and one retry.
- **My first SQL grader was wrong.** It compared whole rows, so a correct answer with month labels formatted differently counted as wrong. It now compares numbers only. The lesson: check the grader before believing a score.
- **Speed falls off fast once a model spills out of VRAM.** At 37% on the GPU, `gemma3:12b` is about 6× slower than the 4B model.

**The revised plan (v2.0):**
- an `LLMProvider` interface with `OllamaProvider` and `ScriptedProvider`
- Guided mode as an honest no-LLM fallback with identical numbers
- budgets measured in LLM seconds
- a local server locked to 127.0.0.1 with a per-launch token, instead of invite codes
- a static replay showcase on GitHub Pages
- a local benchmark pinned by model digest
- S2 replaced by S2L (model selection), with criteria fixed before it runs and a newly written question set, because the probe questions shaped the schema

**Review of v2.0 and fixes.** An independent reviewer found one critical problem, four high and five medium. The critical one was mine. I'd adopted the "transcribe" RESOLVE schema even though its only measurement so far was 4/12 on `gemma3:4b`, below the months schema's 9/12, and I'd written "TBD" in the docs while that number was already sitting in the output file. The cause was the schema itself: it required `year`, `month` and `months_ago` all at once, so the model filled two of them ("October, 1 month ago"). Lesson: **a schema that allows contradictions will get them.** It's now a one-of union. The other main fixes:
- refuse Ollama `-cloud` models, which are served from localhost but run remotely
- require WSL mirrored networking, so Ollama never listens on 0.0.0.0
- use one fixed `num_ctx`, with a typed overflow error
- a confirmatory BH family that's the same in both modes, so PLAN can't prune dimensions

Full log: `reviews/2026-11-15-local-first-review.md`.

**Git attribution.** You don't want Claude listed as a GitHub contributor. Five pushed commits carried a `Co-Authored-By: Claude` trailer. With your explicit approval, I rebuilt them with identical trees, authors and dates, minus the trailer, and force-pushed `main` and `m0-tooling` with `--force-with-lease`. New hashes: `main` is at `5baae19`, `m0-tooling` at `6550614`. Future commits and PRs carry no AI attribution (CLAUDE.md).

**Probe v2 results** (one probe version `dcca7674e90c699d`, Ollama 0.40.16, all Q4_K_M, `remote_host` null for all three; summaries in `spikes/s2_local_llm/results/`):

| Model (digest) | Share in VRAM | Decode speed | Windows | Months | Transcribe v1 | Transcribe v2 (union) | p50 per call |
|---|---|---|---|---|---|---|---|
| gemma3:4b (`68ceab7e`) | 100% | 48 tok/s | 2/12 | 9/12 | 4/12 | 5/12 | 4.0 s |
| qwen3:8b (`500a1f06`) | 70% | 18 tok/s | 3/12 | 9/12 | 7/12 | 7/12 | 6.6 s |
| gemma3:12b (`b92b2185`) | 37% | 7.8 tok/s | 8/12 | 12/12 | — | 12/12 | 13.4 s |

The union fixed the contradiction bug but didn't help the small models. They still map "August" to a relative month, and `qwen3:8b` turned "last month" into November 2024. The 4–8B models do best on the simplest schema (months). Only the 12B model gets everything right, but it's partly on the CPU and about 3× slower. All three substituted a known metric for an unknown one at least once ("churn" → orders_count) in some mode. That's why S2L now has a zero-substitution gate. The probe has 12 questions, so these numbers are directional only.

## 2026-11-15: S2L and S6 frozen before any run

You approved v2.0 and asked for the S2L questions and criteria to be frozen before the benchmark runs. They are now committed on `m1-spikes`, and no model has seen the new questions yet.

**What's frozen:**
- 40 RESOLVE questions in 8 categories (clear 5, relative 5, date boundary 5, year transition 5, ambiguous 4, unknown metric 8, unsupported period 4, invalid 4), over three dataset contexts. Context C has a partial month.
- 10 PLAN and 10 ASSESS contexts. Enums run from 16 to 42 values. An earlier draft said 31; I counted, and the largest context has 42.
- 20 SYNTH scenarios: 7 with a planted driver, 7 with an AOV offset, 6 null.
- The pass/fail criteria and the selection order in `docs/M1_SPIKES.md` (S2L and S6 sections).

**Hashes** (SHA-256 of the working-tree files at freeze; `run.py` writes the first 16 hex characters of each into every result file, so a result can be checked against this list):

| File | SHA-256 |
|---|---|
| questions.json | `55e21b750e91e4bd93ad4616b7d88ca8cce8df6c05105b19fc64833556bc7f23` |
| resolve.py | `f56ff823173511f44b509f5892f2ca412666df2c64767a3fbe204111c6362ce9` |
| plan.py | `8709822e5faec9e0331624aecae82d22aa12db1ca0df2d8a1f2bf26953a2ceb7` |
| synth.py | `8634334eed1cbd6d9c36e0579972bcfaa9df60ab5bb34fae77a1399803c16436` |
| client.py | `b1aa1fd8fa718e1d4e9faab67898fd9c5abafd75c21308c41c51468f0a8f681c` |
| run.py | `3ac71f00b3733a969bb119e16df02ad42fa7e09531adaff804b91f19b1c07794` |
| test_s2l_harness.py | `4c419d085e2d66e39a4e662727b3ed0c875adb30c80b6f9fa0526f57b6a34f90` |
| M1_SPIKES.md lines 122–249 (S2L + S6 criteria) | `e389a6978b24d5cde122ed4ce3f0554882d17839532bf584fead7556a5293699` |

**Rule from here:** if a model fails, the thresholds stay. A harness bug found after a run gets fixed in a new commit, every model is re-run, and the journal says what changed and why. A threshold change needs your approval.

**Harness tests (71)** check that every question can be answered with its gold answer, that Guided mode passes VERIFY on all scenarios, that each VERIFY rule fires, and the rendering and stats helpers. A low score should mean the model failed, not the harness.

## 2026-11-15: Review found the v1 harness too lax; v2 frozen, v1 run stopped

An independent review of the spike code found 16 issues (`reviews/2026-11-15-m1-spikes-review.md`). The serious ones made the S2L/S6 gates easier to pass than the spec says:
- substitutions that landed on an out-of-range month weren't counted
- a claim could name the wrong region in plain text
- the direction check gave up on any sentence with both "fell" and "rose"
- PLAN extras could use out-of-scope dimensions

Several of the fixes feed the retry and repair loop, so they can't be applied by re-scoring saved outputs. The v1 run had been going for about 8 minutes on `gemma3:4b` and hadn't written any results. I stopped it. Nobody looked at any v1 model output.

**What changed in v2:** scoring, VERIFY, PLAN/ASSESS checks, one schema form (`same_month_last_year`, for R05), and two prompt lines saying "only in-scope dimensions" and "segment names go in label slots". **No threshold changed, and the 40 questions are byte-identical** (same hash as v1).

**v2 hashes** (the same files as v1, working tree at freeze, LF line endings):

| File | SHA-256 |
|---|---|
| questions.json | `55e21b750e91e4bd93ad4616b7d88ca8cce8df6c05105b19fc64833556bc7f23` (unchanged) |
| resolve.py | `8cf26a8fb386c5f0946b124e56dcde422da7e8b9b6157dc42974d6ee4feee4c5` |
| plan.py | `d5c44696128d40fa13849cd10c1d5bd4e6ce991f2033a10072423027005a3ced` |
| synth.py | `57679fe77387939bbd85493a91486a8118907c9bd817f7462c9c7c7d0eb9cc27` |
| client.py | `531e720e41154d17e8ec782767bf1cab2b8a0e99673e763f7936e65654b851b8` |
| run.py | `ac2aa3e61ed894ff4f2746cf51732d36f05adda0c22cc7374f65d30d2af18637` |
| test_s2l_harness.py | `a5fe0d8a2d1f11c1eac4ba81a79d61ad3978f2f2e4c275912eacfd1a885bdc41` |
| M1_SPIKES.md lines 123–253 (S2L + S6) | `31be64980a048ad0609139c1e79b5d8710a33079a22f4337a03c25320d06b2a5` |

**S7 ran (10 s on CPU) and fails.** Results: `spikes/s7_calibration/results/s7.json`.

| Criterion | Levels (ARCH §6 as written) | Log levels (candidate) |
|---|---|---|
| 1. any-driver rate ≤ 0.069, flat / trend | 0.040 / **0.107** ✗ | 0.036 / 0.051 ✓ |
| 2. PI coverage in [0.932, 0.968], flat / trend | 0.955 / **0.925** ✗ | 0.955 / 0.948 ✓ |
| 3. power at d = 3 ≥ 0.80 | **0.37** ✗ | **0.49** ✗ |
| 4. non-planted region flags ≤ 0.05 | 0.022 max ✓ | 0.027 max ✓ |
| 5. formula = statsmodels to 1e-9 | 7e-15 ✓ | 7e-15 ✓ |

Why:
- **Trend failure: heteroskedasticity.** Business noise scales with the level. In a growing series, the newest month is noisier than the average month the fit learned from, so the interval is too narrow exactly where it's used. On log levels the noise is roughly constant, and calibration comes back. A diagnostic that removed the multiplicative shocks still failed in levels, because Poisson and lognormal order noise also grows with the level.
- **Power failure: short history plus 8 tests.** With 12–23 months and BH over 8 p-values, a drop of 3 residual SDs is caught 37% of the time in levels and 49% in logs. Without the BH correction, it's 72% (diagnostic). 80% power needs about 5 SDs in levels. That's a property of the data and the test, not a bug. The 0.80 bar was a guess made before measuring.
- **Reported only:** with seasonality (±10%) the method is *conservative* (any-driver 0.018), because the sine wave inflates the residual SD. With AR(1) monthly shocks at ρ = 0.4, any-driver is 0.052 in levels and 0.044 in logs, inside the band but closer to it.

I haven't changed the method or the bar. Both are your call at the checkpoint: switching `baseline_compare` to log levels (an ARCH §6 change), and either accepting a lower power bar or reporting the MDE honestly.

## 2026-11-16: M1 results. S2L: no model passes. S7 fails; log levels investigated

**S2L v2 ran on all three models** (frozen harness `5850571`, run.py hash `ac2aa3e6` in every results file). No model passes.
- gemma3:4b and qwen3:8b are fast enough (projected 71 s and 178 s) but wrong too often.
- gemma3:12b fails latency (444 s), with 65% of the model on the CPU.
- By the pre-registered rule, **Guided mode is the v1 default**, with Full mode experimental. Guided passed VERIFY 20/20.

Reading the raw outputs, I found that **my harness was partly to blame**:
- The RESOLVE rule text never names `"explain_change"`. The Gemma models put the right metric and months next to `intent: "other"`.
- SYNTH had no slot for the period label, so naming "October 2025" broke the no-digits rule.
- The reviewer found a third: a `number` format that VERIFY never accepts.

I kept the v2 scores as the result. A diagnostic re-score puts gemma3:12b at exactly 36/40 on RESOLVE, but that's post hoc and on the bar. S6 still fails for every model once the period-label defect is removed. Lesson: freezing protects against tuning, but it doesn't protect against a broken instrument. A hand-read smoke run before the full run would have caught all three (R18).

**S7 extended** (`extended.py`, 19 conditions; the original S7 re-run is byte-identical):
- Log levels pass the calibration criteria in 15/19 conditions.
- They fail with steep linear growth or decline (the series curves in log space). A mid-history launch is conservative.
- The MDE for 80% power is about 28% of a region's month, or 38% with ±10% seasonality.
- The original 80%-at-3-SD bar was unreachable: one uncorrected ideal test gets only 0.64–0.75.
- I proposed a replacement criterion (efficiency against the ideal test, plus MDE disclosure) for approval. It isn't applied.

Second independent review: `reviews/2026-11-16-m1-results-review.md`. It corrected my pass count (13 → 15) and showed the "6 SDs with growth" was a units artifact. No verdict changed.

## 2026-11-16: D11 approved: `baseline_compare` fits log levels

You approved log-level modeling at the M1 checkpoint.
- **Recorded:** D11 in `DECISIONS.md` (on `main`, `d06d1aa`). ADR-010 and ARCH §6 are revised: log-scale OLS, the interval transformed back (so asymmetric), a new gate for a zero comparison month, descriptive-only for metrics that can be negative, and the measured limits. Steep linear growth or decline still breaks calibration, and an 80% MDE is about 28% of a region's month.
- **Not changed:** S7 stays recorded as failed. The power criterion is still open (D12), as are S2L v3 vs. Guided default (D13), the model (D4) and the timeline (D1).
- **Still owed:** a calibration run of the product's `baseline_compare` (M3 eval suite), because the spike code isn't the product code.
- **Learning guide:** added a worked log-scale example with numbers checked in Python. It shows a −13.9% month with p = 0.008 that BH *doesn't* flag on its own among 8 tests.

## 2026-11-16: S2L v3 harness frozen before any run

Per D13 I wrote the v3 fixes and a new held-out set of 40 questions (contexts D, E, F; same categories and counts as v2). An independent review found six issues, all fixed before freezing (`reviews/2026-11-16-s2l-v3-prerun-review.md`). The worst was that "insignificant" slipped past the significance check. A similarity scan showed some held-out questions were close to v2 wording, so I reworded the closest ones. The remaining matches share a sentence template but use different months and metrics.

Frozen at `ebf15a3`. No v3 model output existed before this point. SHA-256:

| File | SHA-256 |
|---|---|
| questions_v3.json | `71cd4ccb2da1bb0e8c3e60255a1db42ef8a291584be37df65feec017307c6837` |
| resolve.py | `8d5d47d0c84d84eb945cb04543fc2c1fe77f330ba52d4c19a0173efd630b44af` |
| plan.py | `d5c44696128d40fa13849cd10c1d5bd4e6ce991f2033a10072423027005a3ced` |
| synth.py | `c7f840b2fe103286c85d399f730b5f4574a9a7835a7b685201bc00fef640f37e` |
| client.py | `531e720e41154d17e8ec782767bf1cab2b8a0e99673e763f7936e65654b851b8` |
| run.py | `089afaec8bf34d36f1d3ddb0a842d15a3a38edee63cda155cb9f139c671659c6` |
| test_s2l_harness.py | `632d1ef011e66192ec75e142d91566fd3f589bfad0dadfa0880ac441a36ef0a8` |

`run.py` writes the first 16 hex digits of each into every results file, so a result can be matched to this freeze. Next: a smoke run on the v2 questions, read by hand. If it forces a change, that change gets its own review and new hashes before the full run.

Smoke run (gemma3:4b, v2 questions C01, R05, U01, P02, plan contexts 0 and 6, synth scenarios a, b, c; `out/v3/smoke_gemma3_4b.json`, not scored), read by hand:
- The three D13 fixes work. Every RESOLVE output used `explain_change`, SYNTH filled month names through the period-label slots, and no output asked for `number`.
- Every failure was the model's. Transcribe put `same_month_last_year` where the question named September or no baseline. "Churn" became `active_customers`. "Last week" became a month after the retry. PLAN repeated a dimension, used the out-of-scope `promo`, and left out `segment` on `describe_change` although the field exists. SYNTH showed `delta` (dollars) as a percent while `delta_pct` was available, and wrote "{month} compared to {month}".
- One leniency in VERIFY, present in v2 too: a template can write a literal `%` after a percent slot ("{share}%"), which renders as "%%". It can only make a model look better, it doesn't put a number in model text, and changing VERIFY now would need another review. So I left it, and I'll count those templates in the results.

No change forced; the full run uses the frozen `ebf15a3` harness.

## 2026-11-16: S2L v3 results. No model passes; Guided mode stays the default

The full v3 run took about 4.5 h on the frozen harness (`ebf15a3`). Results: `M1_SPIKES.md` → S2L v3 results; commit `4308045`.
- **RESOLVE:** 4b 23/40, qwen3:8b 32/40, 12b 36/40. Every model still substituted (7 / 5 / 1), so none passes. 12b's one substitution was "2026 compared with 2025", answered as January vs January.
- **PLAN/ASSESS:** exactly the v2 counts. Those prompts didn't change, so this is a replication.
- **Latency:** only 4b fits (77 s p50). qwen3:8b went from 178 s to 216 s because its SYNTH outputs got longer.
- **S6:** 1 / 4 / 8 of 20 completed. The digit problem is gone; what's left are genuine rule breaches.

What I learned: fixing the harness was worth it. RESOLVE went from near zero to near the bar, so the v2 numbers mostly measured my prompt. But the real model weakness underneath is substitution, and that's the one that matters for an analyst. The independent results review (`reviews/2026-11-16-s2l-v3-results-review.md`) changed no verdict. It found a second lenient VERIFY gap ("fell by -$110,283" passes), corrected several numbers I'd copied or mislabelled (the "420 calls" came from v2), and asked for the full failure breakdown, which is now in the write-up. Both lenient gaps only flatter the models. A pre-run review mostly catches defects that push scores down; leniency only shows up when you read the outputs that passed.
