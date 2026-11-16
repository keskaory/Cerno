# Cerno — Learning Guide

This guide is for understanding Cerno well enough to explain it, change it and defend it. It assumes you know Python, SQL and intro statistics. Each milestone adds a section. Every section points at real files and functions so you can read the code next to it.

**How to use it:** read a section, open the files it names, and run the commands. Each section ends with "check yourself" questions. If you can answer them without looking, you own that part.

Contents:
1. The big picture
2. M0: the tooling, and why each piece exists
3. S3: making query results reproducible (canonical hashing)
4. Local LLMs: what runs on your laptop and why the design looks the way it does
5. Glossary

---

## 1. The big picture

Cerno answers one kind of question: *"why did metric X change between period A and period B?"* The product idea is **analytical accountability**: every number in the report is computed by code from a stored query, and every claim carries an evidence label that code computes.

The architecture follows from one rule: **the language model never produces a number and never touches the data.** It only does the parts that need language:
- reading the question
- choosing which breakdowns are worth looking at
- writing sentences with blanks ("slots") that code fills in

```
question ──▶ RESOLVE (LLM: what metric? which months?)      → QuestionSpec, which you confirm
         ──▶ PLAN    (LLM: which dimensions?)               → a typed list of steps
         ──▶ EXECUTE (code: compiled SQL in a sandboxed worker, plus statistics)
         ──▶ SYNTHESIZE (LLM: sentences with {slots})       → claims
         ──▶ VERIFY  (code: every slot resolves, no stray digits, direction words match signs)
         ──▶ report  (code fills the slots with numbers from the evidence)
```

Why this split? Language models are good at language and unreliable at arithmetic and at remembering exact numbers. A report that is 95% right but has one invented number is worse than useless for a business decision. By construction, a Cerno number can only come from a query result.

Main design documents:
- `docs/ARCHITECTURE.md`: the full design
- `docs/DECISIONS.md`: what was decided, with alternatives
- `docs/RISKS.md`: what could go wrong

**Two modes (ARCH §5.5).** If no local model is good enough, Guided mode replaces the three LLM steps:
- RESOLVE becomes a form.
- PLAN becomes "check every dimension".
- SYNTHESIZE becomes fixed sentence templates.

The numbers and evidence are identical in both modes, which is a useful property to be able to explain.

---

## 2. M0: the tooling, and why each piece exists

| Piece | File | What it does | Why this one |
|---|---|---|---|
| **uv** | `backend/pyproject.toml`, `backend/uv.lock` | Installs Python packages and pins every version in a lockfile | Much faster than pip, and the lockfile means CI and your machine install identical versions. `uv sync --locked` fails if the lockfile is stale. |
| **Dependency groups** | `[dependency-groups]` in `pyproject.toml` | `dev` holds tools (ruff, mypy, pytest). `spikes` holds the libraries the throwaway experiments need. | The product package (`cerno`) has zero runtime dependencies so far. Spike libraries don't leak into the product. |
| **ruff** | `[tool.ruff]` | Linter and formatter | One tool replaces flake8, isort and black. The rule set (`E,F,W,I,B,UP,SIM,RUF`) catches real bugs (B = bugbear) without being noisy. |
| **mypy --strict** | `[tool.mypy]` | Static type checking | Strict mode forces type hints everywhere, which documents the code and catches mismatched data shapes before they run. |
| **pytest** | `backend/tests/` | Tests | `--strict-markers` turns a typo in `@pytest.mark.live` into an error instead of a silently skipped test. |
| **GitHub Actions** | `.github/workflows/ci.yml` | Runs everything above on Ubuntu for every PR | Production is Linux, and the worker sandbox only works on Linux. CI catches "works on my Windows machine" bugs. `permissions: contents: read` gives the workflow the least access it needs. |
| **gitleaks** | the `secrets` job in CI | Scans git history for leaked keys | Cheap insurance. It's free for personal accounts. |
| **.gitattributes** | `* text=auto eol=lf` | Forces Unix line endings | Windows git otherwise converts files to CRLF, which breaks shell scripts and makes diffs noisy. |

**Try it** (from `backend/`):

```bash
uv run ruff check . && uv run mypy cerno tests && uv run pytest
```

**Check yourself:**
1. What does `--locked` protect against in CI?
2. Why does the `cerno` package have no runtime dependencies yet, even though spikes use DuckDB?

---

## 3. S3: making query results reproducible

