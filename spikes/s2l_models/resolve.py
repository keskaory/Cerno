"""RESOLVE for S2L: two output schemas, code-side resolution, and scoring.

The model never does date arithmetic in the transcribe schema; in the months schema it
does some ("last month" -> YYYY-MM). Code resolves both to one status, then scores it
against the frozen accepted answers in questions.json.
"""

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

QUESTIONS = Path(__file__).parent / "questions.json"
MetricOut = Literal[
    "revenue", "orders_count", "aov", "active_customers", "unknown_metric", "unspecified"
]
Intent = Literal["explain_change", "other"]

METRIC_TEXT = """Metrics defined in this dataset:
- revenue: sum of order amounts
- orders_count: number of orders
- aov: average order value = revenue / orders_count
- active_customers: customers with at least one order in the month"""


class MonthsOut(BaseModel):
    intent: Intent
    metric: MetricOut
    comparison_month: str = Field(description="YYYY-MM, or 'unsupported'")
    baseline_month: str = Field(description="YYYY-MM, 'previous', or 'unsupported'")


class NamedMonth(BaseModel):
    kind: Literal["named_month"]
    month: int = Field(ge=1, le=12)
    year: int | None


class Relative(BaseModel):
    kind: Literal["relative"]
    months_ago: int = Field(ge=0)


class Unsupported(BaseModel):
    kind: Literal["unsupported"]


class PreviousMonth(BaseModel):
    kind: Literal["previous_month"]


class SameMonthLastYear(BaseModel):
    kind: Literal["same_month_last_year"]


class TranscribeOut(BaseModel):
    intent: Intent
    metric: MetricOut
    comparison: NamedMonth | Relative | Unsupported = Field(discriminator="kind")
    baseline: PreviousMonth | SameMonthLastYear | NamedMonth | Relative | Unsupported = Field(
        discriminator="kind"
    )


MONTHS_RULES = """Return JSON describing the question.
- intent: "other" if the request is not about explaining a change in a metric.
- metric: one of the defined metrics; "unknown_metric" if the question asks about a metric
  that is not defined; "unspecified" if it names no metric.
- comparison_month: the month the question asks about, as YYYY-MM. "Last month" means the
  latest complete month. If the question uses quarters, weeks, years or day ranges, write
  "unsupported".
- baseline_month: the month to compare against, as YYYY-MM; "previous" if the question names
  no baseline; "unsupported" for quarters, weeks, years or day ranges."""

TRANSCRIBE_RULES = """Return JSON describing the question. Copy periods exactly as the
question states them; never compute dates.
- intent: "other" if the request is not about explaining a change in a metric.
- metric: one of the defined metrics; "unknown_metric" if the question asks about a metric
  that is not defined; "unspecified" if it names no metric.
- comparison: a named month is {"kind": "named_month", "month": 1-12, "year": the year only if
  the question gives one, else null}. "last month" is {"kind": "relative", "months_ago": 1},
  "two months ago" is months_ago 2, "this month" is months_ago 0. Quarters, weeks, years or
  day ranges are {"kind": "unsupported"}.
- baseline: {"kind": "previous_month"} if the question names no baseline;
  {"kind": "same_month_last_year"} for "the same month last year"; otherwise the same forms
  as comparison."""


def load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    return data


def context_text(ctx: dict[str, Any]) -> str:
    lines = [
        f"Today is {ctx['today']}.",
        f"The dataset has daily data from {ctx['data_start']} to {ctx['data_end']}.",
        f"The latest complete month is {ctx['latest_complete']}.",
    ]
    if ctx["partial"]:
        lines.append(f"The month {ctx['partial']} is partial (data until {ctx['data_end']}).")
    lines.append("Cerno explains the change in one metric between two calendar months.")
    return "\n".join(lines) + "\n" + METRIC_TEXT


def system_prompt(ctx: dict[str, Any], schema: str) -> str:
    return context_text(ctx) + "\n\n" + (MONTHS_RULES if schema == "months" else TRANSCRIBE_RULES)


# --- month arithmetic on (year, month) -------------------------------------------------

YM = tuple[int, int]


def parse_ym(s: str) -> YM:
    m = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", s)
    if not m:
        raise ValueError(f"not a YYYY-MM month: {s!r}")
    return int(m[1]), int(m[2])


