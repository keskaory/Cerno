"""Preliminary local-LLM probe (not the full spike). Measures, per Ollama model:
- RESOLVE-style structured output: schema validity, exact-match accuracy, latency
- text-to-SQL (only used by the ReAct baseline): execution accuracy against gold queries
- decode speed and how much of the model sits in VRAM

Usage (from backend/):  uv run python ../spikes/s2_local_llm/probe.py gemma3:4b [more models...]
Only talks to the local Ollama server on localhost:11434. Costs nothing.
"""

import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import duckdb

OLLAMA = "http://localhost:11434"
OUT = Path(__file__).parent / "out"
RESULTS = Path(__file__).parent / "results"

CONTEXT = """Dataset: e-commerce orders, daily data from 2024-01-01 to 2025-10-31.
Today is 2025-11-05. Metrics: revenue (sum of order amount), orders_count, aov (revenue / orders).
Dimensions: region (North, South, East, West), channel (web, app, store).
Windows are half-open [start, end) on whole calendar months. If the question names no
baseline, the baseline is the month before the comparison month. If the question asks about a
metric that is not listed, set metric to "unknown_metric"."""

RESOLVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "metric": {"enum": ["revenue", "orders_count", "aov", "unknown_metric"]},
        "comparison_start": {"type": "string", "pattern": r"^\d{4}-\d{2}-01$"},
        "comparison_end": {"type": "string", "pattern": r"^\d{4}-\d{2}-01$"},
        "baseline_start": {"type": "string", "pattern": r"^\d{4}-\d{2}-01$"},
        "baseline_end": {"type": "string", "pattern": r"^\d{4}-\d{2}-01$"},
    },
    "required": [
        "metric",
        "comparison_start",
        "comparison_end",
        "baseline_start",
        "baseline_end",
    ],
    "additionalProperties": False,
}

MONTH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "metric": {"enum": ["revenue", "orders_count", "aov", "unknown_metric"]},
        "comparison_month": {"type": "string", "pattern": r"^\d{4}-\d{2}$"},
        "baseline_month": {"type": "string", "pattern": r"^\d{4}-\d{2}$"},
    },
    "required": ["metric", "comparison_month", "baseline_month"],
    "additionalProperties": False,
}
MONTH_NOTE = "\nAnswer with months as YYYY-MM; code turns them into date windows."


# One-of union: exactly one way to name a period, so the model can't fill two at once.
# (v1 required year, month and months_ago together; gemma3:4b then filled month AND
# months_ago and scored 4/12. See the journal, 2026-11-15.)
_NAMED = {
    "type": "object",
    "properties": {
        "kind": {"const": "named_month"},
        "month": {"type": "integer", "minimum": 1, "maximum": 12},
        "year": {"type": ["integer", "null"]},
    },
    "required": ["kind", "month", "year"],
    "additionalProperties": False,
}
_RELATIVE = {
    "type": "object",
    "properties": {
        "kind": {"const": "relative"},
        "months_ago": {"type": "integer", "minimum": 1},
    },
    "required": ["kind", "months_ago"],
    "additionalProperties": False,
}
_UNSUPPORTED = {
    "type": "object",
    "properties": {"kind": {"const": "unsupported"}},
    "required": ["kind"],
    "additionalProperties": False,
}
_DEFAULT = {
    "type": "object",
    "properties": {"kind": {"const": "previous_month"}},
    "required": ["kind"],
    "additionalProperties": False,
}
TRANSCRIBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "metric": {"enum": ["revenue", "orders_count", "aov", "unknown_metric"]},
        "comparison": {"anyOf": [_NAMED, _RELATIVE, _UNSUPPORTED]},
        "baseline": {"anyOf": [_DEFAULT, _NAMED, _RELATIVE, _UNSUPPORTED]},
    },
    "required": ["metric", "comparison", "baseline"],
    "additionalProperties": False,
}
TRANSCRIBE_NOTE = """
Copy each period exactly as the question states it; never compute dates.
- A named month: kind "named_month", month 1-12, year only if the question gives one.
- "last month" is kind "relative" with months_ago=1; "two months ago" is months_ago=2.
- If the question names no baseline period, baseline is kind "previous_month".
- Quarters, weeks, years or anything else: kind "unsupported"."""
# "Last month" means the latest complete month in the DATA, not the calendar (review M2).
LATEST_COMPLETE = (2025, 10)


