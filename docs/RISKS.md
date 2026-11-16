# Cerno — Risks

Status: **v1.1 draft** (2026-11-15). Revised after the architecture review and the two consistency checks. Each risk lists why it matters, likelihood (L) and impact (I), how to check it early, the mitigation, and the fallback. **Validated** means a spike or test has measured it. **Assumed** means we believe it but haven't checked.

| # | Risk | L | I | State |
|---|---|---|---|---|
| R1 | Queries run fine but answer the wrong question (wrong join, grain, filter or period) | Med | High | assumed mitigated |
| R2 | Statistical conclusions are overstated | Med | High | assumed mitigated |
| R3 | Untrusted SQL or files escape the sandbox, steal secrets or exhaust resources | Low | High | assumed mitigated |
| R4 | API spend runs away (abuse, races, underestimated cost per run) | Med | High | unvalidated |
| R5 | Reports are wrong but look verified (hallucination, injection steering) | Med | High | assumed mitigated |
| R6 | The agent loops or stalls | Low | Med | mitigated by design |
| R7 | Business definitions are ambiguous | Med | Med | mitigated by design |
| R8 | The eval can't tell good changes from bad ones | Med | High | assumed mitigated |
| R9 | The hosting setup doesn't hold up (memory, volume, SSE through the proxy) | Med | Med | unvalidated |
| R10 | Scope is too big for the time available | High | High | partly mitigated; timeline unknown |
| R11 | Results aren't reproducible (hash flakes) | Med | Med | unvalidated |
| R12 | Windows dev vs. Linux prod differences waste days | Med | Low | decision pending (D5) |
| R13 | The structured-output schema is too large or slow for typed steps with dynamic enums | Low | Med | unvalidated |

### R1 — Logically wrong SQL
- **Why it matters:** a wrong number looks exactly like a right one.
- **Mitigation:** every query is compiled; there's no freehand SQL in v1. Joins are N:1 star joins only. Ratio metrics aggregate each side separately. Time windows are half-open, the timezone is explicit, and `period_check` runs. Golden SQL tests check the compiler, and decompositions assert that they sum to the total.
- **What's left:** wrong metric definitions. User confirmation is the mitigation for those.
- **Fallback if the compiler can't express enough questions:** reintroduce freehand SQL as exploratory-only, with a runtime check that the result grain is correct.

### R2 — Overstated statistics
- **Mitigation:** period changes (for the total and for each segment) are judged by a one-step-ahead prediction interval from a linear trend fitted to k ≥ 12 prior months, instead of by row-level tests. BH/BY correction is applied across every p-value in the investigation. Steps added mid-run are labeled exploratory. Strength labels are computed by code. Group comparisons at the customer level are deferred until they can be calibrated.
- **Validation:** the calibration suite (about 200 seeds per PR, ≥ 1,000 nightly). This turns "assumed" into "validated" on our data generator, though not on real data.
- **What's left:** with 12–23 points the intervals are wide. Annual seasonality and curved growth break the linear-trend assumption. Calibration measures that failure on a seasonal scenario but doesn't fix it.
- **Fallback:** if a tool can't pass calibration, it returns descriptive output only.

### R3 — Sandbox escape
- **Mitigation:**
  - Worker subprocess with an empty environment, rlimits and SIGKILL on timeout.
  - Locked DuckDB config: external access off, extensions and secrets off, temp space capped, `threads=1`.
  - Only compiled SQL runs, and the API process never loads DuckDB.
- **Validate early:** spike S1 plus the security test suite.
- **Fallback:** a container per query (gVisor, Firecracker), at a large cost in latency.

### R4 — API spend
- **Mitigation:** a reservation before each call against three budgets (per run, per invite code, global daily). Per-stage `max_tokens`. At most 6 LLM calls per run. Rate limits per IP. Separate keys and spend limits for dev, eval and prod. Demo visitors only see replays.
- **Validate early:** spike S2 measures cost per stage on hand-written evidence, and S2b re-measures it on the M2 skeleton.
- **Fallback:** a cheaper model for RESOLVE and ASSESS (needs your approval; D4), or fewer ASSESS rounds.

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