Code: `spikes/s3_repro/canon.py` (`canonical_hash`, `_norm`) and `spikes/s3_repro/run.py` (`run_hashes`, `open_locked`). Results: `docs/M1_SPIKES.md` → S3.

### The problem
Every piece of evidence stores a hash of its query result, so a later replay can prove it got the same answer. But "the same answer" is slippery:
- **Row order.** Without an `ORDER BY`, SQL returns rows in whatever order the engine finds convenient. With several threads, that order changes between runs.
- **Floating-point sums aren't associative.** `(a + b) + c` can differ from `a + (b + c)` in the last bits. With 4 threads, DuckDB splits a SUM into partial sums and adds them up in whatever order the threads finish. So `SUM(amount)` over a DOUBLE column gives slightly different results from run to run.
- **Ties.** `ORDER BY n DESC LIMIT 20`, when many rows share the same `n`, can return a different 20 rows each time.

### The solution: a canonical form
`canonical_hash(columns, rows)` hashes a normalized version of the result:
1. Every value goes through `_norm`, which tags it with its type (`f:`, `d:`, `i:` …). A DOUBLE `1.0` and a DECIMAL `1.00` then never collide.
2. Floats are written with **10 significant digits** (`f"{x:.10g}"`), which throws away the last-bit noise. `-0.0` becomes `0.0`.
3. Decimals are normalized, so `1.50` and `1.5` match.
4. The rows are **sorted**, which removes the row-order problem.
5. Column names and types go into the hash.

### Worked example
Suppose the query is `SELECT region, SUM(amount) FROM orders GROUP BY region`. At 4 threads, one run returns:

```
[('r3', 4012345.678901234), ('r1', 3998877.123456789)]
```

The next run returns:

```
[('r1', 3998877.1234567893), ('r3', 4012345.678901233)]
```

The order differs, and so do the last digits. `_norm` turns both into the same rows:

```
['s:r1', 'f:3998877.123'] and ['s:r3', 'f:4012345.679']
```

Sorted and hashed, the two runs give the same SHA-256. The raw hashes (`raw_hash`, which keeps the order and all digits) differ. That's exactly what S3 measured: at 4 threads, 50 runs gave 50 different raw hashes and 1 canonical hash.

### What rounding can't fix
Ties with LIMIT. At 4 threads, the ties query gave **50 different canonical hashes in 50 runs**, because DuckDB returned genuinely different rows each time. Rounding can't help, because the data itself differs. The fix belongs in the SQL: the compiler always appends the grouping keys to the `ORDER BY` (`ORDER BY n DESC, customer_id`), which makes the order total. The spike's `q4b_total_order_limit` query shows that this fix works.

### The failure mode to remember
Rounding to 10 significant digits can still flip at a boundary. If a true value sits right at `...4999999999|5`, noise can round it either way. It's rare, and the production config uses `threads=1`, where S3 saw no noise at all. Rounding is a second layer of protection, not the main one.

### A surprise
Two DuckDB files built from the same Parquet weren't byte-identical, even though every query on them matched. So a dataset version's fingerprint must hash the logical content (schemas plus canonical rows), not the file bytes (ARCH §3.1).

**Try it** (from `backend/`; takes about a minute):

```bash
uv run python ../spikes/s3_repro/run.py --rows 200000 --reps 10
```

**Check yourself:**
1. Why tag values with their type before hashing?
2. Why doesn't `threads=1` alone make `ORDER BY ... LIMIT` with ties safe across DuckDB versions?
3. What does a canonical hash *not* prove about two results?

---

## 4. Local LLMs: what runs on your laptop, and why the design looks the way it does

Code: `spikes/s2_local_llm/probe.py`. Decision: D9 and ADR-013 in `docs/DECISIONS.md`. Design: ARCH §5.2.

### Hardware basics
- A model's **parameters** (4B, 8B, 12B) are its weights. At "Q4" **quantization**, each weight is stored in about 4 bits instead of 16, which makes an 8B model about 5 GB instead of about 16 GB. Quality drops a little.
- The model has to sit in **VRAM** (your RTX 4050 has 6 GB) to run fast. Whatever doesn't fit gets offloaded to system RAM and the CPU, which is much slower. The KV cache, the model's memory of the prompt so far, grows with context length and also takes VRAM.
- The probe measured this directly:

  | Model | Share in VRAM | Decode speed |
  |---|---|---|
  | `gemma3:4b` | 100% | 49 tok/s |
  | `qwen3:8b` | 70% | 17 tok/s |
  | `gemma3:12b` | 37% | 7.5 tok/s |

  Two things cause the slowdown, and they're mixed together here:
  - **Bigger models do more work per token.** Even fully in VRAM, an 8B model is slower than a 4B one.
  - **Offloading to the CPU.** The part of the model in system RAM runs on the much slower CPU, so a lower VRAM share costs extra speed on top of size.

  Separating the two would mean running the same model at different offload levels.

