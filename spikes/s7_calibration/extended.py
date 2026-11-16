"""S7 extended: does baseline_compare on LOG levels hold up beyond the original scenarios?

The original S7 (run.py, results/s7.json) failed and stays failed. This script investigates
the proposed revision, log-level modeling, under more conditions:
- growth: linear 0-8%/month, compounding, decline
- seasonality: +-5/10/20% annual cycle
- noise: monthly AR(1), larger shocks, heavy-tailed shocks, more overdispersion
- small and zero-valued segments, with the ARCH §6 gates (zero month or < 30 orders/period)

It reports false-positive rates, interval coverage, power over effect sizes, the minimum
detectable effect (MDE) for 80% power, and the analytic power of an ideal t-test so the
power requirement can be judged against what is statistically possible.

usage: python extended.py   (writes results/s7_extended.json; about a minute on CPU)
"""

import itertools
import json
import platform
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import calib as cb
import numpy as np
import scipy
from scipy import signal, stats

HERE = Path(__file__).parent
ALPHA = 0.05
M = len(cb.SEGMENTS)


@dataclass(frozen=True)
class Condition:
    name: str
    cfg: cb.Config = field(default_factory=cb.Config)
    growth: float = 0.0  # mean linear growth per month (cells vary from 0.5x to 1.5x)
    compound: bool = False  # growth compounds (exponential) instead of linear
    seasonal_amp: float = 0.0
    month_rho: float = 0.0
    shock_scale: float = 1.0  # multiplies both monthly shock SDs
    shock_df: float | None = None  # Student-t monthly shocks (scaled to the same SD)
    launch_region: bool = False  # region 3 has zero orders before a month drawn per seed


def simulate_ext(
    rng: np.random.Generator, n_months: int, c: Condition
) -> tuple[np.ndarray, np.ndarray]:
    """calib.simulate with more options. Returns (revenue per day, orders per month), each
    (CELLS, n_months). With a default Condition it draws exactly what calib.simulate draws."""
    cfg = c.cfg
    n_days = n_months * cb.DAYS
    day = np.arange(n_days)
    month = day // cb.DAYS
    weekly = 1 + cfg.weekly_amp * np.sin(2 * np.pi * day / 7)
    ar = signal.lfilter([1.0], [1.0, -cfg.ar_phi], rng.normal(0, cfg.ar_sd, (cb.CELLS, n_days)))
    sd_shared, sd_cell = cfg.month_shared_sd * c.shock_scale, cfg.month_cell_sd * c.shock_scale
    if c.shock_df is None:
        shock = rng.normal(0, sd_shared, n_months)[None, :] + rng.normal(
            0, sd_cell, (cb.CELLS, n_months)
        )
    else:  # t draws rescaled to unit variance, so only the tails change
        k = np.sqrt((c.shock_df - 2) / c.shock_df)
        shock = sd_shared * k * rng.standard_t(c.shock_df, n_months)[
            None, :
        ] + sd_cell * k * rng.standard_t(c.shock_df, (cb.CELLS, n_months))
    if c.month_rho:
        shock = signal.lfilter([np.sqrt(1 - c.month_rho**2)], [1.0, -c.month_rho], shock, axis=1)
    level = np.ones((cb.CELLS, n_months))
    t = np.arange(n_months)[None, :]
    if c.growth:
        lo, hi = sorted((0.5 * c.growth, 1.5 * c.growth))  # decline has negative growth
        g = rng.uniform(lo, hi, cb.CELLS)[:, None]
        level = level * ((1 + g) ** t if c.compound else np.clip(1 + g * t, 0.05, None))
    if c.seasonal_amp:
        phase = rng.uniform(0, 12)
        level = level * (1 + c.seasonal_amp * np.sin(2 * np.pi * (t + phase) / 12))
    if c.launch_region:  # a new region: zero before launch, a step in the total after
        launch = int(rng.integers(2, n_months - 2))
        level[3 * cb.CHANNELS : 4 * cb.CHANNELS, :launch] = 0.0
    mu = (
        cfg.cell_means()[:, None] * weekly[None, :] * np.exp(ar + shock[:, month]) * level[:, month]
    )
    orders = rng.poisson(rng.gamma(cfg.nb_shape, mu / cfg.nb_shape))
    s2 = cfg.value_sigma**2
    m = np.exp(cfg.value_mu + s2 / 2)
    v = (np.exp(s2) - 1) * np.exp(2 * cfg.value_mu + s2)
    revenue = np.clip(rng.normal(orders * m, np.sqrt(orders * v)), 0, None)
    per_day = revenue.reshape(cb.CELLS, n_months, cb.DAYS).sum(axis=2) / cb.DAYS
    monthly_orders = orders.reshape(cb.CELLS, n_months, cb.DAYS).sum(axis=2)
    return per_day, monthly_orders