### R9 — Hosting
- **Validate early:** spike S4 on the real target: a 1 GB machine with a volume, a 50 MB ingest, two concurrent queries, an SSE stream held through 6 minutes of silence with a heartbeat, a redeploy in the middle of a run, and peak memory recorded.
- **Fallback:** Railway Hobby, or a 2 GB machine.

### R10 — Scope
- **Mitigation:** narrowed to one question type and one domain, with a walking skeleton first and upload moved late.
- **Still open:** the timeline (D1). Without it, milestones can't be sized against a date.

### R11 — Reproducibility
- **Mitigation:** canonical result hashing, `threads=1`, DECIMAL money, UTC, deterministic functions only, and recorded versions and seeds.
- **Validate:** spike S3.

### R12 — Windows dev
- **Mitigation:** WSL2 (recommended) and CI on Linux. Worker rlimits are Linux-only, so native Windows dev would need a "no rlimits" mode that is never used in prod.

### R13 — Structured-output schema limits
- **Validate:** spike S2 also sends the step union with about 30 enum values and records whether it's accepted, plus latency and failure rate.
- **Fallback:** plain string fields validated by code after parsing.

## Spikes

Spikes are time-boxed and throwaway. They live in `spikes/` and are not shipped. Writing them requires implementation approval. Each one can run without code from later milestones; inputs it needs are hand-written.

| Spike | When | Question | Done when | Size |
|---|---|---|---|---|
| **S1** Worker sandbox | M1 | Does the worker setup block or kill the worker-side attacks in security groups 1–4? Covers the root supervisor spawning a worker, dropping it to uid `cerno-worker`, empty env, rlimits, `unshare(CLONE_NEWNET)`, the locked config, and kill/respawn requested over a control socket. A stub process stands in for the API. The validator (group 5) is tested in M2. Also: do `interrupt()` and spill behave as documented on read-only files, and does opening the same file twice with different configs in one process conflict? Tested on a hand-built DuckDB file. | Every attack has a recorded outcome, config adjusted, and the network-isolation approach confirmed or a fallback documented | 1 day |
| **S2** LLM cost, schema, cache | M1 | Using **hand-written evidence JSON** for 5 questions: tokens by type, cost, and latency for RESOLVE (30 questions at `low`, plus accuracy) and SYNTHESIZE (`medium`). Is the step-union structured-output schema accepted with about 30 enum values, and how long does the first call take? How many tokens is the dataset context, compared with the minimum cacheable length? | Provisional per-stage `max_tokens` and per-run cost estimate written to the journal | 1 day, < $10 |
| **S2b** Re-measure on the skeleton | M2 (task 2.10) | Real end-to-end cost and latency on the M2 skeleton | Budget caps confirmed or revised | 2 hours, < $5 |
| **S3** Hash determinism | M1 | Is the canonical hash stable across 50 runs, comparing threads=1 vs 4 and DOUBLE vs DECIMAL? | Hash policy confirmed | 2 hours |
| **S4** Hosting | M1 (needs your OK to create a Fly account) | Does the topology work on a 1 GB machine? Uses throwaway stubs: a script that loads a 50 MB CSV into DuckDB, and a fake SSE run that idles for 6 minutes with heartbeats and gets redeployed mid-run. Also checks whether `unshare` and the uid switch work there. | R9 checklist passes or a fallback is chosen | 0.5 day |
| **S6** Slot claims | M1 | Given hand-written evidence JSON from 3 scenarios, can the model write readable slot templates? Measure stray digits, invalid references, and readability over 20 reports. On the same reports, measure the error rate of a naive regex number check. | VERIFY design confirmed or adjusted | 0.5 day, ~$2 |

S5 ("playbook with no LLM") is no longer a spike. It became the no-LLM baseline in M3, because it needs the real playbook.

**Update (2026-11-15, M1 approval):**
- You asked for statistical calibration in M1, so a new spike, **S7** (calibration of `baseline_compare` + BH), joins it.
- **S4 is deferred** until just before M5.
- The exact pass/fail criteria and results for S1, S2, S3, S6 and S7 live in `M1_SPIKES.md`.
