"""Tests for the S2L/S6 harness itself, so a benchmark score reflects the model, not a bug."""

from typing import Any

import client as c
import plan as pl
import pytest
import resolve as rv
import synth as sy

DATA = rv.load(rv.QUESTIONS_V2)  # frozen v2 set; context-specific tests below use it
DATA_V3 = rv.load()
BOTH = [(d, q) for d in (DATA, DATA_V3) for q in d["questions"]]


def gold_months(a: dict[str, Any]) -> rv.MonthsOut:
    """The months-schema output a perfect model would give for an accepted answer."""
    base = {
        "intent": "explain_change",
        "metric": "revenue",
        "comparison_month": "2025-10",
        "baseline_month": "previous",
    }
    s = a["status"]
    if s == "ok":
        base |= {
            "metric": a["metric"],
            "comparison_month": a["comparison"],
            "baseline_month": a["baseline"],
        }
    elif s == "invalid":
        base["intent"] = "other"
    elif s == "unknown_metric":
        base["metric"] = "unknown_metric"
    elif s == "needs_metric":
        base["metric"] = "unspecified"
    elif s == "unsupported_period":
        base["comparison_month"] = "unsupported"
    else:
        return None  # type: ignore[return-value]  # out_of_range / partial depend on the month
    return rv.MonthsOut.model_validate(base)


@pytest.mark.parametrize(
    ("data", "q"), BOTH, ids=[f"{'v3' if d is DATA_V3 else 'v2'}-{q['id']}" for d, q in BOTH]
)
def test_every_question_is_answerable(data: dict[str, Any], q: dict[str, Any]) -> None:
    ctx = data["contexts"][q["ctx"]]
    answerable = False
    for a in q["accept"]:
        out = gold_months(a)
        if out is not None:
            result = rv.resolve(out, ctx)
            assert rv.score(result, q["accept"])["correct"], (q["id"], result)
            answerable = True
    if not answerable:  # out_of_range / partial: try the month a literal reading gives
        months = (
            {"I02": "2029-02", "I03": "2026-05", "I04": "2024-03"}
            if data is DATA_V3
            else {"I02": "2031-03", "I03": "2025-03", "I04": "2023-12"}
        )
        out = rv.MonthsOut(
            intent="explain_change",
            metric="revenue",
            comparison_month=months[q["id"]],
            baseline_month="previous",
        )
        assert rv.score(rv.resolve(out, ctx), q["accept"])["correct"]


@pytest.mark.parametrize("data", [DATA, DATA_V3], ids=["v2", "v3"])
def test_question_set_shape(data: dict[str, Any]) -> None:
    cats: dict[str, int] = {}
    for q in data["questions"]:
        cats[q["cat"]] = cats.get(q["cat"], 0) + 1
    assert len(data["questions"]) == 40
    assert cats["unknown_metric"] >= 8 and cats["unsupported_period"] >= 4
    assert len({q["id"] for q in data["questions"]}) == 40


def test_v3_has_same_category_counts_and_no_reused_wording() -> None:
    def counts(d: dict[str, Any]) -> dict[str, int]:
        out: dict[str, int] = {}
        for q in d["questions"]:
            out[q["cat"]] = out.get(q["cat"], 0) + 1
        return out

    assert counts(DATA) == counts(DATA_V3)
    assert not {q["q"] for q in DATA["questions"]} & {q["q"] for q in DATA_V3["questions"]}