def _shift(y: int, m: int, k: int) -> tuple[int, int]:
    i = y * 12 + (m - 1) - k
    return i // 12, i % 12 + 1


def resolve_period(p: dict[str, Any], anchor: tuple[int, int]) -> tuple[int, int]:
    """Code, not the model, turns a transcribed period into a calendar month.

    anchor is the latest complete month in the data (for the comparison period) or the
    comparison month (for the baseline). A month with no year is the most recent such month
    at or before the anchor.
    """
    kind = p["kind"]
    if kind == "relative":
        return _shift(*anchor, int(p["months_ago"]) - 1)
    if kind == "named_month":
        month = int(p["month"])
        if p.get("year"):
            return int(p["year"]), month
        return (anchor[0] if month <= anchor[1] else anchor[0] - 1), month
    raise ValueError(f"unsupported period: {p}")


def window_dict(metric: str, c: tuple[str, str], b: tuple[str, str]) -> dict[str, Any]:
    return {
        "metric": metric,
        "comparison_start": c[0],
        "comparison_end": c[1],
        "baseline_start": b[0],
        "baseline_end": b[1],
    }


def month_window(month: str) -> tuple[str, str]:
    y, m = map(int, month.split("-"))
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y:04d}-{m:02d}-01", f"{ny:04d}-{nm:02d}-01"


OCT = ("2025-10-01", "2025-11-01")
SEP = ("2025-09-01", "2025-10-01")
AUG = ("2025-08-01", "2025-09-01")
# (question, expected metric, comparison window, baseline window); None windows = not checked
RESOLVE_CASES: list[tuple[str, str, tuple[str, str] | None, tuple[str, str] | None]] = [
    ("Why did revenue fall in October compared to September?", "revenue", OCT, SEP),
    ("Why did revenue drop last month?", "revenue", OCT, SEP),
    ("What happened to order volume in September vs August?", "orders_count", SEP, AUG),
    ("Why is average order value down last month?", "aov", OCT, SEP),
    (
        "Explain the change in AOV in October 2025 compared with October 2024",
        "aov",
        OCT,
        ("2024-10-01", "2024-11-01"),
    ),
    ("Why did churn go up last month?", "unknown_metric", None, None),
    ("Why did sales decline in August?", "revenue", AUG, ("2025-07-01", "2025-08-01")),
    ("How come we got fewer orders last month than the month before?", "orders_count", OCT, SEP),
    ("Why did customer satisfaction drop in October?", "unknown_metric", None, None),
    ("Revenue in Oct vs Aug: what drove the difference?", "revenue", OCT, AUG),
    ("Why did the number of orders rise two months ago?", "orders_count", SEP, AUG),
    ("Why was gross margin lower in September?", "unknown_metric", None, None),
]

SQL_SYSTEM = """You write one DuckDB SQL query. Table: orders(order_id BIGINT, region VARCHAR,
channel VARCHAR, order_date DATE, amount DECIMAL(18,2)). Regions: North, South, East, West.
Channels: web, app, store. Return only the SQL, no explanation, no code fences."""
SQL_CASES: list[tuple[str, str]] = [
    (
        "Total revenue in October 2025.",
        "SELECT SUM(amount) FROM orders WHERE order_date >= '2025-10-01' "
        "AND order_date < '2025-11-01'",
    ),
    (
        "Revenue by region in September 2025.",
        "SELECT region, SUM(amount) FROM orders WHERE order_date >= '2025-09-01' "
        "AND order_date < '2025-10-01' GROUP BY region",
    ),
    (
        "Number of orders per channel in 2025.",
        "SELECT channel, COUNT(*) FROM orders WHERE order_date >= '2025-01-01' "
        "AND order_date < '2026-01-01' GROUP BY channel",
    ),
    (
        "Average order value in the West region in October 2025.",
        "SELECT SUM(amount) / COUNT(*) FROM orders WHERE region = 'West' "
        "AND order_date >= '2025-10-01' AND order_date < '2025-11-01'",
    ),
    (
        "Monthly revenue for 2025, ordered by month.",
        "SELECT date_trunc('month', order_date) AS m, SUM(amount) FROM orders "
        "WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' GROUP BY m ORDER BY m",
    ),
    (
        "Change in revenue from September to October 2025 for each region.",
        "SELECT region, SUM(CASE WHEN order_date >= '2025-10-01' AND order_date < '2025-11-01' "
        "THEN amount ELSE 0 END) - SUM(CASE WHEN order_date >= '2025-09-01' AND "
        "order_date < '2025-10-01' THEN amount ELSE 0 END) FROM orders GROUP BY region",
    ),
]