def fmt_ym(ym: YM) -> str:
    return f"{ym[0]:04d}-{ym[1]:02d}"


def shift(ym: YM, k: int) -> YM:
    """k months earlier (negative k = later)."""
    i = ym[0] * 12 + ym[1] - 1 - k
    return i // 12, i % 12 + 1


def most_recent(month: int, at_or_before: YM) -> YM:
    return (at_or_before[0] if month <= at_or_before[1] else at_or_before[0] - 1), month


# --- resolution -------------------------------------------------------------------------


def semantic_problem(out: BaseModel) -> str | None:
    """Errors code can detect without knowing the right answer; these trigger the retry."""
    if isinstance(out, MonthsOut):
        for name, v, extra in (
            ("comparison_month", out.comparison_month, {"unsupported"}),
            ("baseline_month", out.baseline_month, {"unsupported", "previous"}),
        ):
            if v not in extra:
                try:
                    parse_ym(v)
                except ValueError as e:
                    return f"{name}: {e}"
    return None


def resolve(out: MonthsOut | TranscribeOut, ctx: dict[str, Any]) -> dict[str, Any]:
    """Turn a model output into one status, the way the product's RESOLVE step would."""
    if out.intent == "other":
        return {"status": "invalid"}
    if out.metric == "unknown_metric":
        return {"status": "unknown_metric"}
    if out.metric == "unspecified":
        return {"status": "needs_metric"}
    latest = parse_ym(ctx["latest_complete"])
    try:
        if isinstance(out, MonthsOut):
            if "unsupported" in (out.comparison_month, out.baseline_month):
                return {"status": "unsupported_period"}
            comp = parse_ym(out.comparison_month)
            base = (
                shift(comp, 1) if out.baseline_month == "previous" else parse_ym(out.baseline_month)
            )
        else:
            if out.comparison.kind == "unsupported" or out.baseline.kind == "unsupported":
                return {"status": "unsupported_period"}
            comp = _resolve_period(out.comparison, latest)
            if isinstance(out.baseline, PreviousMonth):
                base = shift(comp, 1)
            elif isinstance(out.baseline, SameMonthLastYear):
                base = shift(comp, 12)
            elif isinstance(out.baseline, NamedMonth) and out.baseline.year is None:
                base = most_recent(out.baseline.month, shift(comp, 1))
            else:
                base = _resolve_period(out.baseline, latest)
    except ValueError:
        return {"status": "malformed"}
    start = parse_ym(ctx["data_start"][:7])
    partial = parse_ym(ctx["partial"]) if ctx["partial"] else None
    if comp == partial:
        return {"status": "partial_period"}
    if comp > latest or base > latest or comp < start or base < start:
        return {"status": "out_of_range"}
    if comp == base:
        return {"status": "malformed"}
    return {
        "status": "ok",
        "metric": out.metric,
        "comparison": fmt_ym(comp),
        "baseline": fmt_ym(base),
    }


def _resolve_period(p: NamedMonth | Relative, latest: YM) -> YM:
    if isinstance(p, Relative):
        # "last month" (1) is the latest complete month; "this month" (0) is the one after.
        return shift(latest, p.months_ago - 1)
    if p.year is not None:
        return p.year, p.month
    return most_recent(p.month, latest)


def score(result: dict[str, Any], accept: list[dict[str, Any]]) -> dict[str, bool]:
    correct = any(all(result.get(k) == v for k, v in a.items()) for a in accept)
    # A substitution answers a different question: a known metric and a concrete period where
    # none was acceptable. "ok" is one form; so are partial_period and out_of_range when the
    # question needed a refusal (unknown or unspecified metric, unsupported period, off-topic),
    # because code only reaches those statuses after accepting a metric and inventing a month.
    statuses = {a["status"] for a in accept}
    concrete = {"ok", "partial_period", "out_of_range"}
    if "ok" in statuses:
        substitution = False
    elif statuses & concrete:  # e.g. I02-I04: an "ok" here answers a different month
        substitution = result["status"] == "ok"
    else:
        substitution = result["status"] in concrete
    return {"correct": correct, "substitution": substitution}
