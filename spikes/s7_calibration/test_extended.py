"""Tests for the S7 extended experiments."""

import calib as cb
import extended as ex
import numpy as np


def test_default_condition_matches_original_generator() -> None:
    a = cb.simulate(np.random.default_rng(5), 16, cb.Config())
    b, orders = ex.simulate_ext(np.random.default_rng(5), 16, ex.Condition("flat"))
    np.testing.assert_array_equal(a, b)
    assert orders.shape == (cb.CELLS, 16) and orders.dtype.kind == "i"


def test_seasonal_matches_original_generator() -> None:
    a = cb.simulate(np.random.default_rng(6), 14, cb.Config(), seasonal_amp=0.1, month_rho=0.4)
    b, _ = ex.simulate_ext(
        np.random.default_rng(6), 14, ex.Condition("s", seasonal_amp=0.1, month_rho=0.4)
    )
    np.testing.assert_array_equal(a, b)


def test_gates() -> None:
    orders = np.full((8, 14), 100)
    orders[2, 3] = 0  # a zero period
    orders[5, :] = 20  # too few orders
    orders[6, -1] = 0  # zero in the comparison month: log undefined
    ok = ex.estimable(orders)
    assert ok.tolist() == [True, True, False, True, True, False, False, True]


def test_zero_and_small_segments_are_gated() -> None:
    for name in ("micro_shop_3_per_day", "new_region_launch"):
        c = next(x for x in ex.CONDITIONS if x.name == name)
        r = ex.one_seed(np.random.default_rng(1), c)
        assert not r["estimable"].all(), name
        assert np.isnan(r["log"]["p"][~r["estimable"]]).all()
    launch = next(x for x in ex.CONDITIONS if x.name == "new_region_launch")
    _, orders = ex.simulate_ext(np.random.default_rng(2), 18, launch)
    assert (orders[9:12, 0] == 0).all()  # region 3's cells before launch


def test_mde80_interpolates() -> None:
    assert ex.mde80([1, 2, 3], [0.5, 0.7, 0.9]) == 2.5
    assert ex.mde80([1, 2], [0.1, 0.2]) is None


def test_analytic_power_sanity() -> None:
    assert abs(ex.analytic_power(0.0, 18, 0.05) - 0.05) < 1e-9  # size = alpha under H0
    assert ex.analytic_power(3, 23, 0.05) > ex.analytic_power(3, 23, 0.05 / 8)
    assert ex.analytic_power(6, 18, 0.05 / 8) > ex.analytic_power(3, 18, 0.05 / 8)


def test_analytic_power_has_no_nan_at_large_effects() -> None:
    assert 0.999 < ex.analytic_power(8, 23, 0.05 / 8) <= 1.0


def test_decline_condition_runs_and_stays_positive() -> None:
    c = next(x for x in ex.CONDITIONS if x.name == "decline_3pct")
    rev, _ = ex.simulate_ext(np.random.default_rng(3), 24, c)
    assert (rev > 0).all()
