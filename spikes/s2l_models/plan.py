"""PLAN and ASSESS for S2L: a typed step union with per-dataset enums, plus semantic checks.

PLAN cannot drop confirmed dimensions (ARCH §5.1): it orders them, picks a crosstab pair, and
proposes up to 3 exploratory extras. ASSESS stops or adds up to 3 exploratory steps.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic import create_model as _create_model

# Models are built at runtime from each dataset's enums, which mypy can't follow.
create_model: Any = _create_model

STEP_KINDS = ("baseline_compare", "decompose_change", "describe_change", "crosstab")


@dataclass
class PlanContext:
    name: str
    metrics: list[str]
    dims: dict[str, list[str]]  # dimension -> segment values
    metric: str
    comparison: str
    baseline: str
    in_scope: list[str]
    profile: dict[str, dict[str, float]]  # dim -> segment -> descriptive % change
    evidence: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    # dim -> segment -> {"unexpected_pct": .., "q": ..} for ASSESS

    def segments(self) -> list[str]:
        return sorted({s for v in self.dims.values() for s in v})

    def enum_count(self) -> int:
        return len(self.metrics) + len(self.dims) + len(STEP_KINDS) + len(self.segments())


def lit(values: list[str]) -> Any:
    return Literal[tuple(values)]


def models_for(ctx: PlanContext) -> tuple[type[BaseModel], type[BaseModel]]:
    """Build the PLAN and ASSESS output models with this dataset's enums."""
    M, D, S = lit(ctx.metrics), lit(list(ctx.dims)), lit(ctx.segments())
    reason = (str, Field(max_length=200))
    baseline = create_model(
        "BaselineCompare",
        kind=(Literal["baseline_compare"], ...),
        metric=(M, ...),
        dimension=(D, ...),
        segment=(S | None, None),
        reason=reason,
    )
    decompose = create_model(
        "DecomposeChange",
        kind=(Literal["decompose_change"], ...),
        metric=(M, ...),
        dimension=(D, ...),
        segment=(S | None, None),
        reason=reason,
    )
    describe = create_model(
        "DescribeChange",
        kind=(Literal["describe_change"], ...),
        metric=(M, ...),
        segment=(S | None, None),
        reason=reason,
    )
    crosstab = create_model(
        "Crosstab",
        kind=(Literal["crosstab"], ...),
        metric=(M, ...),
        dimension_a=(D, ...),
        dimension_b=(D, ...),
        reason=reason,
    )
    step = baseline | decompose | describe | crosstab
    pair = create_model("Pair", dimension_a=(D, ...), dimension_b=(D, ...))
    plan = create_model(
        "PlanOut",
        dimension_order=(list[D], ...),  # type: ignore[valid-type]
        crosstab=(pair | None, None),
        extras=(list[step], Field(max_length=3)),  # type: ignore[valid-type]
    )
    assess = create_model(
        "AssessOut",
        action=(Literal["stop", "add_steps"], ...),
        steps=(list[step], Field(max_length=3)),  # type: ignore[valid-type]
        reason=reason,
    )
    return plan, assess


def _step_key(s: Any) -> tuple[Any, ...]:
    d = s.model_dump()
    d.pop("reason", None)
    return tuple(sorted(d.items()))


def _playbook_keys(ctx: PlanContext) -> set[tuple[Any, ...]]:
    keys = set()
    for dim in ctx.in_scope:
        for kind in ("baseline_compare", "decompose_change"):
            keys.add(
                tuple(
                    sorted(
                        {
                            "kind": kind,
                            "metric": ctx.metric,
                            "dimension": dim,
                            "segment": None,
                        }.items()
                    )
                )
            )
    keys.add(
        tuple(sorted({"kind": "describe_change", "metric": ctx.metric, "segment": None}.items()))
    )
    return keys


def _check_steps(
    ctx: PlanContext, steps: list[Any], taken_pair: frozenset[str] | None = None
) -> str | None:
    seen: set[tuple[Any, ...]] = set()
    playbook = _playbook_keys(ctx)
    for s in steps:
        k = _step_key(s)
        dim = getattr(s, "dimension", None)
        seg = getattr(s, "segment", None)
        if k in playbook:
            return f"step {s.kind} on {dim or 'total'} duplicates a playbook step"
        # The playbook already runs baseline_compare on every segment of in-scope dimensions.
        if s.kind == "baseline_compare" and s.metric == ctx.metric and dim in ctx.in_scope:
            return f"baseline_compare on {dim}={seg} duplicates a playbook step"
        if s.kind == "crosstab":
            pair = frozenset((s.dimension_a, s.dimension_b))
            if len(pair) == 1:
                return "crosstab needs two different dimensions"
            if pair == taken_pair:
                return "crosstab duplicates the planned crosstab"
            k = ("crosstab", s.metric, pair)  # order of the two dimensions doesn't matter
            dims = list(pair)
        else:
            dims = [dim] if dim is not None else []
        if any(d not in ctx.in_scope for d in dims):
            return f"steps may only use in-scope dimensions {ctx.in_scope}"
        if k in seen:
            return "duplicate step"
        seen.add(k)
        if seg is not None and dim is not None and seg not in ctx.dims[dim]:
            return f"segment {seg} does not belong to dimension {dim}"
        if seg is not None and dim is None and not any(seg in ctx.dims[d] for d in ctx.in_scope):
            return f"segment {seg} is not in an in-scope dimension"
    return None