def estimable(orders: np.ndarray) -> np.ndarray:
    """ARCH §6 gates per segment over the k prior periods: no zero period and >= 30 orders
    per period on average. orders: (8, k+1)."""
    hist = orders[:, :-1]
    return (hist > 0).all(axis=1) & (hist.mean(axis=1) >= 30) & (orders[:, -1] > 0)


def one_seed(
    rng: np.random.Generator,
    c: Condition,
    drop: tuple[int, float, str] | None = None,
) -> dict[str, Any]:
    """drop = (region, size, unit) with unit "pct" (fraction of that month's level) or
    "sd" (multiples of the level residual SD, passed in already converted to revenue/day)."""
    n_months = int(rng.integers(13, 25))
    cells, orders = simulate_ext(rng, n_months, c)
    if drop is not None:
        region, size, unit = drop
        rows = slice(region * cb.CHANNELS, (region + 1) * cb.CHANNELS)
        amount = size * cells[rows, -1].sum() if unit == "pct" else size
        cells = cb.plant_drop(cells, region, amount)
    levels, seg_orders = cb.segments(cells), cb.segments(orders)
    ok = estimable(seg_orders)
    out: dict[str, Any] = {"k": n_months - 1, "estimable": ok}
    for scale in ("level", "log"):
        p = np.full(M, np.nan)
        covered = None
        err = np.full(M, np.nan)
        s_level = np.full(M, np.nan)
        for i in np.flatnonzero(ok):
            y = np.log(levels[i]) if scale == "log" else levels[i]
            r = cb.baseline_compare(y, ALPHA)
            p[i], err[i] = r.p, r.observed - r.predicted
            if scale == "level":
                s_level[i] = r.s
            if i == 0:
                covered = r.lo <= r.observed <= r.hi
        q = np.full(M, np.nan)
        if ok.any():
            q[ok] = cb.bh(p[ok])  # BH over the estimable segments only (m varies)
        out[scale] = {"p": p, "q": q, "covered": covered, "err": err}
        if scale == "level":
            out["s_level"] = s_level
    return out


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [float("nan"), float("nan")]
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(float(c - h), 4), round(float(c + h), 4)]


def rng_for(*key: int) -> np.random.Generator:
    return np.random.default_rng([7, *key])  # 7: a stream no original S7 scenario uses


def null_condition(idx: int, c: Condition, n: int) -> dict[str, Any]:
    runs = [one_seed(rng_for(idx, s), c) for s in range(n)]
    res: dict[str, Any] = {
        "n": n,
        "segments_estimable_share": round(float(np.mean([r["estimable"] for r in runs])), 4),
        "total_estimable_share": round(float(np.mean([r["estimable"][0] for r in runs])), 4),
    }
    for scale in ("level", "log"):
        anyf = sum(bool((np.nan_to_num(r[scale]["q"], nan=1) < ALPHA).any()) for r in runs)
        cov_runs = [r[scale]["covered"] for r in runs if r[scale]["covered"] is not None]
        cov = sum(bool(x) for x in cov_runs)
        res[scale] = {
            "any_driver_rate": round(anyf / n, 4),
            "any_driver_wilson95": wilson(anyf, n),
            "total_pi_coverage": round(cov / len(cov_runs), 4) if cov_runs else None,
            "total_pi_coverage_wilson95": wilson(cov, len(cov_runs)),
            "pass_fp": anyf / n <= 0.069,
            "pass_coverage": bool(cov_runs) and 0.932 <= cov / len(cov_runs) <= 0.968,
        }
    res["_s_level"] = np.array([r["s_level"] for r in runs])
    return res