@pytest.mark.parametrize(
    ("ctx", "comp", "base", "want"),
    [
        (
            "A",
            {"kind": "relative", "months_ago": 1},
            {"kind": "previous_month"},
            ("2025-10", "2025-09"),
        ),
        (
            "B",
            {"kind": "named_month", "month": 12, "year": None},
            {"kind": "named_month", "month": 11, "year": None},
            ("2025-12", "2025-11"),
        ),
        (
            "B",
            {"kind": "named_month", "month": 1, "year": None},
            {"kind": "named_month", "month": 12, "year": None},
            ("2026-01", "2025-12"),
        ),
        (
            "A",
            {"kind": "relative", "months_ago": 1},
            {"kind": "relative", "months_ago": 13},
            ("2025-10", "2024-10"),
        ),
        (
            "A",
            {"kind": "named_month", "month": 10, "year": None},
            {"kind": "named_month", "month": 8, "year": None},
            ("2025-10", "2025-08"),
        ),
    ],
)
def test_transcribe_resolution(
    ctx: str, comp: dict[str, Any], base: dict[str, Any], want: tuple[str, str]
) -> None:
    out = rv.TranscribeOut.model_validate(
        {"intent": "explain_change", "metric": "revenue", "comparison": comp, "baseline": base}
    )
    r = rv.resolve(out, DATA["contexts"][ctx])
    assert (r["comparison"], r["baseline"]) == want


def test_this_month_in_partial_context() -> None:
    out = rv.TranscribeOut.model_validate(
        {
            "intent": "explain_change",
            "metric": "revenue",
            "comparison": {"kind": "relative", "months_ago": 0},
            "baseline": {"kind": "previous_month"},
        }
    )
    assert rv.resolve(out, DATA["contexts"]["C"])["status"] == "partial_period"


def test_substitution_detected() -> None:
    assert rv.score({"status": "ok", "metric": "revenue"}, [{"status": "unknown_metric"}])[
        "substitution"
    ]
    assert not rv.score({"status": "needs_metric"}, [{"status": "unknown_metric"}])["substitution"]


def test_months_semantic_problem() -> None:
    bad = rv.MonthsOut(
        intent="explain_change",
        metric="revenue",
        comparison_month="2025-10-31",
        baseline_month="previous",
    )
    assert rv.semantic_problem(bad)


# --- PLAN / ASSESS ---------------------------------------------------------------------


def test_plan_contexts_and_enums() -> None:
    ctxs = pl.contexts()
    assert len(ctxs) == 10
    assert max(x.enum_count() for x in ctxs) >= 30


def test_plan_semantics() -> None:
    ctx = pl.contexts()[0]
    plan_m, assess_m = pl.models_for(ctx)
    ok = plan_m.model_validate(
        {"dimension_order": ["channel", "region"], "crosstab": None, "extras": []}
    )
    assert pl.plan_problem(ctx, ok) is None
    missing = plan_m.model_validate({"dimension_order": ["region"], "crosstab": None, "extras": []})
    assert pl.plan_problem(ctx, missing)
    dup = plan_m.model_validate(
        {
            "dimension_order": ["region", "channel"],
            "crosstab": None,
            "extras": [
                {
                    "kind": "decompose_change",
                    "metric": ctx.metric,
                    "dimension": "region",
                    "segment": None,
                    "reason": "x",
                }
            ],
        }
    )
    assert "duplicates" in (pl.plan_problem(ctx, dup) or "")
    wrong_seg = plan_m.model_validate(
        {
            "dimension_order": ["region", "channel"],
            "crosstab": None,
            "extras": [
                {
                    "kind": "baseline_compare",
                    "metric": "aov",
                    "dimension": "region",
                    "segment": "web",
                    "reason": "x",
                }
            ],
        }
    )
    assert pl.plan_problem(ctx, wrong_seg)
    stop_with_steps = assess_m.model_validate(
        {
            "action": "stop",
            "reason": "x",
            "steps": [{"kind": "describe_change", "metric": "aov", "segment": None, "reason": "x"}],
        }
    )
    assert pl.assess_problem(ctx, stop_with_steps)


def test_plan_schema_inlines_for_ollama() -> None:
    plan_m, _ = pl.models_for(pl.contexts()[6])
    assert "$ref" not in str(c.inline_refs(plan_m.model_json_schema()))


# --- S6 VERIFY -----------------------------------------------------------------------------


