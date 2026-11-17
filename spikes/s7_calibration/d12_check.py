"""D12 criterion 3a on a fresh seed stream: is baseline_compare (log levels, D11) efficient?

Pre-registered before the first run (2026-11-16), together with this file's hash:
- Data: the "flat" condition (null_flat-shaped), the extended.py generator.
- Seeds: root 1216, a stream no earlier S7 run uses (run.py uses roots 1-6, extended.py 7).
- d unit: the region's in-sample level residual SD, pooled as RMS over 1,000 null seeds of
  this stream ([1216, 0, i]; same definition as run.py and extended.py, not the stream-7 values).
- Power: 1,000 seeds per d, d = 2, 3, 4, 5. The planted region cycles over the 4 regions.
  A hit is q < 0.05 (BH over the estimable segments) for that region, in the planted direction.
- Ideal test: analytic_power(d, k, 0.05 / 8), averaged over the k each power seed drew.
- Gate: for every d, empirical log-scale power >= ideal - 0.05, compared unrounded (point
  estimate; Wilson 95% intervals reported). If any d fails, 3a is recorded as failed.
- It runs once. No rerun, whatever the result.
- Reported only: levels power; the non-planted region flag rate; and a like-for-like
  efficiency check, the hit rate with the region's raw p < 0.05/8 in the planted direction.
  The BH gate is lenient: the planted drop also moves the total and the channels, which
  loosens BH's threshold for the region, while the ideal test is held at alpha/8.
- The output records the git commit, SHA-256 of this file, extended.py and calib.py, and the
  python/numpy/scipy versions.

usage: python d12_check.py   (writes results/d12_check.json)
"""

import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

import calib as cb
import extended as ex
import numpy as np
import scipy

HERE = Path(__file__).parent
ROOT = 1216
N_NULL = 1000
N_POWER = 1000
DS = (2, 3, 4, 5)
MARGIN = 0.05
FLAT = ex.Condition("flat")


def rng(*key: int) -> np.random.Generator:
    return np.random.default_rng([ROOT, *key])


def region_sd(n: int = N_NULL) -> np.ndarray:
    s = np.array([ex.one_seed(rng(0, i), FLAT)["s_level"] for i in range(n)])
    regions = s[:, 1 : 1 + cb.REGIONS]
    return np.asarray(np.sqrt(np.nanmean(regions**2, axis=0)))


def power_point(j: int, d: float, sd: np.ndarray, n: int = N_POWER) -> dict[str, Any]:
    hits = {"level": 0, "log": 0}
    raw_bonf = 0
    other = {"level": 0, "log": 0}
    ks = []
    for s in range(n):
        region = s % cb.REGIONS
        r = ex.one_seed(rng(1, j, s), FLAT, drop=(region, d * sd[region], "sd"))
        ks.append(r["k"])
        for scale in hits:
            q = np.nan_to_num(r[scale]["q"], nan=1.0)
            hits[scale] += bool(q[1 + region] < ex.ALPHA and r[scale]["err"][1 + region] < 0)
            other[scale] += sum(bool(q[1 + g] < ex.ALPHA) for g in range(cb.REGIONS) if g != region)
        p_reg = r["log"]["p"][1 + region]
        raw_bonf += bool(p_reg < ex.ALPHA / ex.M and r["log"]["err"][1 + region] < 0)
    ideal = float(np.mean([ex.analytic_power(d, k, ex.ALPHA / ex.M) for k in ks]))
    power_log = hits["log"] / n
    return {
        "d": d,
        "n": n,
        "power_log": round(power_log, 4),
        "power_log_wilson95": ex.wilson(hits["log"], n),
        "power_level": round(hits["level"] / n, 4),
        "ideal_alpha_over_8": round(ideal, 4),
        "bar": round(ideal - MARGIN, 4),
        "pass": power_log >= ideal - MARGIN,
        "power_log_raw_p_alpha_over_8": round(raw_bonf / n, 4),
        "power_log_raw_p_alpha_over_8_wilson95": ex.wilson(raw_bonf, n),
        "other_region_flag_rate_log": round(other["log"] / (n * (cb.REGIONS - 1)), 4),
    }


def env() -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=HERE, check=False
    ).stdout.strip()
    files = ("d12_check.py", "extended.py", "calib.py")
    return {
        "git_commit": commit,
        "sha256": {f: hashlib.sha256((HERE / f).read_bytes()).hexdigest() for f in files},
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "platform": platform.platform(),
    }


def main() -> None:
    t0 = time.time()
    sd = region_sd()
    points = [power_point(j, d, sd) for j, d in enumerate(DS)]
    out = {
        "env": env(),
        "root_seed": ROOT,
        "region_sd_revenue_per_day": [round(float(x), 2) for x in sd],
        "points": points,
        "pass_3a": all(p["pass"] for p in points),
        "elapsed_s": round(time.time() - t0, 1),
    }
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "d12_check.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"pass_3a": out["pass_3a"], "points": points}, indent=1))


if __name__ == "__main__":
    main()
