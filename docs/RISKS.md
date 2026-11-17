# Cerno — Risks

Status: **v2.0 draft** (2026-11-15). v1.1 came from the architecture review. v2.0 applies D9 ($0, local-first): R4 and R9 change and R14–R17 are new. Each risk lists why it matters, likelihood (L) and impact (I), how to check it early, the mitigation, and the fallback. **Validated** means a spike or test has measured it. **Assumed** means we believe it but haven't checked.

| # | Risk | L | I | State |
|---|---|---|---|---|
| R1 | Queries run fine but answer the wrong question (wrong join, grain, filter or period) | Med | High | assumed mitigated |
| R2 | Statistical conclusions are overstated | **High** | High | **S7 failed** (2026-11-16); log levels adopted (D11), power criterion open (D12) |
| R3 | Untrusted SQL or files escape the sandbox, steal secrets or exhaust resources | Low | High | assumed mitigated |
| R4 | ~~API spend runs away~~ (retired by D9: no paid APIs). Replaced by R14. | — | — | retired |
| R5 | Reports are wrong but look verified (hallucination, injection steering) | Med | High | assumed mitigated |
| R6 | The agent loops or stalls | Low | Med | mitigated by design |
| R7 | Business definitions are ambiguous | Med | Med | mitigated by design |
| R8 | The eval can't tell good changes from bad ones | Med | High | assumed mitigated |
| R9 | ~~Hosting doesn't hold up~~ (retired by D9: local app plus a static showcase) | — | — | retired |
| R10 | Scope is too big for the time available | High | High | partly mitigated; timeline unknown |
| R11 | Results aren't reproducible (hash flakes) | Med | Med | unvalidated |
| R12 | Windows dev vs. Linux prod differences waste days | Med | Low | decision pending (D5) |
| R13 | The structured-output schema is too large or slow for typed steps with dynamic enums | Low | Med | **measured** (S2L): schemas up to 42 enum values parsed 100% with thinking off |
| R14 | No local model that fits 6 GB of VRAM is good enough for PLAN and SYNTHESIZE | **High** | High | **occurred** (S2L v2 and v3: no model passes); Guided mode is the default |
| R15 | Local inference is too slow for a good live demo or an affordable-in-time benchmark | High | Med | **measured**: v3 only gemma3:4b fits 180 s (77 s); qwen3:8b 216 s, 12b 369 s |
| R16 | Grammar-constrained output parses, but its content is wrong (ignored schema keywords, plausible but wrong enums) | High | Med | **confirmed** (S2L): 420/420 parsed, every failure semantic |
| R17 | Results depend on one machine's hardware and drivers, so others can't reproduce them | Med | Med | assumed mitigated |
| R18 | Our own benchmark harness mis-measures (prompt or VERIFY defects), so scores reflect the harness, not the model | **High** | High | **occurred** in v1 and v2; v3 found only two lenient VERIFY gaps (see below) |

### R1 — Logically wrong SQL
- **Why it matters:** a wrong number looks exactly like a right one.
- **Mitigation:** every query is compiled; there's no freehand SQL in v1. Joins are N:1 star joins only. Ratio metrics aggregate each side separately. Time windows are half-open, the timezone is explicit, and `period_check` runs. Golden SQL tests check the compiler, and decompositions assert that they sum to the total.
- **What's left:** wrong metric definitions. User confirmation is the mitigation for those.
- **Fallback if the compiler can't express enough questions:** reintroduce freehand SQL as exploratory-only, with a runtime check that the result grain is correct.

### R2 — Overstated statistics
- **Mitigation:** period changes (for the total and for each segment) are judged by a one-step-ahead prediction interval from a linear trend fitted to k ≥ 12 prior months, instead of by row-level tests. BH/BY correction is applied across every p-value in the investigation. Steps added mid-run are labeled exploratory. Strength labels are computed by code. Group comparisons at the customer level are deferred until they can be calibrated.
- **Validation:** the calibration suite (about 200 seeds per PR, ≥ 1,000 nightly). This turns "assumed" into "validated" on our data generator, though not on real data.
- **What's left:** with 12–23 points the intervals are wide. Annual seasonality and curved growth break the linear-trend assumption. Calibration measures that failure on a seasonal scenario but doesn't fix it.
- **S7 result (2026-11-16): failed.** In levels, false positives with growth were 0.107 and coverage 0.925, because noise scales with the level. Log levels fix this in 15 of 19 tested conditions, including moderate and compounding growth, but fail for steep linear growth (5–8%/mo) or linear decline, where the series curves on the log scale. Power at d = 3 is 0.37 (levels) or 0.49 (log) against 0.80, a bar an ideal test can't reach with 12–23 monthly points and 8 tests (ideal ≈0.35). The MDE for 80% power is about 4 residual SDs, about a 28% drop in a region. See `M1_SPIKES.md` → M1 results.
- **Fallback:** if a tool can't pass calibration, it returns descriptive output only.

