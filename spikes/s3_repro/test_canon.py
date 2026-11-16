import datetime as dt
from decimal import Decimal

import pytest
from canon import canonical_hash

COLS = [("region", "VARCHAR"), ("s", "DOUBLE")]


def test_row_order_does_not_matter() -> None:
    rows = [("a", 1.5), ("b", 2.5)]
    assert canonical_hash(COLS, rows) == canonical_hash(COLS, list(reversed(rows)))


def test_float_noise_below_10_sig_digits_is_ignored() -> None:
    a = canonical_hash(COLS, [("a", 1234.567890123)])
    b = canonical_hash(COLS, [("a", 1234.567890124)])
    assert a == b


def test_float_change_at_10th_digit_is_detected() -> None:
    a = canonical_hash(COLS, [("a", 1234.567890)])
    b = canonical_hash(COLS, [("a", 1234.567891)])
    assert a != b


def test_negative_zero_equals_zero() -> None:
    assert canonical_hash(COLS, [("a", -0.0)]) == canonical_hash(COLS, [("a", 0.0)])


def test_types_are_part_of_the_hash() -> None:
    dec = canonical_hash([("s", "DECIMAL(18,2)")], [(Decimal("1.00"),)])
    dbl = canonical_hash([("s", "DOUBLE")], [(1.0,)])
    assert dec != dbl
    # Same declared type, different Python value types still differ.
    assert canonical_hash(COLS, [("a", 1.0)]) != canonical_hash(COLS, [("a", 1)])


def test_decimal_trailing_zeros_normalized() -> None:
    cols = [("s", "DECIMAL(18,2)")]
    assert canonical_hash(cols, [(Decimal("1.50"),)]) == canonical_hash(cols, [(Decimal("1.5"),)])


def test_null_and_dates() -> None:
    cols = [("d", "DATE"), ("x", "DOUBLE")]
    h = canonical_hash(cols, [(dt.date(2025, 1, 1), None)])
    assert h != canonical_hash(cols, [(dt.date(2025, 1, 2), None)])


def test_unknown_type_rejected() -> None:
    with pytest.raises(TypeError):
        canonical_hash(COLS, [("a", object())])