def chat(model: str, system: str, user: str, fmt: dict[str, Any] | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "options": {"temperature": 0, "num_ctx": 4096, "seed": 1},
        "think": False,
    }
    if fmt is not None:
        body["format"] = fmt
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        out: dict[str, Any] = json.loads(r.read())
    out["_wall_s"] = time.perf_counter() - t0
    return out


def sample_db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE orders AS
        SELECT i AS order_id,
               ['North','South','East','West'][1 + CAST(hash(i) % 4 AS BIGINT)] AS region,
               ['web','app','store'][1 + CAST(hash(i * 7) % 3 AS BIGINT)] AS channel,
               DATE '2024-01-01' + CAST(hash(i * 13) % 670 AS INTEGER) AS order_date,
               CAST(5 + (hash(i * 31) % 20000) / 100.0 AS DECIMAL(18,2)) AS amount
        FROM range(20000) t(i)""")
    return con


def numbers(con: duckdb.DuckDBPyConnection, sql: str) -> list[list[str]] | None:
    """Numeric cells per row (rounded to cents), rows sorted. None if the SQL fails.

    Labels (region names, month formats) and column order are ignored on purpose: the probe
    checks whether the numbers are right, not whether the SQL looks like the gold query.
    """
    try:
        rows = con.execute(sql).fetchall()
    except Exception:
        return None
    out = []
    for row in rows:
        nums = sorted(
            f"{float(v):.2f}" for v in row if isinstance(v, int | float) or hasattr(v, "as_tuple")
        )
        out.append(nums)
    return sorted(out)


def sql_correct(got: list[list[str]] | None, gold: list[list[str]] | None) -> bool:
    """Same row count, and each gold row's numbers appear in the matching result row."""
    if got is None or gold is None or len(got) != len(gold):
        return False
    return all(set(g) <= set(r) for g, r in zip(gold, got, strict=True))