def plan_problem(ctx: PlanContext, out: Any) -> str | None:
    if sorted(out.dimension_order) != sorted(ctx.in_scope):
        return f"dimension_order must be a permutation of {ctx.in_scope}"
    pair = None
    if out.crosstab is not None:
        a, b = out.crosstab.dimension_a, out.crosstab.dimension_b
        if a == b or a not in ctx.in_scope or b not in ctx.in_scope:
            return "crosstab must pair two different in-scope dimensions"
        pair = frozenset((a, b))
    return _check_steps(ctx, out.extras, pair)


def assess_problem(ctx: PlanContext, out: Any) -> str | None:
    if out.action == "stop" and out.steps:
        return "action stop must have no steps"
    if out.action == "add_steps" and not out.steps:
        return "action add_steps needs at least one step"
    return _check_steps(ctx, out.steps)


PLAN_RULES = """You plan an investigation of why a metric changed. A fixed playbook already
runs describe_change on the total, plus baseline_compare and decompose_change on every
in-scope dimension. You may not remove any of that. Return JSON with:
- dimension_order: all in-scope dimensions, most promising first
- crosstab: a pair of in-scope dimensions worth crossing, or null
- extras: up to 3 additional exploratory steps (not duplicates of the playbook, only
  in-scope dimensions), each with a short reason. Use [] if nothing extra is worth checking."""

ASSESS_RULES = """You review the evidence so far. Return JSON with action "stop" if the
evidence is enough, or "add_steps" with up to 3 exploratory steps that would check something
the playbook did not (not duplicates of the playbook, only in-scope dimensions). Give a short
reason."""


def plan_prompt(ctx: PlanContext) -> tuple[str, str]:
    data = {
        "metrics": ctx.metrics,
        "dimensions": ctx.dims,
        "question": {
            "metric": ctx.metric,
            "comparison": ctx.comparison,
            "baseline": ctx.baseline,
            "in_scope_dimensions": ctx.in_scope,
        },
        "descriptive_pct_change_by_segment": ctx.profile,
    }
    return PLAN_RULES, json.dumps(data, indent=1)


def assess_prompt(ctx: PlanContext) -> tuple[str, str]:
    data = {
        "question": {
            "metric": ctx.metric,
            "comparison": ctx.comparison,
            "baseline": ctx.baseline,
            "in_scope_dimensions": ctx.in_scope,
        },
        "segments": {d: ctx.dims[d] for d in ctx.in_scope},
        "baseline_compare_results": ctx.evidence,
        "note": "q < 0.05 means beyond normal variation after multiple-testing correction",
    }
    return ASSESS_RULES, json.dumps(data, indent=1)


REGIONS = ["North", "South", "East", "West"]
CHANNELS = ["web", "app", "store"]
CATEGORIES = ["apparel", "home", "beauty", "electronics", "toys", "grocery"]
DEVICES = ["desktop", "mobile"]


def _ev(segs: list[str], flagged: dict[str, float]) -> dict[str, dict[str, float]]:
    return {
        s: (
            {"unexpected_pct": flagged[s], "q": 0.004}
            if s in flagged
            else {"unexpected_pct": round(1.5 - i * 0.7, 1), "q": round(0.4 + i * 0.1, 2)}
        )
        for i, s in enumerate(segs)
    }


def contexts() -> list[PlanContext]:
    """5 datasets x 2 variants = 10 PLAN and 10 ASSESS calls. Enum counts run from 16 to 42."""
    big = {
        "region": REGIONS,
        "channel": CHANNELS,
        "category": CATEGORIES,
        "device": DEVICES,
        "tier": ["new", "returning"],
        "country": ["US", "CA", "MX"],
        "payment": ["card", "wallet"],
        "promo": ["yes", "no"],
    }
    big_metrics = ["revenue", "orders_count", "aov", "active_customers", "items_sold", "refunds"]
    small = {"region": REGIONS, "channel": CHANNELS}
    mid = {"region": REGIONS, "channel": CHANNELS, "category": CATEGORIES}
    out: list[PlanContext] = []
    specs = [
        ("shop_small", ["revenue", "orders_count", "aov"], small, "revenue", ["region", "channel"]),
        (
            "shop_mid",
            ["revenue", "orders_count", "aov", "active_customers"],
            mid,
            "orders_count",
            ["region", "channel", "category"],
        ),
        ("shop_aov", ["revenue", "orders_count", "aov"], mid, "aov", ["region", "category"]),
        ("shop_big", big_metrics, big, "revenue", ["region", "channel", "category", "device"]),
        ("shop_big2", big_metrics, big, "active_customers", ["region", "tier", "country"]),
    ]
    for name, metrics, dims, metric, scope in specs:
        for variant in (0, 1):
            hot_dim = scope[variant % len(scope)]
            hot_seg = dims[hot_dim][variant]
            profile = {
                d: {
                    s: (-14.0 if (d, s) == (hot_dim, hot_seg) else round(-1.0 + 0.5 * i, 1))
                    for i, s in enumerate(dims[d])
                }
                for d in scope
            }
            evidence = {
                d: _ev(dims[d], {hot_seg: -11.5} if (variant == 0 and d == hot_dim) else {})
                for d in scope
            }
            out.append(
                PlanContext(
                    f"{name}_v{variant}",
                    metrics,
                    dims,
                    metric,
                    "2025-10",
                    "2025-09",
                    scope,
                    profile,
                    evidence,
                )
            )
    return out
