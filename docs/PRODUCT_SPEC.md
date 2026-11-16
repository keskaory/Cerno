# Cerno — Product Spec

Status: **v1.1 draft** (2026-11-15). Revised after the architecture review and the consistency checks. Items marked `ASSUMPTION` are defaults that are still unconfirmed. Pending decisions are listed in `DECISIONS.md`.

## One-line description

Cerno investigates why a business metric changed and shows its work. Every number in its report is computed by code from a stored query, and every claim says how strong its evidence is.

## The problem

Text-to-SQL tools answer "what was revenue in October?" They don't answer "why did revenue drop?", which takes several queries, a decomposition across segments, and a judgment about whether the change is larger than normal variation. LLM tools that try to answer it tend to produce confident stories that aren't checked against anything.

## Who it's for

- **Product user:** `ASSUMPTION` an operator or founder at a small company with no analyst, who needs an explanation they can defend.
- **Most actual traffic:** recruiters and engineers looking at a portfolio project. They need to understand a full investigation within 2 minutes without spending any API money. The first screen is built for them.

## What's different

Driver decomposition already exists in BI tools, and LLM data analysis exists in notebook-style assistants (the competitor list still needs checking before we quote it publicly). Cerno's claim is the **combination**:

1. An LLM plans the investigation within a fixed playbook, and every step it adds is labeled.
2. The statistics are deterministic code that accounts for multiple testing. The LLM chooses analyses but never computes them.
3. Claims are verified mechanically. The model writes claim templates, code fills in every number from evidence, and a VERIFY step checks references, direction, coverage and wording.
4. Evidence strength comes from the analysis, not from the model's confidence:
   - **Measured:** exact, computed directly from the data.
   - **Beyond normal variation:** the month came in further from what its recent trend predicted than chance would explain, after correcting for how many segments were checked.
   - **Within normal variation:** inconclusive, reported together with the smallest change the data could have detected.
   - **Not enough history:** less than 12 months of comparable data, so only exact numbers are shown.
   - **Untested:** a hypothesis that was planned but never checked.
5. "Why" is answered as **what accounts for the change**, never as what caused it.
6. Published evaluation numbers compare Cerno to an unconstrained agent on the same questions.

## v1 scope

**In v1:**
- **One question type:** a period-over-period change in a metric and what accounts for it. Segment comparisons appear inside this type; they aren't a separate question type.
- **Bundled datasets:**
  - a seeded e-commerce dataset (demo plus eval), with metrics and dimensions defined in YAML
  - a semi-synthetic dataset built from the public Olist e-commerce data with injected effects
- **QuestionSpec confirmation:** Cerno shows how it read the question (metric, periods, dimensions) as editable chips before it runs. It never stops mid-run to ask.
- **Live run view:** the plan as a checklist, streamed steps with SQL and results, a cost and time display, and a cancel button.
- **Report:** a three-sentence answer, claims with strength badges, numbers that open the evidence behind them, an investigation timeline, caveats, and an appendix of claims that were dropped.
- **Public replays** of pre-run demo investigations, which cost nothing to view. Live runs need a per-person invite code.

**Before "v1 done", in later milestones:**
- Upload of CSV/Parquet files (≤ 50 MB, ≤ 10 tables) with a type review, key and join confirmation, and metric definitions that Cerno proposes and the user confirms.

**Deferred:**
- Other question types (standalone conversion/retention questions, "what changed that I should look at?")
- Anomaly detection, cohort retention, causal methods
- Warehouse connectors, multi-user organizations, freehand SQL, agent-written code
- Fiscal calendars (calendar months only, with an explicit timezone)

## User journeys

1. **First visit (recruiter):** open a demo report → read the answer → click a number to see its SQL and result → open the timeline to see one hypothesis that was dropped as within normal variation → watch the eval summary.
2. **Live run (invite code):** pick a dataset → ask a question → confirm or edit the QuestionSpec chips → watch the run stream → get the report.
3. **Bring your own data (later milestone):** upload → review types → confirm keys, joins and two or three metrics → ask.

## Success criteria

These are the starting targets. The real bar gets set after the baseline is measured.
- **Driver recall and precision** on planted effects: higher than the unconstrained agent baseline, with the paired difference's CI above zero.
- **Specificity on null scenarios:** ≥ 90% of null runs report no meaningful driver.
- **Unsupported claims before VERIFY:** measured and reported. After VERIFY, no unverified claim is shown as verified.
- **Cost and latency:** p50 ≤ $0.50 and ≤ 3 minutes per investigation, with a hard cap of $0.75 per run. These are provisional and get revised after spikes S2 and S2b.
- **Understanding:** 2–3 people unfamiliar with the project can follow the 2-minute replay path without help.

## Privacy and security expectations

- **What reaches Anthropic:** schema, metric definitions, column profiles, and **up to 50 aggregated result rows per step**, plus a few example values per column in profiles. Bulk tables are never sent. The privacy notice states this, names every processor (Anthropic, the hosting provider), and gives retention periods.
- `ASSUMPTION` In v1, only bundled data and public or synthetic uploads are expected. The "don't upload sensitive data" notice is a warning, not a control. Deletion and retention are built as if people will ignore it.
- **Read-only access** is enforced by the compute worker and the DuckDB configuration, not by prompts.

## Cost envelope

- **Hosting:** about $7–12/month (one small Fly.io machine with a volume).
- **API:** a development budget per month **[D6, D4]**. In production, a global daily cap plus per-invite-code budgets.
- **Full evaluation run:** priced from spike S2 before it's scheduled.