def power_curve(
    idx: int, c: Condition, sizes: Sequence[float], unit: str, n: int, region_sd: np.ndarray
) -> dict[str, Any]:
    curve: dict[str, Any] = {"unit": unit, "n_per_point": n, "points": []}
    for j, size in enumerate(sizes):
        hits = {"level": 0, "log": 0}
        other = {"level": 0, "log": 0}
        other_n = 0
        for s in range(n):
            region = s % cb.REGIONS
            amount = size * region_sd[region] if unit == "sd" else size
            r = one_seed(rng_for(idx, 1000 + j, s), c, drop=(region, amount, unit))
            for scale in ("level", "log"):
                q = np.nan_to_num(r[scale]["q"], nan=1.0)
                err = r[scale]["err"][1 + region]
                hits[scale] += bool(q[1 + region] < ALPHA and err < 0)
                other[scale] += sum(
                    bool(q[1 + g] < ALPHA) for g in range(cb.REGIONS) if g != region
                )
            other_n += cb.REGIONS - 1
        curve["points"].append(
            {
                "size": size,
                **{f"power_{sc}": round(hits[sc] / n, 4) for sc in hits},
                **{f"power_{sc}_wilson95": wilson(hits[sc], n) for sc in hits},
                **{f"other_region_flag_rate_{sc}": round(other[sc] / other_n, 4) for sc in other},
            }
        )
    for sc in ("level", "log"):
        curve[f"mde80_{sc}"] = mde80(
            [pt["size"] for pt in curve["points"]], [pt[f"power_{sc}"] for pt in curve["points"]]
        )
    return curve


def mde80(sizes: Sequence[float], power: Sequence[float]) -> float | None:
    """Smallest effect with 80% power, by linear interpolation on the measured curve."""
    pts = list(zip(sizes, power, strict=True))
    for (s0, p0), (s1, p1) in itertools.pairwise(pts):
        if p0 < 0.8 <= p1:
            return round(s0 + (0.8 - p0) * (s1 - s0) / (p1 - p0), 2)
    return sizes[0] if power and power[0] >= 0.8 else None


def analytic_power(d: float, k: int, alpha: float) -> float:
    """Power of one ideal prediction-interval t-test (iid normal residuals, known model) for a
    shift of d residual SDs: noncentral t with k-2 df and ncp d / SE inflation."""
    t = np.arange(k, dtype=float)
    infl = np.sqrt(1 + 1 / k + (k - t.mean()) ** 2 / ((t - t.mean()) ** 2).sum())
    crit = stats.t.ppf(1 - alpha / 2, k - 2)
    ncp = d / infl
    # scipy's nct.cdf returns NaN deep in the lower tail at large ncp; that tail is ~0 there.
    lower = float(np.nan_to_num(stats.nct.cdf(-crit, k - 2, ncp), nan=0.0))
    return float(stats.nct.sf(crit, k - 2, ncp)) + lower


def analytic_table() -> dict[str, Any]:
    """What the original criterion asked for, against what an ideal test can deliver.

    alpha_single = 0.05: one test, no correction (an upper bound on power).
    alpha_bonf = 0.05/8: what BH demands of a lone discovery among 8 tests (a lower bound
    when nothing else is flagged).
    """
    ds = [1, 2, 3, 4, 5, 6, 8]
    rows = []
    for k in (12, 18, 23):
        rows.append(
            {
                "k": k,
                "single_test": {d: round(analytic_power(d, k, ALPHA), 3) for d in ds},
                "bonferroni_8": {d: round(analytic_power(d, k, ALPHA / M), 3) for d in ds},
            }
        )
    avg = {
        d: round(float(np.mean([analytic_power(d, k, ALPHA / M) for k in range(12, 24)])), 3)
        for d in ds
    }
    return {"by_k": rows, "bonferroni_8_avg_over_k_12_23": avg}