### Structured output through constrained decoding
Ollama's `format` parameter takes a JSON schema and converts it into a **grammar**. At each step, the model is only allowed to pick tokens that keep the output valid against that grammar. So the output always parses as JSON with the right keys. In the probe, all three models gave valid JSON 12/12 times.

Two things to understand:
1. **Parsing is not correctness.** The JSON shape can be perfect while the content is wrong.
2. **The grammar doesn't enforce everything in the schema.** The probe asked for dates matching `^\d{4}-\d{2}-01$`, and a model returned `2025-10-31`. So Cerno validates every output with Pydantic, plus semantic checks, and retries once with the error message (ARCH §5.2).

### Why the model transcribes and code resolves
The probe tried three ways of asking for the question's periods, on the same 12 questions:

| What the model was asked to output | Who does the date arithmetic | `gemma3:4b` | `qwen3:8b` | `gemma3:12b` |
|---|---|---|---|---|
| half-open windows (`2025-10-01` to `2025-11-01`) | model | 2/12 | 3/12 | 8/12 |
| months (`2025-10`) | model, partly | 9/12 | 9/12 | 12/12 |
| transcription v1: `year`, `month` and `months_ago` all present | code | 4/12 | 7/12 | — |
| transcription v2: a one-of union | code | 5/12 | 7/12 | 12/12 |

**A lesson from row 3.** The first transcription schema let the model fill `month` *and* `months_ago` at once ("October, 1 month ago"). The resolver trusted `months_ago`, and the score dropped below the months schema. The fix is to make the schema have **exactly one way to say each thing**: a union with a `kind` field. A schema that allows contradictions will get them.

**But the union didn't rescue the small models.** They still mixed up "August" and "last month", and `qwen3:8b` turned "last month" into November 2024. For the 4–8B models the simple months schema did better (9/12). Only the 12B model got everything right, under both schemas, and it runs at about 13 s per call. Simpler schemas suit smaller models. S2L picks the schema per model on fresh questions.

The errors in the first row were arithmetic, not understanding: inclusive end dates (`10-31` instead of `11-01`), or picking 2024 for "August" with no year. That's the same lesson as "the model never writes numbers", applied to dates. Code turns a transcription into a month with `resolve_period()` in `probe.py`:
- "Last month" (`months_ago=1`) means the **latest complete month in the dataset**, not the calendar month. On Olist, which ends in 2018, "last month" must mean 2018-08.
- A month with no year means the most recent such month in the data.

### The provider interface
Every LLM call goes through one small protocol, `LLMProvider.generate(request) -> result` (ARCH §5.2):
- `OllamaProvider` talks to the local server.
- `ScriptedProvider` returns canned outputs. It's what makes the agent testable in CI, which has no GPU, and lets tests feed in deliberately broken outputs.

Swapping in a different model or vendor touches one class.

**Check yourself:**
1. Why does valid JSON from constrained decoding still need Pydantic validation?
2. Give two reasons the model transcribes periods instead of computing dates.
3. What breaks if a test suite calls the real model instead of `ScriptedProvider`?

---

## 5. Glossary

| Term | Meaning |
|---|---|
| **Canonical hash** | A hash of a normalized result (sorted rows, rounded floats, typed values) |
| **Constrained decoding** | Restricting which tokens the model may emit so the output follows a grammar |
| **Evidence** | A stored query or tool result: SQL, parameters, canonical preview and hash |
| **Guided mode** | Running Cerno with no LLM: a form for input, code templates for prose |
| **Half-open window** | `[start, end)`: includes the start and excludes the end, so consecutive months never overlap |
| **QuestionSpec** | The structured reading of a question (metric, comparison and baseline windows, dimensions) that you confirm before a run |
| **Quantization** | Storing weights in fewer bits (e.g. 4) to shrink the model |
| **Slot** | A placeholder in a claim that code fills from evidence, e.g. `{ev_12.rows[region=West].delta_pct}` |
| **VRAM** | GPU memory. The model runs fast only if it fits. |