### R3 — Sandbox escape
- **Mitigation:**
  - Worker subprocess with an empty environment, rlimits and SIGKILL on timeout.
  - Locked DuckDB config: external access off, extensions and secrets off, temp space capped, `threads=1`.
  - Only compiled SQL runs, and the API process never loads DuckDB.
- **Validate early:** spike S1 plus the security test suite.
- **Fallback:** a container per query (gVisor, Firecracker), at a large cost in latency.

### R4 — API spend (retired)
D9 removed paid APIs. The reservation mechanism survives with LLM seconds as its unit (ARCH §5.3).

### R5 — Wrong report that looks verified
- **Mitigation:**
  - Claims are templates whose slots are filled by code, so the model writes no numbers.
  - VERIFY checks that references resolve, that direction words match signs, that the main driver is covered, and that the text has no URLs or banned words.
  - Filters can only come from the QuestionSpec or the metric definitions.
  - Instruction-like text in the data is flagged.
  - Everything renders as plain text.
- **What's left:** a technically supported but misleading framing. The eval checks driver precision and an LLM judge looks at wording.

### R6 — Agent loops
A playbook drives the run, ASSESS is bounded (≤ 2 rounds, ≤ 3 added steps each), duplicate steps are rejected, and caps on calls, steps, time and errors are enforced.

### R7 — Ambiguous definitions
Bundled datasets ship with confirmed metrics. The QuestionSpec is confirmed before the run starts. Definitions are shown next to every result.

### R8 — Eval blind spots
A realistic data generator, a calibration suite, seeds with CIs, driver precision and null specificity, an unconstrained-agent baseline, and a semi-synthetic real dataset. Known limits are written down.

### R9 — Hosting (retired)
There's no hosted service. The static showcase is files on GitHub Pages, and the local app runs on the developer's machine.

### R10 — Scope
- **Mitigation:** narrowed to one question type and one domain, with a walking skeleton first and upload moved late.
- **Still open:** the timeline (D1). Without it, milestones can't be sized against a date.

### R11 — Reproducibility
- **Mitigation:** canonical result hashing, `threads=1`, DECIMAL money, UTC, deterministic functions only, and recorded versions and seeds.
- **Validate:** spike S3.

### R12 — Windows dev
- **Mitigation:** WSL2 (recommended) and CI on Linux. Worker rlimits are Linux-only, so native Windows dev would need a "no rlimits" mode that is never used in prod.

### R13 — Structured-output schema limits
- **Validate:** S2L sends the PLAN step union with about 30 enum values through Ollama's `format` and records parse and validation rates and latency.
- **Fallback:** split PLAN into two smaller calls (pick dimensions, then parameters), or use plain string fields validated by code.

### R14 — Local model quality (new)
- **Why it matters:** a 4–8B model may pick bad dimensions, or write claims that keep failing VERIFY. Either would push runs into `incomplete_coverage`, or produce reports that read badly.
- **What the probe showed (12 questions, not a gate):**
  - Asking for half-open date windows directly scored 2–8/12.
  - Asking for months scored 9–12/12.
  - All three models produced valid JSON 12/12 times.
- **Mitigation:**
  - The model does no arithmetic, no dates and no SQL.
  - Each call is one small schema with short context.
  - Every output is validated, with one retry.
  - Strength, outcome and coverage come from code.
  - VERIFY is deterministic.
- **Validate:** S2L and S6.
- **Measured (S2L v2 and v3, 2026-11-16): occurred.** No model passed either run. With the harness fixed (v3), the best RESOLVE score was 36/40 (gemma3:12b), and every model still substituted concrete months for some unsupported periods. The best S6 result was 8/20 completed, against 18 needed. Guided mode is the v1 default.
- **Model doesn't follow the schema's intent** (seen in the probe: filling `month` and `months_ago` together): use one-of unions so there's exactly one way to say each thing.
- **Fallback:** Guided mode (ARCH §5.5): the full playbook, code-written prose, and the same numbers and evidence, with the limitation stated in the README. That's an honest fallback, not a workaround.

### R15 — Local latency (new)
- **Probe numbers:**
  - `gemma3:4b`: fully on the GPU, about 49 tok/s and about 4 s per RESOLVE call.
  - `qwen3:8b`: about 70% on the GPU at a 4k context, about 17 tok/s and about 7 s per call.
  - `gemma3:12b`: 37% on the GPU, about 7.5 tok/s and about 14 s per call.
- **Mitigation:** small outputs, thinking off, a single GPU queue, one run at a time, and the model kept loaded between calls (`keep_alive`).
- **Validate:** S2L's projected time per investigation, then S2b on the skeleton.
- **Measured (S2L v3):** projected time per investigation p50 77 s (4b), 216 s (qwen3:8b), 369 s (12b), against 180 s. Only gemma3:4b fits, and it's the least accurate.
- **Fallback:** a smaller model for RESOLVE only, or fewer ASSESS rounds.