CONDITIONS = [
    Condition("flat"),
    Condition("growth_1pct", growth=0.01),
    Condition("growth_3pct", growth=0.03),
    Condition("growth_5pct", growth=0.05),
    Condition("growth_8pct", growth=0.08),
    Condition("compound_3pct", growth=0.03, compound=True),
    Condition("decline_3pct", growth=-0.03),
    Condition("seasonal_5pct", seasonal_amp=0.05),
    Condition("seasonal_10pct", seasonal_amp=0.10),
    Condition("seasonal_20pct", seasonal_amp=0.20),
    Condition("month_ar_0.4", month_rho=0.4),
    Condition("month_ar_0.7", month_rho=0.7),
    Condition("shocks_x2", shock_scale=2.0),
    Condition("heavy_tails_t3", shock_df=3.0),
    Condition("overdispersed_nb1", cfg=replace(cb.Config(), nb_shape=1.0)),
    Condition("small_shop_40_per_day", cfg=replace(cb.Config(), orders_per_day=40.0)),
    Condition("tiny_shop_8_per_day", cfg=replace(cb.Config(), orders_per_day=8.0)),
    Condition("micro_shop_3_per_day", cfg=replace(cb.Config(), orders_per_day=3.0)),
    Condition("new_region_launch", launch_region=True),
]
POWER_CONDITIONS = ["flat", "growth_3pct", "seasonal_10pct", "month_ar_0.4", "heavy_tails_t3"]
SD_SIZES = [1, 2, 3, 4, 5, 6, 8]
PCT_SIZES = [0.05, 0.10, 0.15, 0.20, 0.30, 0.40]


def main() -> None:
    t0 = time.time()
    nulls: dict[str, Any] = {}
    for i, c in enumerate(CONDITIONS):
        nulls[c.name] = null_condition(i, c, 1000)
    flat_s = nulls["flat"]["_s_level"][:, 1 : 1 + cb.REGIONS]
    region_sd = np.sqrt(np.nanmean(flat_s**2, axis=0))  # same definition as run.py
    power: dict[str, Any] = {}
    for c in CONDITIONS:
        if c.name not in POWER_CONDITIONS:
            continue
        i = CONDITIONS.index(c)
        power[c.name] = {
            "by_sd": power_curve(i, c, SD_SIZES, "sd", 300, region_sd),
            "by_pct": power_curve(i, c, PCT_SIZES, "pct", 300, region_sd),
        }
    for v in nulls.values():
        v.pop("_s_level")
    cfg = cb.Config()
    mean_value = np.exp(cfg.value_mu + cfg.value_sigma**2 / 2)
    region_level = cb.segments(cfg.cell_means()[:, None] * mean_value)[1 : 1 + cb.REGIONS, 0]
    out = {
        "env": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "platform": platform.platform(),
        },
        "conditions": {c.name: {**asdict(c), "cfg": asdict(c.cfg)} for c in CONDITIONS},
        "region_sd_revenue_per_day": [round(float(x), 2) for x in region_sd],
        "region_sd_pct_of_expected_level": [
            round(float(s / lv), 4) for s, lv in zip(region_sd, region_level, strict=True)
        ],
        "null": nulls,
        "power": power,
        "analytic": analytic_table(),
        "elapsed_s": round(time.time() - t0, 1),
    }
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "s7_extended.json").write_text(json.dumps(out, indent=1, default=_json))
    print(f"wrote results/s7_extended.json in {out['elapsed_s']} s")


def _json(o: Any) -> Any:
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    raise TypeError(type(o))


if __name__ == "__main__":
    main()