def all_scenarios() -> list[dict[str, Any]]:
    return [
        sy.scenario(k, s)
        for k, s in [("a", 0), ("a", 3), ("b", 10), ("b", 13), ("c", 20), ("c", 25)]
    ]


@pytest.mark.parametrize("sc", all_scenarios(), ids=lambda s: f"{s['scenario']}{s['seed']}")
def test_guided_mode_passes_verify(sc: dict[str, Any]) -> None:
    out = sy.guided(sc)
    assert sy.verify(out, sc["evidence"]) == []
    text = " ".join(sy.render(cl, sc["evidence"]) for cl in [out.headline, *out.claims])
    assert "{" not in text


def test_scenarios_have_expected_flags() -> None:
    a, b, cc = sy.scenario("a", 2), sy.scenario("b", 12), sy.scenario("c", 22)
    assert sy.flagged(a["evidence"]) == {a["planted"]}
    assert len(sy.flagged(b["evidence"])) == 2
    assert sy.flagged(cc["evidence"]) == set()
    assert sy.outcome(cc["evidence"]) == "no_meaningful_change"


def claim(t: str, *slots: tuple[str, str, str]) -> sy.Claim:
    return sy.Claim(template=t, slots=[sy.Slot(name=n, ref=r, fmt=f) for n, r, f in slots])  # type: ignore[arg-type]


def test_verify_catches_each_rule() -> None:
    sc = sy.scenario("a", 0)
    ev, w = sc["evidence"], sc["planted"]
    seg = (
        ("s", f"ev_base_region.rows[segment={w}].segment", "label"),
        ("x", f"ev_base_region.rows[segment={w}].unexpected_pct", "pct_abs"),
    )
    good = sy.SynthOut(
        headline=claim("{s} fell by {x}.", *seg),
        claims=[claim("{s} came in {x} below normal.", *seg)],
    )
    assert sy.verify(good, ev) == []
    cases = {
        "digits": sy.SynthOut(
            headline=claim("{s} fell 20 percent {x}.", *seg),
            claims=[
                claim(
                    "ok",
                )
            ],
        ),
        "bad ref": sy.SynthOut(
            headline=claim("{s} fell by {x}.", *seg),
            claims=[claim("{y} rose.", ("y", "ev_nope.delta", "money"))],
        ),
        "direction": sy.SynthOut(headline=claim("{s} rose by {x}.", *seg), claims=[claim("ok")]),
        "coverage": sy.SynthOut(
            headline=claim("Revenue fell by {d}.", ("d", "ev_total.delta", "money_abs")),
            claims=[claim("ok")],
        ),
        "causal": sy.SynthOut(
            headline=claim("{s} fell by {x} because of weather.", *seg), claims=[claim("ok")]
        ),
        "placeholder": sy.SynthOut(
            headline=claim("{s} fell by {x} {z}.", *seg), claims=[claim("ok")]
        ),
    }
    for name, out in cases.items():
        assert sy.verify(out, ev), name


def test_driver_wording_rejected_on_null() -> None:
    sc = sy.scenario("c", 21)
    out = sy.SynthOut(
        headline=claim(
            "{s} drove the change.", ("s", "ev_dec_region.rows[segment=West].segment", "label")
        ),
        claims=[claim("ok")],
    )
    assert any("driver" in e for e in sy.verify(out, sc["evidence"]))


def test_render_formats() -> None:
    assert sy.render_value(-12345.6, "money") == "-$12,346"
    assert sy.render_value(-12345.6, "money_abs") == "$12,346"
    assert sy.render_value(-0.1234, "pct") == "-12.3%"
    assert sy.render_value(0.42, "money") == "$0.42"


def test_stats_helpers() -> None:
    assert c.pct([1, 2, 3, 4], 0.5) == 2
    lo, hi = c.wilson(36, 40)
    assert 0.76 < lo < 0.78 and hi > 0.95


