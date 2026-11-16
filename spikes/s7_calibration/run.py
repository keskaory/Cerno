"""Run the S7 scenarios and check the 5 frozen criteria in docs/M1_SPIKES.md.

usage: python run.py [--log]   (writes results/s7.json or results/s7_log.json)

--log fits baseline_compare on log levels instead of levels: the candidate fix for the
heteroskedastic trend failure. Shifts are still planted in levels.
"""

import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

import calib as cb
import numpy as np
import scipy
import statsmodels
import statsmodels.api as sm
from scipy import stats

HERE = Path(__file__).parent
ALPHA = 0.05
LOG = "--log" in sys.argv
MONTH_RHO = 0.4  # reported-only scenario: month-to-month persistence the method ignores


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(float(c - h), 4), round(float(c + h), 4)]


SCENARIO_ID = {
    "null_flat": 1,
    "null_trend": 2,
    "shift": 3,
    "seasonal_null": 4,
    "formula": 5,
    "null_month_ar": 6,
}


def rng_for(scenario: str, seed: int, d: int = 0) -> np.random.Generator:
    return np.random.default_rng([SCENARIO_ID[scenario], d, seed])


def one_seed(
    rng: np.random.Generator,
    cfg: cb.Config,
    kind: str,
    drop: tuple[int, float] | None = None,
) -> dict[str, Any]:
    n_months = int(rng.integers(13, 25))  # 13-24 months: k = 12-23 prior periods
    growth = rng.uniform(0.01, 0.03, cb.CELLS) if kind == "null_trend" else None
    amp = 0.10 if kind == "seasonal_null" else 0.0
    rho = MONTH_RHO if kind == "null_month_ar" else 0.0
    cells = cb.simulate(rng, n_months, cfg, growth=growth, seasonal_amp=amp, month_rho=rho)
    if drop is not None:
        cells = cb.plant_drop(cells, *drop)
    levels = cb.segments(cells)
    segs = np.log(levels) if LOG else levels
    res = [cb.baseline_compare(s, ALPHA) for s in segs]
    # Shift sizes are always in level units, so both scales face the same planted drop.
    s_level = [r.s for r in res] if not LOG else [cb.baseline_compare(y).s for y in levels]
    p = np.array([r.p for r in res])
    return {
        "k": n_months - 1,
        "p": p,
        "q_bh": cb.bh(p),
        "q_by": cb.by(p),
        "total_covered": res[0].lo <= res[0].observed <= res[0].hi,
        "errors": np.array([r.observed - r.predicted for r in res]),
        "s": np.array(s_level),
    }


def null_scenario(kind: str, n: int, cfg: cb.Config) -> dict[str, Any]:
    runs = [one_seed(rng_for(kind, s), cfg, kind) for s in range(n)]
    any_bh = sum(bool((r["q_bh"] < ALPHA).any()) for r in runs)
    any_by = sum(bool((r["q_by"] < ALPHA).any()) for r in runs)
    covered = sum(bool(r["total_covered"]) for r in runs)
    per_segment = {
        name: round(sum(bool(r["q_bh"][i] < ALPHA) for r in runs) / n, 4)
        for i, name in enumerate(cb.SEGMENTS)
    }
    raw_p05 = sum(bool((r["p"] < ALPHA).any()) for r in runs)
    return {
        "n": n,
        "any_driver_rate_bh": round(any_bh / n, 4),
        "any_driver_rate_bh_wilson95": wilson(any_bh, n),
        "any_driver_rate_by": round(any_by / n, 4),
        "any_raw_p_below_alpha_rate": round(raw_p05 / n, 4),
        "total_pi_coverage": round(covered / n, 4),
        "total_pi_coverage_wilson95": wilson(covered, n),
        "flag_rate_by_segment_bh": per_segment,
        "_s": np.array([r["s"] for r in runs]),
    }


