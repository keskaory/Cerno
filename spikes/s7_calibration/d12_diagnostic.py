"""Post-run diagnostic for the D12 3a check (not a gate). Seed root 99, never the 1216 stream.

Why did the raw p < alpha/8 hit rate beat the ideal test? Same null log series, three ways of
planting a drop of d level-SDs in one region's last month:
  ideal:     analytic_power(d)
  shift:     a pure log shift of d log-SDs (checks the test itself is ideal)
  concave:   a fixed log shift of -ln(1 - x) at the expected level (log concavity only)
  realized:  subtract the revenue amount from the noisy last month (what d12_check.py does)

usage: python d12_diagnostic.py   (prints a table; about 10 s)
"""

import calib as cb
import extended as ex
import numpy as np

A8 = ex.ALPHA / ex.M
N = 3000
FLAT = ex.Condition("flat")


def main() -> None:
    logs, levels = [], []
    for i in range(N):
        rng = np.random.default_rng([99, 5, i])
        n_months = int(rng.integers(13, 25))
        cells, _ = ex.simulate_ext(rng, n_months, FLAT)
        lv = cb.segments(cells)[1 + i % cb.REGIONS]
        levels.append(lv)
        logs.append(np.log(lv))
    region = np.arange(N) % cb.REGIONS
    # Per-region pooled residual SDs (RMS of the in-sample s of each fit), on each scale.
    s_log = np.array([cb.baseline_compare(y).s for y in logs])
    s_lev = np.array([cb.baseline_compare(y).s for y in levels])
    sd_log = np.array([np.sqrt(np.mean(s_log[region == g] ** 2)) for g in range(cb.REGIONS)])
    sd_lev = np.array([np.sqrt(np.mean(s_lev[region == g] ** 2)) for g in range(cb.REGIONS)])
    lev_means = np.array([lv.mean() for lv in levels])
    mean_lev = np.array([lev_means[region == g].mean() for g in range(cb.REGIONS)])
    print("level SD / mean level vs log SD:", np.round(sd_lev / mean_lev / sd_log, 3))

    def hit(y: np.ndarray) -> bool:
        r = cb.baseline_compare(y)
        return bool(r.p < A8 and r.observed < r.predicted)

    print("d  ideal  shift  concave  realized")
    for d in (2, 3, 4, 5):
        ideal = np.mean([ex.analytic_power(d, len(y) - 1, A8) for y in logs])
        shift = concave = realized = 0
        for i, y in enumerate(logs):
            g = region[i]
            amount = d * sd_lev[g]
            ys = y.copy()
            ys[-1] -= d * sd_log[g]
            yc = y.copy()
            yc[-1] += np.log(1 - amount / mean_lev[g])
            yr = y.copy()
            yr[-1] = np.log(np.exp(y[-1]) - amount)
            shift += hit(ys)
            concave += hit(yc)
            realized += hit(yr)
        print(f"{d}  {ideal:.3f}  {shift / N:.3f}  {concave / N:.3f}    {realized / N:.3f}")


if __name__ == "__main__":
    main()
