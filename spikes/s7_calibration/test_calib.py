"""Tests for the S7 pieces, so a calibration result reflects the method, not a bug."""

import calib as cb
import numpy as np
import pytest
from statsmodels.stats.multitest import multipletests


def test_segments_add_up() -> None:
    cells = np.random.default_rng(0).uniform(1, 10, (cb.CELLS, 5))
    seg = cb.segments(cells)
    assert seg.shape == (8, 5)
    np.testing.assert_allclose(seg[0], cells.sum(axis=0))
    np.testing.assert_allclose(seg[1:5].sum(axis=0), seg[0])
    np.testing.assert_allclose(seg[5:8].sum(axis=0), seg[0])
    np.testing.assert_allclose(seg[1], cells[0:3].sum(axis=0))  # region 0 = cells 0..2
    np.testing.assert_allclose(seg[5], cells[0::3].sum(axis=0))  # channel 0 = every 3rd


def test_bh_and_by_match_statsmodels() -> None:
    p = np.random.default_rng(1).uniform(0, 0.2, 8)
    np.testing.assert_allclose(cb.bh(p), multipletests(p, method="fdr_bh")[1])
    np.testing.assert_allclose(cb.by(p), multipletests(p, method="fdr_by")[1])


def test_plant_drop_lowers_only_one_region_last_month() -> None:
    cells = np.random.default_rng(2).uniform(5, 10, (cb.CELLS, 14))
    out = cb.plant_drop(cells, 2, 3.0)
    seg_before, seg_after = cb.segments(cells), cb.segments(out)
    assert seg_before[3, -1] - seg_after[3, -1] == pytest.approx(3.0)
    np.testing.assert_allclose(seg_before[[1, 2, 4], -1], seg_after[[1, 2, 4], -1])
    np.testing.assert_allclose(cells[:, :-1], out[:, :-1])


def test_baseline_compare_gate_and_exact_line() -> None:
    with pytest.raises(ValueError):
        cb.baseline_compare(np.arange(12.0))
    y = 5 + 2 * np.arange(14.0) + np.tile([0.1, -0.1], 7)
    r = cb.baseline_compare(y)
    assert r.predicted == pytest.approx(5 + 2 * 13, abs=0.2)
    assert r.lo < r.predicted < r.hi


def test_simulate_is_deterministic_and_positive() -> None:
    cfg = cb.Config()
    a = cb.simulate(np.random.default_rng(3), 15, cfg)
    b = cb.simulate(np.random.default_rng(3), 15, cfg)
    np.testing.assert_array_equal(a, b)
    assert a.shape == (cb.CELLS, 15) and (a > 0).all()


def test_orders_clear_the_30_per_period_gate() -> None:
    # The smallest cell must average >= 30 orders per month, or the gate would hide segments.
    assert cb.Config().cell_means().min() * cb.DAYS >= 30


def test_month_ar_shocks_persist() -> None:
    # Lag-1 correlation of the total's monthly level should be clearly positive with rho > 0.
    cfg = cb.Config()

    def lag1(rho: float) -> float:
        vals = []
        for s in range(40):
            y = cb.segments(cb.simulate(np.random.default_rng(s), 24, cfg, month_rho=rho))[0]
            vals.append(np.corrcoef(y[:-1], y[1:])[0, 1])
        return float(np.mean(vals))

    assert lag1(0.6) > lag1(0.0) + 0.2
