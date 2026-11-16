"""Canonical result hash (ARCHITECTURE §7), spike version.

Rows are sorted, floats are rounded to 10 significant digits, and column names and types are
part of the hash, so a DOUBLE 1.0 and a DECIMAL 1.00 never collide.
"""

import datetime as dt
import hashlib
import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

FLOAT_SIG_DIGITS = 10


def _norm(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"b:{value}"
    if isinstance(value, float):
        if value != value:
            return "f:nan"
        if value == 0:
            value = 0.0  # -0.0 and 0.0 hash the same
        return f"f:{value:.{FLOAT_SIG_DIGITS}g}"
    if isinstance(value, int):
        return f"i:{value}"
    if isinstance(value, Decimal):
        return f"d:{value.normalize():f}"
    if isinstance(value, dt.datetime | dt.date):
        return f"t:{value.isoformat()}"
    if isinstance(value, str):
        return f"s:{value}"
    raise TypeError(f"unsupported type in result: {type(value).__name__}")


def canonical_hash(columns: Sequence[tuple[str, str]], rows: Sequence[Sequence[Any]]) -> str:
    """Hash of (column name, type) pairs plus the sorted, normalized rows."""
    norm_rows = sorted([_norm(v) for v in row] for row in rows)
    payload = json.dumps({"columns": [list(c) for c in columns], "rows": norm_rows})
    return hashlib.sha256(payload.encode()).hexdigest()


def raw_hash(rows: Sequence[Sequence[Any]]) -> str:
    """Order-sensitive, unrounded hash, used only to see how much the raw output moves."""
    return hashlib.sha256(repr(list(rows)).encode()).hexdigest()