# --- regressions for the 2026-11-15 review (docs/reviews/2026-11-15-m1-spikes-review.md) ----


def test_substitution_counts_invented_out_of_range_or_partial_months() -> None:
    assert rv.score({"status": "partial_period"}, [{"status": "unsupported_period"}])[
        "substitution"
    ]
    assert rv.score({"status": "out_of_range"}, [{"status": "unknown_metric"}])["substitution"]
    assert not rv.score({"status": "out_of_range"}, [{"status": "out_of_range"}])["substitution"]
    assert rv.score({"status": "ok"}, [{"status": "partial_period"}])["substitution"]


def test_transcribe_same_month_last_year() -> None:
    out = rv.TranscribeOut.model_validate(
        {
            "intent": "explain_change",
            "metric": "revenue",
            "comparison": {"kind": "relative", "months_ago": 1},
            "baseline": {"kind": "same_month_last_year"},
        }
    )
    r = rv.resolve(out, DATA["contexts"]["A"])
    assert (r["comparison"], r["baseline"]) == ("2025-10", "2024-10")


def _extra(**kw: Any) -> dict[str, Any]:
    return {"segment": None, "reason": "x", **kw}


def test_plan_rejects_out_of_scope_and_hidden_duplicates() -> None:
    ctx = next(x for x in pl.contexts() if x.name == "shop_aov_v0")  # in scope: region, category
    plan_m, assess_m = pl.models_for(ctx)

    def plan(extras: list[dict[str, Any]], crosstab: Any = None) -> str | None:
        out = plan_m.model_validate(
            {"dimension_order": ctx.in_scope, "crosstab": crosstab, "extras": extras}
        )
        return pl.plan_problem(ctx, out)

    channel = _extra(kind="decompose_change", metric="aov", dimension="channel")
    assert "in-scope" in (plan([channel]) or "")
    seg_test = _extra(kind="baseline_compare", metric="aov", dimension="region", segment="North")
    assert "duplicates" in (plan([seg_test]) or "")
    pair = {"dimension_a": "region", "dimension_b": "category"}
    flipped = {
        "kind": "crosstab",
        "metric": "aov",
        "dimension_a": "category",
        "dimension_b": "region",
        "reason": "x",
    }
    assert "crosstab" in (plan([flipped], pair) or "")
    ok = _extra(kind="decompose_change", metric="aov", dimension="region", segment="North")
    assert plan([ok]) is None
    assess = assess_m.model_validate({"action": "add_steps", "reason": "x", "steps": [channel]})
    assert pl.assess_problem(ctx, assess)


def test_verify_rejects_segment_name_in_text() -> None:
    sc = sy.scenario("a", 0)  # planted North
    x = ("x", "ev_base_region.rows[segment=North].unexpected_pct", "pct_abs")
    out = sy.SynthOut(
        headline=claim("East drove the decline, falling {x}.", x), claims=[claim("ok")]
    )
    assert any("label slots" in e for e in sy.verify(out, sc["evidence"]))


def test_verify_driver_label_must_be_flagged() -> None:
    sc = sy.scenario("a", 0)
    out = sy.SynthOut(
        headline=claim(
            "{s} drove the decline, falling {x}.",
            ("s", "ev_base_region.rows[segment=East].segment", "label"),
            ("x", "ev_base_region.rows[segment=North].unexpected_pct", "pct_abs"),
        ),
        claims=[claim("ok")],
    )
    assert any("driver" in e for e in sy.verify(out, sc["evidence"]))