def shift_scenario(d: int, n: int, cfg: cb.Config, region_sd: np.ndarray) -> dict[str, Any]:
    planted_hits = 0
    other_flags = np.zeros(cb.REGIONS)
    other_n = np.zeros(cb.REGIONS)
    for s in range(n):
        region = s % cb.REGIONS
        r = one_seed(rng_for("shift", s, d), cfg, "null_flat", drop=(region, d * region_sd[region]))
        flags = r["q_bh"][1 : 1 + cb.REGIONS] < ALPHA
        # Power counts a flag only in the planted direction (a drop).
        planted_hits += bool(flags[region]) and r["errors"][1 + region] < 0
        for j in range(cb.REGIONS):
            if j != region:
                other_flags[j] += bool(flags[j])
                other_n[j] += 1
    other_rates = other_flags / other_n
    return {
        "n": n,
        "power": round(planted_hits / n, 4),
        "power_wilson95": wilson(planted_hits, n),
        "non_planted_region_flag_rate": [round(float(x), 4) for x in other_rates],
        "max_non_planted_region_flag_rate": round(float(other_rates.max()), 4),
        "non_planted_wilson95": [
            wilson(int(f), int(m)) for f, m in zip(other_flags, other_n, strict=True)
        ],
    }


def formula_check(n: int = 100) -> dict[str, Any]:
    worst_se = worst_p = 0.0
    for s in range(n):
        rng = rng_for("formula", s)
        k = int(rng.integers(12, 30))
        y = rng.normal(100, 10, k + 1) + rng.uniform(-2, 2) * np.arange(k + 1)
        mine = cb.baseline_compare(y)
        x = sm.add_constant(np.arange(k, dtype=float))
        fit = sm.OLS(y[:k], x).fit()
        pred = fit.get_prediction(np.array([[1.0, float(k)]]))
        se = float(np.sqrt(pred.var_pred_mean[0] + fit.scale))
        p = float(2 * stats.t.sf(abs((y[k] - pred.predicted_mean[0]) / se), fit.df_resid))
        worst_se = max(worst_se, abs(se - mine.se))
        worst_p = max(worst_p, abs(p - mine.p))
    return {"n": n, "max_abs_diff_se": worst_se, "max_abs_diff_p": worst_p}


def main() -> None:
    t0 = time.time()
    cfg = cb.Config()
    flat = null_scenario("null_flat", 1000, cfg)
    trend = null_scenario("null_trend", 1000, cfg)
    seasonal = null_scenario("seasonal_null", 500, cfg)
    month_ar = null_scenario("null_month_ar", 500, cfg)
    # d units = the region's in-sample residual SD under null_flat (pooled as RMS over seeds),
    # as the spec says. The prediction SE is 9-17% larger, so a shift of d residual SDs is
    # about d/1.1 prediction SEs.
    region_sd = np.sqrt((flat["_s"][:, 1 : 1 + cb.REGIONS] ** 2).mean(axis=0))
    shifts = {f"shift_d{d}": shift_scenario(d, 300, cfg, region_sd) for d in (1, 2, 3, 4)}
    formula = formula_check()

    crit = {
        "1_false_positives": all(x["any_driver_rate_bh"] <= 0.069 for x in (flat, trend)),
        "2_coverage": all(0.932 <= x["total_pi_coverage"] <= 0.968 for x in (flat, trend)),
        "3_power_d3": shifts["shift_d3"]["power"] >= 0.80,
        "4_precision": all(x["max_non_planted_region_flag_rate"] <= 0.05 for x in shifts.values()),
        "5_formula": formula["max_abs_diff_se"] <= 1e-9 and formula["max_abs_diff_p"] <= 1e-9,
    }
    for x in (flat, trend, seasonal, month_ar):
        x.pop("_s")
    out = {
        "env": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "statsmodels": statsmodels.__version__,
            "platform": platform.platform(),
            "config": cfg.__dict__,
        },
        "region_sd_revenue_per_day": [round(float(x), 2) for x in region_sd],
        "null_flat": flat,
        "null_trend": trend,
        "seasonal_null": seasonal,
        "null_month_ar_reported_only": {"rho": MONTH_RHO, **month_ar},
        **shifts,
        "formula_check": formula,
        "criteria": {k: bool(v) for k, v in crit.items()},
        "passed": bool(all(crit.values())),
        "elapsed_s": round(time.time() - t0, 1),
    }
    (HERE / "results").mkdir(exist_ok=True)
    out["scale"] = "log" if LOG else "level"
    (HERE / "results" / ("s7_log.json" if LOG else "s7.json")).write_text(
        json.dumps(out, indent=1, default=float)
    )
    print(json.dumps({k: out[k] for k in ("criteria", "passed", "elapsed_s")}, indent=1))


if __name__ == "__main__":
    main()
