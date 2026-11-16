# Cerno

An AI analyst that investigates why a business metric changed. Every number in its report is rendered by code from stored evidence, and every claim says how strong its evidence is ("analytical accountability").

## Current phase

**M0–M1 approved (2026-11-15). M2 is not approved.** Stop after M1 for the checkpoint in `docs/M1_SPIKES.md`. Git workflow: D8 in `docs/DECISIONS.md`. Dev environment: WSL2 (D5).

## Read first

1. `docs/IMPLEMENTATION_PLAN.md`: milestones, current status, what's blocked
   - `docs/M1_SPIKES.md`: spike criteria and results
2. `docs/DECISIONS.md`: ADRs plus the pending decisions (D1–D8)
3. `docs/ARCHITECTURE.md`: system design (v1.1)
4. `docs/RISKS.md`: risks and spikes; `docs/EVALUATION.md`: test and eval strategy
5. `docs/DEVELOPMENT_JOURNAL.md`: chronological reasoning; add entries for real decisions and discoveries
6. `docs/reviews/`: review logs with findings and resolutions

## Constraints that don't bend

- The security boundary is the **worker process** (separate uid, empty env, rlimits, no network, killed and respawned by a root supervisor) plus the locked DuckDB config. The API process never imports duckdb. Prompts are never a control.
- The model never calls tools or writes numbers. It returns typed plans and slot-template claims; code executes the plans and renders the numbers.
- Only compiled SQL runs in the product in v1. The one exception is the ReAct eval baseline, which runs its SQL through the same worker with the validator in freehand mode.
- Statistics are deterministic tools that must pass the calibration suite. Period changes are judged by a prediction interval from the prior ≥ 12 months, never with row-level tests.
- LLM spend is reserved before each call against the run, invite-code, and global budgets.
- Model: `claude-opus-5-5`, with effort set explicitly per stage. Don't switch models without the user's approval.

## Conventions

- Writing (docs, commits): direct, plain, technically precise. No marketing tone.
- Code: simple, typed, small modules; comments only for non-obvious reasoning, invariants, security, or statistical assumptions.
- Commits: conventional (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), focused, after tests and review.
- Each milestone: implement → test, lint, types → independent code review → fix → update docs and journal → commit.

## Commands

None yet. M0 adds `uv run pytest`, `uv run ruff check`, and `uv run mypy`.
