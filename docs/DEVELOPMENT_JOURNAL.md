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