def test_verify_direction_per_clause_and_new_words() -> None:
    sc = sy.scenario("b", 10)
    ev = sc["evidence"]
    rows = ev["ev_base_region"]["rows"]
    up = max(rows, key=lambda r: r["unexpected_pct"])["segment"]
    dn = min(rows, key=lambda r: r["unexpected_pct"])["segment"]

    def two(a: str, b: str) -> sy.SynthOut:
        return sy.SynthOut(
            headline=claim(
                "{a} fell by {x} while {b} rose by {y}.",
                ("a", f"ev_base_region.rows[segment={a}].segment", "label"),
                ("x", f"ev_base_region.rows[segment={a}].unexpected_pct", "pct_abs"),
                ("b", f"ev_base_region.rows[segment={b}].segment", "label"),
                ("y", f"ev_base_region.rows[segment={b}].unexpected_pct", "pct_abs"),
            ),
            claims=[claim("ok")],
        )

    assert not [e for e in sy.verify(two(dn, up), ev) if "says" in e]
    assert [e for e in sy.verify(two(up, dn), ev) if "says" in e]
    above = sy.SynthOut(
        headline=claim(
            "{s} came in {x} above normal.",
            ("s", f"ev_base_region.rows[segment={dn}].segment", "label"),
            ("x", f"ev_base_region.rows[segment={dn}].unexpected_pct", "pct_abs"),
        ),
        claims=[claim("ok")],
    )
    assert [e for e in sy.verify(above, ev) if "says up" in e]


def test_verify_format_must_fit_field() -> None:
    sc = sy.scenario("a", 0)
    out = sy.SynthOut(
        headline=claim(
            "{s} was off by {x}.",
            ("s", "ev_base_region.rows[segment=North].segment", "label"),
            ("x", "ev_base_region.rows[segment=North].unexpected_pct", "money_abs"),
        ),
        claims=[claim("ok")],
    )
    assert any("cannot be shown" in e for e in sy.verify(out, sc["evidence"]))


def test_behind_is_not_driver_wording() -> None:
    assert (
        sy.driver_claims(
            sy.SynthOut(
                headline=claim("Revenue fell behind the level predicted."), claims=[claim("ok")]
            )
        )
        == 0
    )


def test_guided_suppresses_share_when_offsetting() -> None:
    for seed in range(20, 26):
        sc = sy.scenario("c", seed)
        out = sy.guided(sc)
        refs = [s.ref for cl in [out.headline, *out.claims] for s in cl.slots]
        if sy.offsetting(sc["evidence"]["ev_dec_region"]):
            assert not any("share_of_change" in r for r in refs)
        assert sy.verify(out, sc["evidence"]) == []


# --- v3 fixes (D13) -------------------------------------------------------------------------


def test_v3_rules_name_both_intent_values() -> None:
    for rules in (rv.MONTHS_RULES, rv.TRANSCRIBE_RULES):
        assert '"explain_change"' in rules and '"other"' in rules


def test_v3_period_label_is_a_usable_slot() -> None:
    sc = sy.scenario("a", 0)
    assert "ev_total.comparison_period" in sy.available_refs(sc["evidence"])
    out = sy.SynthOut(
        headline=claim(
            "{s} fell by {x} in {m}.",
            ("s", "ev_base_region.rows[segment=North].segment", "label"),
            ("x", "ev_base_region.rows[segment=North].unexpected_pct", "pct_abs"),
            ("m", "ev_total.comparison_period", "label"),
        ),
        claims=[claim("ok")],
    )
    assert sy.verify(out, sc["evidence"]) == []
    assert sy.render(out.headline, sc["evidence"]).endswith("in October 2025.")
    bad = sy.SynthOut(
        headline=claim("Revenue was {m}.", ("m", "ev_total.comparison_period", "money")),
        claims=[claim("ok")],
    )
    assert sy.verify(bad, sc["evidence"])


def test_v3_number_format_is_gone() -> None:
    with pytest.raises(ValueError):
        sy.Slot(name="x", ref="ev_total.delta", fmt="number")  # type: ignore[arg-type]


def test_v3_significance_is_rejected() -> None:
    sc = sy.scenario("c", 20)
    out = sy.SynthOut(
        headline=claim("The statistical significance of the change is low."), claims=[claim("ok")]
    )
    assert any("signific" in e for e in sy.verify(out, sc["evidence"]))
