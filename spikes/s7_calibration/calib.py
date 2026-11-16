"""S7: is baseline_compare + BH honest on data shaped like a small shop's?

The generator, the test and the multiplicity step, kept apart so the test can be checked
against statsmodels on its own (criterion 5).
"""

from dataclasses import dataclass

import numpy as np
from scipy import signal, stats

REGIONS, CHANNELS, DAYS = 4, 3, 30  # synthetic 30-day months; calendar length isn't the point
CELLS = REGIONS * CHANNELS  # cell index = region * CHANNELS + channel
SEGMENTS = ["total", "r0", "r1", "r2", "r3", "c0", "c1", "c2"]  # m = 8 p-values per seed


@dataclass(frozen=True)
class Config:
    orders_per_day: float = 400.0  # whole shop
    region_w: tuple[float, ...] = (0.35, 0.30, 0.20, 0.15)
    channel_w: tuple[float, ...] = (0.5, 0.3, 0.2)
    nb_shape: float = 5.0  # gamma-Poisson: daily rate ~ Gamma(shape, mean/shape)
    value_mu: float = 3.6  # log order value: mean ~ $50
    value_sigma: float = 0.8
    weekly_amp: float = 0.15
    ar_phi: float = 0.5  # AR(1) on log daily demand
    ar_sd: float = 0.05
    month_shared_sd: float = 0.03  # one shock per month hitting every cell
    month_cell_sd: float = 0.04  # plus one per cell per month

    def cell_means(self) -> np.ndarray:
        w = np.outer(self.region_w, self.channel_w).ravel()
        return self.orders_per_day * w


def simulate(
    rng: np.random.Generator,
    n_months: int,
    cfg: Config,
    growth: np.ndarray | None = None,
    seasonal_amp: float = 0.0,
    month_rho: float = 0.0,
) -> np.ndarray:
    """Revenue per day for each cell and month, shape (CELLS, n_months).

    growth: per-cell linear growth per month as a fraction of the starting level.
    month_rho: AR(1) coefficient for the monthly shocks. 0 makes them independent, which is
    the method's own assumption; > 0 is the realistic case it does not model.
    """
    n_days = n_months * DAYS
    day = np.arange(n_days)
    month = day // DAYS
    weekly = 1 + cfg.weekly_amp * np.sin(2 * np.pi * day / 7)
    ar = signal.lfilter([1.0], [1.0, -cfg.ar_phi], rng.normal(0, cfg.ar_sd, (CELLS, n_days)))
    shock = rng.normal(0, cfg.month_shared_sd, n_months)[None, :] + rng.normal(
        0, cfg.month_cell_sd, (CELLS, n_months)
    )
    if month_rho:
        # Keep the stationary variance equal to the independent case.
        shock = signal.lfilter([np.sqrt(1 - month_rho**2)], [1.0, -month_rho], shock, axis=1)
    level = np.ones((CELLS, n_months))
    if growth is not None:
        level = level * (1 + growth[:, None] * np.arange(n_months)[None, :])
    if seasonal_amp:
        phase = rng.uniform(0, 12)
        level = level * (1 + seasonal_amp * np.sin(2 * np.pi * (np.arange(n_months) + phase) / 12))
    mu = (
        cfg.cell_means()[:, None] * weekly[None, :] * np.exp(ar + shock[:, month]) * level[:, month]
    )
    orders = rng.poisson(rng.gamma(cfg.nb_shape, mu / cfg.nb_shape))
    # The sum of n lognormal order values, by its normal approximation (n is tens per day).
    s2 = cfg.value_sigma**2
    m = np.exp(cfg.value_mu + s2 / 2)
    v = (np.exp(s2) - 1) * np.exp(2 * cfg.value_mu + s2)
    revenue = np.clip(rng.normal(orders * m, np.sqrt(orders * v)), 0, None)
    per_day: np.ndarray = revenue.reshape(CELLS, n_months, DAYS).sum(axis=2) / DAYS
    return per_day


def segments(cells: np.ndarray) -> np.ndarray:
    """Cells (CELLS, T) -> segments (8, T) in SEGMENTS order. Revenue is additive."""
    grid = cells.reshape(REGIONS, CHANNELS, -1)
    return np.vstack([grid.sum(axis=(0, 1))[None], grid.sum(axis=1), grid.sum(axis=0)])


@dataclass(frozen=True)
class Compare:
    observed: float
    predicted: float
    se: float
    p: float
    lo: float
    hi: float
    s: float  # in-sample residual SD of the trend fit


def baseline_compare(y: np.ndarray, alpha: float = 0.05) -> Compare:
    """One-step-ahead prediction interval on levels (ARCH §6).

    Fit y ~ a + b*t on the first k points, predict point k. SE_pred = s*sqrt(1 + 1/k +
    (t0 - tbar)^2 / Stt), t-distribution with k - 2 df. Assumes iid normal residuals around
    a linear trend; S7 measures what happens when the data bends that assumption.
    """
    k = len(y) - 1
    if k < 12:
        raise ValueError("not_estimable: fewer than 12 prior periods")
    t = np.arange(k, dtype=float)
    hist = y[:k]
    tbar = t.mean()
    stt = ((t - tbar) ** 2).sum()
    b = ((t - tbar) * (hist - hist.mean())).sum() / stt
    a = hist.mean() - b * tbar
    resid = hist - (a + b * t)
    s = np.sqrt((resid**2).sum() / (k - 2))
    pred = a + b * k
    se = s * np.sqrt(1 + 1 / k + (k - tbar) ** 2 / stt)
    tstat = (y[k] - pred) / se
    p = 2 * stats.t.sf(abs(tstat), k - 2)
    half = stats.t.ppf(1 - alpha / 2, k - 2) * se
    return Compare(
        float(y[k]), float(pred), float(se), float(p), pred - half, pred + half, float(s)
    )


def bh(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted q-values."""
    m = len(p)
    order = np.argsort(p)
    ranked = p[order] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(q, 1.0)
    return out


def by(p: np.ndarray) -> np.ndarray:
    """Benjamini-Yekutieli: BH times sum(1/i); valid under any dependence."""
    return np.minimum(bh(p) * (1 / np.arange(1, len(p) + 1)).sum(), 1.0)


def plant_drop(cells: np.ndarray, region: int, amount: float) -> np.ndarray:
    """Lower one region's final month by `amount` (revenue/day), split across its channels
    in proportion to their size that month."""
    out = cells.copy()
    rows = slice(region * CHANNELS, (region + 1) * CHANNELS)
    last = out[rows, -1]
    out[rows, -1] = last - amount * last / last.sum()
    return out
