# Cerno

An AI analyst that investigates why a business metric changed. Every number in its report is rendered by code from stored evidence, and every claim says how strong its evidence is ("analytical accountability").

## Current phase

**M0–M1 approved (2026-11-15), then paused by D9 ($0, local-first). The revised v2.0 plan needs the user's review before M1 continues. M2 is not approved.** Stop after M1 for the checkpoint in `docs/M1_SPIKES.md`. Git workflow: D8 in `docs/DECISIONS.md`. Dev environment: WSL2 (D5).

## Read first

1. `docs/IMPLEMENTATION_PLAN.md`: milestones, current status, what's blocked
   - `docs/M1_SPIKES.md`: spike criteria and results
2. `docs/DECISIONS.md`: ADRs plus the pending decisions (D1–D10; D9 = $0 local-first)
3. `docs/ARCHITECTURE.md`: system design (v2.0, local-first)
4. `docs/RISKS.md`: risks and spikes; `docs/EVALUATION.md`: test and eval strategy
5. `docs/LEARNING_GUIDE.md`: how the system works, for the developer
6. `docs/DEVELOPMENT_JOURNAL.md`: chronological reasoning; add entries for real decisions and discoveries
7. `docs/reviews/`: review logs with findings and resolutions

## Constraints that don't bend

- The security boundary is the **worker process** (separate uid, empty env, rlimits, no network, killed and respawned by a root supervisor) plus the locked DuckDB config. The API process never imports duckdb. Prompts are never a control.
- The model never calls tools or writes numbers. It returns typed plans and slot-template claims; code executes the plans and renders the numbers.
- Only compiled SQL runs in the product in v1. The one exception is the ReAct eval baseline, which runs its SQL through the same worker with the validator in freehand mode.
- Statistics are deterministic tools that must pass the calibration suite. Period changes are judged by a prediction interval from the prior ≥ 12 months, never with row-level tests.
- **$0 (D9).** No paid APIs, hosting or sign-ups, and no paid fallbacks. The LLM is local (Ollama) behind the `LLMProvider` interface. Tests and CI use `ScriptedProvider`.
- LLM time is reserved before each call against the run's budget.
- The local model is chosen by S2L and pinned by digest. Don't switch models or download new ones without the user's approval.
- If no local model is good enough, ship Guided mode and say so. Never pretend.
- Maintain `docs/LEARNING_GUIDE.md` every milestone. The user wants to understand and own the code.

## Conventions

- Writing (docs, commits): direct, plain, technically precise. No marketing tone.
- Code: simple, typed, small modules; comments only for non-obvious reasoning, invariants, security, or statistical assumptions.
- Commits: conventional (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), focused, after tests and review. **No `Co-Authored-By` or other AI attribution in commits or PR descriptions.**
- Each milestone: implement → test, lint, types → independent code review → fix → update docs and journal → commit.

## Commands

Run from `backend/`, inside WSL:

```bash
uv sync --group dev --group spikes
uv run ruff check . && uv run ruff format --check .
uv run mypy cerno tests
uv run pytest -m "not live"          # live = calls the local Ollama model, run manually
```
