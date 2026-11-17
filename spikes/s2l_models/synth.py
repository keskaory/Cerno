"""S6: slot-template claims, a deterministic VERIFY, and Guided-mode code templates.

The model writes templates like "{region} fell by {drop}" plus slots that point at evidence
("ev_base_region.rows[segment=West].unexpected_pct"). Code renders every number. VERIFY checks
the templates before anything is shown.
"""

import json
import random
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

# *_abs formats drop the sign so "fell by {x}" reads naturally; VERIFY still checks the
# direction word against the signed value.
# v3: "number" removed. No evidence field accepted it, so offering it only invited rejections.
Fmt = Literal["money", "money_abs", "pct", "pct_abs", "label"]


class Slot(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    ref: str
    fmt: Fmt


class Claim(BaseModel):
    template: str = Field(max_length=300)
    slots: list[Slot] = Field(max_length=6)


class SynthOut(BaseModel):
    headline: Claim
    claims: list[Claim] = Field(min_length=1, max_length=6)


REGIONS = ["North", "South", "East", "West"]
CHANNELS = ["web", "app", "store"]
SIGNED = {
    "delta",
    "delta_pct",
    "unexpected",
    "unexpected_pct",
    "rate_effect",
    "mix_effect",
    "total_effect",
}

# --- evidence ---------------------------------------------------------------------------


def _rows(segs: list[str], fields: dict[str, list[float]]) -> list[dict[str, Any]]:
    return [{"segment": s, **{k: v[i] for k, v in fields.items()}} for i, s in enumerate(segs)]


def scenario(kind: str, seed: int) -> dict[str, Any]:
    """Seeded evidence for scenario a (driver), b (offsetting AOV) or c (null)."""
    rng = random.Random(seed)
    base = rng.uniform(800_000, 1_400_000)
    planted = REGIONS[seed % 4]
    ev: dict[str, Any] = {}
    if kind in ("a", "c"):
        metric = "revenue"
        if kind == "a":
            drop = -rng.uniform(0.18, 0.30)
            reg_pct = [drop if r == planted else rng.uniform(-0.02, 0.02) for r in REGIONS]
            reg_q = [
                round(rng.uniform(0.001, 0.009), 4)
                if r == planted
                else round(rng.uniform(0.3, 0.9), 2)
                for r in REGIONS
            ]
        else:
            reg_pct = [rng.uniform(-0.04, 0.04) for _ in REGIONS]
            reg_q = [round(rng.uniform(0.2, 0.95), 2) for _ in REGIONS]
        reg_base = [base * w for w in (0.3, 0.25, 0.25, 0.2)]
        reg_delta = [b * p for b, p in zip(reg_base, reg_pct, strict=True)]
        total_delta = sum(reg_delta)
        ch_pct = [total_delta / base + rng.uniform(-0.01, 0.01) for _ in CHANNELS]
        ch_base = [base * w for w in (0.5, 0.3, 0.2)]
        ch_delta = [b * p for b, p in zip(ch_base, ch_pct, strict=True)]
        total_q = (
            round(rng.uniform(0.01, 0.04), 3) if kind == "a" else round(rng.uniform(0.3, 0.8), 2)
        )
        ev["ev_total"] = {
            "kind": "describe_change",
            "metric": metric,
            "comparison_value": round(base + total_delta, 2),
            "baseline_value": round(base, 2),
            "delta": round(total_delta, 2),
            "delta_pct": round(total_delta / base, 4),
        }
        ev["ev_base_total"] = {
            "kind": "baseline_compare",
            "segment": "total",
            "unexpected": round(total_delta * 0.9, 2),
            "unexpected_pct": round(total_delta / base * 0.9, 4),
            "q": total_q,
        }
        ev["ev_dec_region"] = {
            "kind": "decompose_change",
            "dimension": "region",
            "rows": _rows(
                REGIONS,
                {
                    "delta": [round(d, 2) for d in reg_delta],
                    "share_of_change": [
                        round(d / total_delta, 4) if total_delta else 0.0 for d in reg_delta
                    ],
                },
            ),
        }
        ev["ev_base_region"] = {
            "kind": "baseline_compare",
            "dimension": "region",
            "rows": _rows(
                REGIONS, {"unexpected_pct": [round(p * 0.95, 4) for p in reg_pct], "q": reg_q}
            ),
        }
        ev["ev_dec_channel"] = {
            "kind": "decompose_change",
            "dimension": "channel",
            "rows": _rows(
                CHANNELS,
                {
                    "delta": [round(d, 2) for d in ch_delta],
                    "share_of_change": [
                        round(d / total_delta, 4) if total_delta else 0.0 for d in ch_delta
                    ],
                },
            ),
        }
        ev["ev_base_channel"] = {
            "kind": "baseline_compare",
            "dimension": "channel",
            "rows": _rows(
                CHANNELS,
                {
                    "unexpected_pct": [round(p * 0.9, 4) for p in ch_pct],
                    "q": [round(rng.uniform(0.15, 0.6), 2) for _ in CHANNELS],
                },
            ),
        }
    else:  # b: AOV, two regions move in opposite directions and offset
        up, down = planted, REGIONS[(seed + 1) % 4]
        aov = rng.uniform(55, 80)
        rate = {
            r: (
                rng.uniform(2.5, 4.0)
                if r == up
                else -rng.uniform(2.5, 4.0)
                if r == down
                else rng.uniform(-0.3, 0.3)
            )
            for r in REGIONS
        }
        mix = {r: rng.uniform(-0.4, 0.4) for r in REGIONS}
        total = {r: rate[r] + mix[r] for r in REGIONS}
        tdelta = sum(total.values())
        ev["ev_total"] = {
            "kind": "describe_change",
            "metric": "aov",
            "comparison_value": round(aov + tdelta, 2),
            "baseline_value": round(aov, 2),
            "delta": round(tdelta, 2),
            "delta_pct": round(tdelta / aov, 4),
        }
        ev["ev_base_total"] = {
            "kind": "baseline_compare",
            "segment": "total",
            "unexpected": round(tdelta * 0.8, 2),
            "unexpected_pct": round(tdelta / aov * 0.8, 4),
            "q": round(rng.uniform(0.4, 0.9), 2),
        }
        ev["ev_mix_region"] = {
            "kind": "decompose_change",
            "method": "symmetric_kitagawa",
            "dimension": "region",
            "rows": [
                {
                    "segment": r,
                    "rate_effect": round(rate[r], 3),
                    "mix_effect": round(mix[r], 3),
                    "total_effect": round(total[r], 3),
                }
                for r in REGIONS
            ],
        }
        ev["ev_base_region"] = {
            "kind": "baseline_compare",
            "dimension": "region",
            "rows": [
                {
                    "segment": r,
                    "unexpected_pct": round(rate[r] / aov, 4),
                    "q": round(rng.uniform(0.001, 0.02), 4)
                    if r in (up, down)
                    else round(rng.uniform(0.3, 0.9), 2),
                }
                for r in REGIONS
            ],
        }
        ev["ev_base_channel"] = {
            "kind": "baseline_compare",
            "dimension": "channel",
            "rows": [
                {
                    "segment": c,
                    "unexpected_pct": round(rng.uniform(-0.01, 0.01), 4),
                    "q": round(rng.uniform(0.3, 0.9), 2),
                }
                for c in CHANNELS
            ],
        }
    # v3: period labels as evidence, so a template can name the months through a label slot
    # instead of writing "October 2025" (digits) into its text.
    ev["ev_total"]["comparison_period"] = "October 2025"
    ev["ev_total"]["baseline_period"] = "September 2025"
    return {
        "scenario": kind,
        "seed": seed,
        "planted": planted if kind == "a" else None,
        "metric": ev["ev_total"]["metric"],
        "comparison": "October 2025",
        "baseline": "September 2025",
        "evidence": ev,
    }


def flagged(ev: dict[str, Any]) -> set[str]:
    """Segments with confirmatory q < 0.05 (the total is not a segment)."""
    return {
        r["segment"]
        for e in ev.values()
        if e["kind"] == "baseline_compare"
        for r in e.get("rows", [])
        if r["q"] < 0.05
    }


def outcome(ev: dict[str, Any]) -> str:
    beyond = flagged(ev) or ev["ev_base_total"]["q"] < 0.05
    return "change_beyond_normal_variation" if beyond else "no_meaningful_change"


# --- references and rendering -------------------------------------------------------------

REF = re.compile(r"^(ev_[a-z_]+)(?:\.rows\[segment=([A-Za-z]+)\])?\.([a-z_]+)$")


def resolve_ref(ev: dict[str, Any], ref: str) -> Any:
    m = REF.match(ref)
    if not m:
        raise KeyError(f"bad reference syntax: {ref}")
    item = ev.get(m[1])
    if item is None:
        raise KeyError(f"no evidence {m[1]}")
    if m[2]:
        row = next((r for r in item.get("rows", []) if r["segment"] == m[2]), None)
        if row is None:
            raise KeyError(f"{m[1]} has no row for {m[2]}")
        item = row
    if m[3] not in item:
        raise KeyError(f"{ref}: no field {m[3]}")
    return item[m[3]]


def render_value(v: Any, fmt: str) -> str:
    if fmt == "label":
        return str(v)
    if not isinstance(v, int | float):
        raise TypeError(f"{fmt} needs a number, got {v!r}")
    if fmt.endswith("_abs"):
        v, fmt = abs(v), fmt[:-4]
    if fmt == "money":
        return (
            f"{'-' if v < 0 else ''}${abs(v):,.0f}"
            if abs(v) >= 100
            else f"{'-' if v < 0 else ''}${abs(v):,.2f}"
        )
    if fmt == "pct":
        return f"{v * 100:.1f}%"
    raise TypeError(f"unknown format {fmt!r}")


def render(claim: Claim, ev: dict[str, Any]) -> str:
    values = {s.name: render_value(resolve_ref(ev, s.ref), s.fmt) for s in claim.slots}
    return claim.template.format(**values)


# --- VERIFY -------------------------------------------------------------------------------

UP = set(
    [
        "rose",
        "rise",
        "rises",
        "rising",
        "increased",
        "increase",
        "increases",
        "increasing",
        "grew",
        "grow",
        "grows",
        "growing",
        "higher",
        "up",
        "gained",
        "gain",
        "gains",
        "improved",
        "improve",
        "jumped",
        "climbed",
        "climb",
        "above",
    ]
)
DOWN = set(
    [
        "fell",
        "fall",
        "falls",
        "falling",
        "decreased",
        "decrease",
        "decreases",
        "decreasing",
        "declined",
        "decline",
        "declines",
        "declining",
        "dropped",
        "drop",
        "drops",
        "dropping",
        "lower",
        "down",
        "shrank",
        "shrink",
        "slipped",
        "dipped",
        "dip",
        "slid",
        "below",
    ]
)
DRIVER = re.compile(r"\b(drove|drive|drives|driver|drivers|driving|responsible for)\b")
CAUSAL = re.compile(r"\b(caused|causes|because|due to|led to|as a result)\b")
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
SEGMENT_NAMES = re.compile(r"\b(" + "|".join(REGIONS + CHANNELS) + r")\b", re.IGNORECASE)
# Which formats each evidence field may be shown in. Every metric in S6 is money-valued.
FIELD_FMTS = {
    "segment": {"label"},
    "comparison_period": {"label"},
    "baseline_period": {"label"},
    "q": set(),  # v3: q drives the evidence label; it is never shown as a number
    "delta_pct": {"pct", "pct_abs"},
    "unexpected_pct": {"pct", "pct_abs"},
    "share_of_change": {"pct", "pct_abs"},
    **{
        f: {"money", "money_abs"}
        for f in (
            "delta",
            "unexpected",
            "comparison_value",
            "baseline_value",
            "rate_effect",
            "mix_effect",
            "total_effect",
        )
    },
}


def offsetting(item: dict[str, Any]) -> bool:
    """ARCH §6: shares of change are meaningless when gross movement > 3x the net change."""
    deltas = [r["delta"] for r in item.get("rows", []) if "delta" in r]
    net = sum(deltas)
    return bool(deltas) and sum(abs(d) for d in deltas) > 3 * abs(net)


def _direction_word(template: str, pos: int) -> str | None:
    """The direction word nearest to character position `pos`, as "up", "down" or None."""
    best: tuple[int, str] | None = None
    for m in re.finditer(r"[A-Za-z]+", PLACEHOLDER.sub(lambda x: " " * len(x[0]), template)):
        w = m[0].lower()
        side = "up" if w in UP else "down" if w in DOWN else None
        if side is None:
            continue
        dist = min(abs(m.start() - pos), abs(m.end() - pos))
        if best is None or dist < best[0]:
            best = (dist, side)
    return best[1] if best else None


def verify(out: SynthOut, ev: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    flags = flagged(ev)
    mentioned: set[str] = set()
    for i, c in enumerate([out.headline, *out.claims]):
        where = "headline" if i == 0 else f"claim {i}"
        names = PLACEHOLDER.findall(c.template)
        slot_names = [s.name for s in c.slots]
        if set(names) != set(slot_names) or len(slot_names) != len(set(slot_names)):
            errors.append(
                f"{where}: placeholders {sorted(set(names))} must match slot names {slot_names}"
            )
        bare = PLACEHOLDER.sub("", c.template)
        if re.search(r"[{}]", bare):
            errors.append(f"{where}: stray brace; placeholders look like {{name}}")
        if re.search(r"\d", bare):
            errors.append(f"{where}: digits in template text; every number must be a slot")
        if SEGMENT_NAMES.search(bare):
            errors.append(f"{where}: segment names must come from label slots, not template text")
        refs_ok = True
        for s in c.slots:
            try:
                v = resolve_ref(ev, s.ref)
                render_value(v, s.fmt)
            except (KeyError, TypeError) as e:
                errors.append(f"{where}: {e}")
                refs_ok = False
                continue
            m = REF.match(s.ref)
            assert m is not None  # resolve_ref succeeded
            field_name = m[3]
            if s.fmt not in FIELD_FMTS.get(field_name, {s.fmt}):
                errors.append(f"{where}: {field_name} cannot be shown as {s.fmt}")
            if field_name == "share_of_change" and offsetting(ev[m[1]]):
                errors.append(
                    f"{where}: movements in {m[1]} offset each other; say so instead of a share"
                )
            if m[2]:
                mentioned.add(m[2])
        low = bare.lower()
        if refs_ok:
            # Check each signed slot against the direction word nearest to it, so
            # "{a} fell by {x} while {b} rose by {y}" is checked clause by clause.
            for s in c.slots:
                if s.ref.rsplit(".", 1)[-1] not in SIGNED:
                    continue
                pos = c.template.find("{" + s.name + "}")
                side = _direction_word(c.template, pos) if pos >= 0 else None
                v = resolve_ref(ev, s.ref)
                if side == "up" and v < 0:
                    errors.append(f"{where}: says up/increase but {s.ref} is negative")
                if side == "down" and v > 0:
                    errors.append(f"{where}: says down/decrease but {s.ref} is positive")
        if DRIVER.search(low):
            labels = {
                m[2]
                for s in c.slots
                if s.fmt == "label" and (m := REF.match(s.ref)) and m[2] is not None
            }
            if not labels or not labels <= flags:
                errors.append(
                    f"{where}: driver wording needs every named segment to be beyond normal "
                    "variation"
                )
        if CAUSAL.search(low):
            errors.append(f"{where}: causal wording is not allowed")
        if "signific" in low:  # also catches insignificant / nonsignificant
            errors.append(f"{where}: do not use 'significant' or 'significance'")
        if re.search(r"https?://|www\.|!\[|\]\(|\*\*|<[a-z]", low):
            errors.append(f"{where}: links, markup or HTML are not allowed")
    for seg in sorted(flags - mentioned):
        errors.append(
            f"coverage: {seg} is beyond normal variation (q < 0.05) but no claim references it"
        )
    return errors


def driver_claims(out: SynthOut) -> int:
    return sum(bool(DRIVER.search(c.template.lower())) for c in [out.headline, *out.claims])


def has_digits(out: SynthOut) -> bool:
    return any(
        re.search(r"\d", PLACEHOLDER.sub("", c.template)) for c in [out.headline, *out.claims]
    )


def covers_planted(out: SynthOut, planted: str) -> bool:
    return any(
        f"[segment={planted}]" in s.ref for c in [out.headline, *out.claims] for s in c.slots
    )


# --- prompt -------------------------------------------------------------------------------

RULES = """You write the text of an analytics report as templates. Code fills in every number.
Rules:
- Write a headline and 1-6 claims. Each has a "template" and "slots".
- In templates, put {slot_name} wherever a number or segment name goes. Never write digits.
- Each slot has: name (lowercase), ref (an evidence reference from the list), fmt:
  money | money_abs | pct | pct_abs | label. Use label for segment names and month names, and
  the _abs formats after a direction word ("fell by {drop}" with pct_abs).
- Field units. Money: delta, unexpected, comparison_value, baseline_value, rate_effect,
  mix_effect, total_effect (for AOV these are dollars per order). Percent: delta_pct,
  unexpected_pct, share_of_change. Label: segment, comparison_period, baseline_period.
  q is used by code to decide "beyond normal variation"; never put q in a slot.
- Name the months with label slots ({month} with ref ev_total.comparison_period), never by
  writing them out, because a year is digits.
- Reference syntax: ev_name.field, or ev_name.rows[segment=X].field
- Segment names (regions, channels) always go in label slots, never in the template text.
- Mention every segment whose q is below 0.05. Do not call a segment a driver ("drove",
  "driver", "responsible for") unless its q is below 0.05.
- Percent fields use pct or pct_abs; money fields use money or money_abs.
- If segment changes in a decomposition mostly offset each other, say the movements offset
  instead of quoting a share of change.
- Direction words (rose, fell, up, down) must match the sign of the numbers they describe.
- No causal words (caused, because, due to, led to), no "significant" or "significance",
  no links or markup.
- Do not restate the overall outcome; code writes it."""


def available_refs(ev: dict[str, Any]) -> list[str]:
    refs = []
    for name, item in ev.items():
        for k, v in item.items():
            if isinstance(v, int | float) or k.endswith("_period"):
                refs.append(f"{name}.{k}")
        for r in item.get("rows", []):
            refs.extend(f"{name}.rows[segment={r['segment']}].{k}" for k in r)
    return refs


def prompt(sc: dict[str, Any]) -> tuple[str, str]:
    user = {
        "question": (
            f"Why did {sc['metric']} change in {sc['comparison']} compared with {sc['baseline']}?"
        ),
        "outcome_written_by_code": outcome(sc["evidence"]),
        "evidence": sc["evidence"],
        "allowed_refs": available_refs(sc["evidence"]),
    }
    return RULES, json.dumps(user, indent=1)


# --- Guided mode: code templates, no model ------------------------------------------------


def guided(sc: dict[str, Any]) -> SynthOut:
    ev = sc["evidence"]
    flags = flagged(ev)
    total_word = "rose" if ev["ev_total"]["delta"] > 0 else "fell"
    headline = Claim(
        template=(
            f"{sc['metric']} {total_word} by {{delta}} ({{delta_pct}}) from the baseline month."
        ),
        slots=[
            Slot(name="delta", ref="ev_total.delta", fmt="money_abs"),
            Slot(name="delta_pct", ref="ev_total.delta_pct", fmt="pct_abs"),
        ],
    )
    claims: list[Claim] = []
    for name, item in ev.items():
        if item["kind"] != "baseline_compare" or "rows" not in item:
            continue
        for r in item["rows"]:
            if r["segment"] not in flags:
                continue
            word = "above" if r["unexpected_pct"] > 0 else "below"
            ref = f"{name}.rows[segment={r['segment']}]"
            claims.append(
                Claim(
                    template=f"{{seg}} came in {{gap}} {word} the level its own history predicted, "
                    "beyond normal variation.",
                    slots=[
                        Slot(name="seg", ref=f"{ref}.segment", fmt="label"),
                        Slot(name="gap", ref=f"{ref}.unexpected_pct", fmt="pct_abs"),
                    ],
                )
            )
    if not flags:
        claims.append(
            Claim(
                template="No region or channel moved beyond its normal range of variation.",
                slots=[],
            )
        )
    if "ev_dec_region" in ev and offsetting(ev["ev_dec_region"]):
        claims.append(
            Claim(
                template="Regional movements largely offset each other, so no single region "
                "accounts for a meaningful share of the change.",
                slots=[],
            )
        )
    elif "ev_dec_region" in ev:
        top = max(ev["ev_dec_region"]["rows"], key=lambda r: abs(r["delta"]))
        ref = f"ev_dec_region.rows[segment={top['segment']}]"
        claims.append(
            Claim(
                template=(
                    "The largest regional contribution came from {seg}, at {share} of the change."
                ),
                slots=[
                    Slot(name="seg", ref=f"{ref}.segment", fmt="label"),
                    Slot(name="share", ref=f"{ref}.share_of_change", fmt="pct"),
                ],
            )
        )
    return SynthOut(headline=headline, claims=claims)