def strip_sql(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip().rstrip(";")


def probe(model: str) -> dict[str, Any]:
    chat(model, "Reply OK.", "OK?", None)  # load the model; not timed below
    ps = json.loads(urllib.request.urlopen(f"{OLLAMA}/api/ps").read())
    loaded: dict[str, Any] = next((m for m in ps["models"] if m["name"] == model), {})
    size, vram = loaded.get("size", 0), loaded.get("size_vram", 0)

    # windows: model writes half-open dates; months: model writes YYYY-MM;
    # transcribe: model copies what the question says and code resolves it.
    modes: dict[str, list[dict[str, Any]]] = {"windows": [], "months": [], "transcribe": []}
    schemas = {"windows": RESOLVE_SCHEMA, "months": MONTH_SCHEMA, "transcribe": TRANSCRIBE_SCHEMA}
    notes = {"windows": "", "months": MONTH_NOTE, "transcribe": TRANSCRIBE_NOTE}
    for q, metric, comp, base in RESOLVE_CASES:
        for mode, results in modes.items():
            schema = schemas[mode]
            r = chat(model, CONTEXT + notes[mode], q, schema)
            try:
                got = json.loads(r["message"]["content"])
                valid = set(got) == set(schema["required"])
                if valid and mode == "months":
                    c, bl = (
                        month_window(got["comparison_month"]),
                        month_window(got["baseline_month"]),
                    )
                    got = window_dict(got["metric"], c, bl)
                elif valid and mode == "transcribe":
                    cy, cm = resolve_period(got["comparison"], LATEST_COMPLETE)
                    base_p = got["baseline"]
                    if base_p["kind"] == "previous_month":
                        by, bm = _shift(cy, cm, 1)
                    elif base_p["kind"] == "relative":  # "two months ago" counts from the data
                        by, bm = resolve_period(base_p, LATEST_COMPLETE)
                    else:  # a named baseline month is the most recent one before comparison
                        by, bm = resolve_period(base_p, _shift(cy, cm, 1))
                    c = month_window(f"{cy:04d}-{cm:02d}")
                    bl = month_window(f"{by:04d}-{bm:02d}")
                    got = window_dict(got["metric"], c, bl) | {"raw": got}
            except (json.JSONDecodeError, ValueError, TypeError, KeyError):
                got, valid = {}, False
            ok = valid and got.get("metric") == metric
            if ok and comp is not None and base is not None:
                ok = (got["comparison_start"], got["comparison_end"]) == comp and (
                    got["baseline_start"],
                    got["baseline_end"],
                ) == base
            results.append(
                {
                    "q": q,
                    "valid": valid,
                    "correct": ok,
                    "got": got,
                    "wall_s": r["_wall_s"],
                    "prompt_tokens": r.get("prompt_eval_count", 0),
                    "out_tokens": r.get("eval_count", 0),
                    "tok_s": r.get("eval_count", 0) / max(r.get("eval_duration", 1), 1) * 1e9,
                }
            )
    resolve = modes["windows"]

    con = sample_db()
    sql: list[dict[str, Any]] = []
    for q, gold in SQL_CASES:
        r = chat(model, SQL_SYSTEM, q, None)
        text = strip_sql(r["message"]["content"])
        got_n = numbers(con, text)
        sql.append(
            {
                "q": q,
                "sql": text,
                "runs": got_n is not None,
                "correct": sql_correct(got_n, numbers(con, gold)),
                "wall_s": r["_wall_s"],
            }
        )

    walls = sorted(x["wall_s"] for x in resolve)
    show = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(f"{OLLAMA}/api/show", data=json.dumps({"model": model}).encode())
        ).read()
    )
    version = json.loads(urllib.request.urlopen(f"{OLLAMA}/api/version").read())["version"]
    return {
        "model": model,
        "digest": loaded.get("digest"),
        "quantization": show.get("details", {}).get("quantization_level"),
        "remote_host": show.get("remote_host"),  # must be None: a local model, not a cloud proxy
        "ollama_version": version,
        "num_ctx": 4096,
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16],
        "size_gb": round(size / 1e9, 2),
        "vram_fraction": round(vram / size, 2) if size else None,
        "resolve_valid": f"{sum(x['valid'] for x in resolve)}/{len(resolve)}",
        "resolve_correct": f"{sum(x['correct'] for x in resolve)}/{len(resolve)}",
        **{f"{k}_correct": f"{sum(x['correct'] for x in v)}/{len(v)}" for k, v in modes.items()},
        "resolve_p50_s": round(walls[len(walls) // 2], 2),
        "resolve_max_s": round(walls[-1], 2),
        "decode_tok_s_median": round(sorted(x["tok_s"] for x in resolve)[len(resolve) // 2], 1),
        "sql_runs": f"{sum(x['runs'] for x in sql)}/{len(sql)}",
        "sql_correct": f"{sum(x['correct'] for x in sql)}/{len(sql)}",
        "detail": {**modes, "sql": sql},
    }


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for model in sys.argv[1:]:
        res = probe(model)
        (OUT / f"{model.replace(':', '_')}.json").write_text(json.dumps(res, indent=2))
        summary = {k: v for k, v in res.items() if k != "detail"}
        # Small summaries are committed (out/ is gitignored) so the numbers in the docs
        # can be traced to a probe version, a model digest and an Ollama version.
        RESULTS.mkdir(exist_ok=True)
        (RESULTS / f"{model.replace(':', '_')}.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary))


if __name__ == "__main__":
    main()