### R16 — Constrained decoding isn't validation (new)
In the probe, Ollama's grammar didn't enforce a JSON-schema `pattern`. **Mitigation:** Pydantic and semantic validation on every output, one retry with the error, then a typed failure (ARCH §5.2). Tests feed malformed and semantically wrong outputs through `ScriptedProvider`.

### R17 — Hardware-specific results (new)
Published eval numbers come from one GPU. **Mitigation:**
- the model is pinned by digest, with `temperature=0` and a fixed seed
- hardware is recorded in every results file
- re-run agreement is measured
- `evals/run.py --model` lets anyone re-run the suite
- the README says which machine produced the numbers

### R18 — Harness validity (new, 2026-11-16)
It happened twice in M1:
- **Before running:** an independent review found v1 scoring too lax. v1 was stopped and replaced by v2 before any output was read.
- **After running:** the v2 RESOLVE prompt never named `"explain_change"`, the SYNTH evidence had no slot for the period label, and the SYNTH prompt offered a `number` format that VERIFY never accepts. All three pushed scores down. One VERIFY gap ("significance") was too lenient.

**Mitigation:**
- an independent review of the harness *before* each run
- a smoke run of 3–5 items per stage on one model, whose outputs are read by hand before the full run (it scores nothing, so it can't bias thresholds)
- a held-out question set for any harness revision written after seeing results
- every prompt names every enum value it expects

**Applied to v3 (2026-11-16):** pre-run review (6 fixes), freeze with hashes, and a smoke run read by hand that found no defect. The post-run review then found two VERIFY gaps that are too *lenient*: a literal "%" after a percent slot, and signed values after "fell" ("fell by -$110,283"). Neither changed a verdict, but they show that a pre-run review mostly catches harness defects that make scores *too low*. Leniency shows up only when someone reads the outputs that passed.

**What's left:** a harness can still be wrong in ways nobody thought to check. Raw outputs are kept (`spikes/*/out/`, gitignored) so the scoring can always be audited.

## Spikes

Spikes are time-boxed and throwaway. They live in `spikes/` and are not shipped. Writing them requires implementation approval. Each one can run without code from later milestones; inputs it needs are hand-written.

| Spike | When | Question | Done when | Size |
|---|---|---|---|---|
| **S1** Worker sandbox | M1 | Does the worker setup block or kill the worker-side attacks in security groups 1–4? Covers the root supervisor spawning a worker, dropping it to uid `cerno-worker`, empty env, rlimits, `unshare(CLONE_NEWNET)`, the locked config, and kill/respawn requested over a control socket. A stub process stands in for the API. The validator (group 5) is tested in M2. Also: do `interrupt()` and spill behave as documented on read-only files, and does opening the same file twice with different configs in one process conflict? Tested on a hand-built DuckDB file. | Every attack has a recorded outcome, config adjusted, and the network-isolation approach confirmed or a fallback documented | 1 day |
| **S2L** Local model selection (replaces S2) | M1 | Which free local model fits the dev machine and passes RESOLVE, schema, latency and S6 criteria? | D4 decided, or Guided mode chosen as default | 1 day, $0 |
| **S2b** Re-measure on the skeleton | M2 (task 2.10) | Real end-to-end latency on the M2 skeleton with the local model | Time limits confirmed or revised | 2 hours, $0 |
| **S3** Hash determinism | M1 | Is the canonical hash stable across 50 runs, comparing threads=1 vs 4 and DOUBLE vs DECIMAL? | Hash policy confirmed | 2 hours |
| ~~**S4** Hosting~~ | — | Dropped under D9 | — | — |
| **S6** Slot claims | M1 | Given hand-written evidence JSON from 3 scenarios, can the model write readable slot templates? Measure stray digits, invalid references, and readability over 20 reports. On the same reports, measure the error rate of a naive regex number check. Runs on the S2L candidates. | VERIFY design confirmed or adjusted | 0.5 day, $0 |

S5 ("playbook with no LLM") is no longer a spike. It became the no-LLM baseline in M3, because it needs the real playbook.

**Update (2026-11-15, M1 approval):**
- You asked for statistical calibration in M1, so a new spike, **S7** (calibration of `baseline_compare` + BH), joins it.
- S4 was deferred until just before M5. (D9 later dropped it.)
- The exact pass/fail criteria and results live in `M1_SPIKES.md`.

**Update (2026-11-15, D9):**
- S2 becomes **S2L**, which selects a local model, and S6 runs on local models.
- S4 is dropped, because nothing is hosted.
- S3 has passed on Windows; its WSL re-run is pending.
