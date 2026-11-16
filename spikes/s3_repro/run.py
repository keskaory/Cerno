"""Spike S3: is the canonical result hash stable? Criteria in docs/M1_SPIKES.md.

Usage (from backend/):  uv run python ../spikes/s3_repro/run.py [--rows 2000000] [--reps 50]
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from canon import canonical_hash, raw_hash

OUT = Path(__file__).parent / "out"

QUERIES: dict[str, str] = {
    "q1_sum_double": "SELECT region, SUM(amount) AS s FROM orders GROUP BY region",
    "q2_sum_decimal": "SELECT region, SUM(amount_dec) AS s FROM orders GROUP BY region",
    "q3_avg": "SELECT channel, AVG(amount) AS a FROM orders GROUP BY channel",
    # Many customers share the same order count, so LIMIT cuts through a tie.
    "q4_ties_limit": (
        "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id "
        "ORDER BY n DESC LIMIT 20"
    ),
    # What the compiler will emit: a total order (ties broken by key).
    "q4b_total_order_limit": (
        "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id "
        "ORDER BY n DESC, customer_id LIMIT 20"
    ),
    "q5_count_distinct": (
        "SELECT region, COUNT(DISTINCT customer_id) AS c FROM orders GROUP BY region"
    ),
    "q6_ratio": (
        "SELECT date_trunc('month', order_date) AS m, region, "
        "SUM(amount_dec) / SUM(items) AS per_item FROM orders GROUP BY m, region"
    ),
}


def locked_config(threads: int, temp_dir: str) -> dict[str, Any]:
    """The production worker config (ARCHITECTURE §4.2), with threads as a parameter."""
    return {
        "access_mode": "READ_ONLY",
        "enable_external_access": False,
        "autoload_known_extensions": False,
        "autoinstall_known_extensions": False,
        "allow_community_extensions": False,
        "allow_persistent_secrets": False,
        "threads": threads,
        "temp_directory": temp_dir,
        "max_temp_directory_size": "512MB",
    }


def open_locked(db: Path, threads: int, temp_dir: str) -> duckdb.DuckDBPyConnection:
    # TimeZone is an ICU setting. DuckDB 2.0 rejects it in connect(config=...) once autoload
    # or external access is off, so it is SET after connect, and only then is the config locked.
    con = duckdb.connect(str(db), config=locked_config(threads, temp_dir))
    con.execute("SET TimeZone = 'UTC'")
    con.execute("SET lock_configuration = true")
    return con


def generate_parquet(path: Path, rows: int, seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    regions = np.array([f"r{i}" for i in range(8)])
    channels = np.array(["web", "app", "store"])
    start = np.datetime64("2024-01-01")
    amount = rng.lognormal(mean=3.5, sigma=0.9, size=rows)
    table = pa.table(
        {
            "order_id": np.arange(rows, dtype=np.int64),
            "customer_id": rng.integers(0, rows // 8, size=rows, dtype=np.int64),
            "region": regions[rng.integers(0, 8, size=rows)],
            "channel": channels[rng.integers(0, 3, size=rows)],
            "order_date": start + rng.integers(0, 730, size=rows).astype("timedelta64[D]"),
            "amount": amount,
            "items": rng.integers(1, 6, size=rows, dtype=np.int32),
        }
    )
    pq.write_table(table, path)


def build_db(parquet: Path, db: Path) -> None:
    """Parquet -> DuckDB file. Mirrors task 2.2: write to a temp name, then rename."""
    tmp = db.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute(
        f"CREATE TABLE orders AS SELECT *, CAST(round(amount, 2) AS DECIMAL(18,2)) AS amount_dec "
        f"FROM read_parquet('{parquet.as_posix()}') ORDER BY order_id"
    )
    con.close()
    os.replace(tmp, db)


def run_hashes(db: Path, threads: int, reps: int) -> dict[str, dict[str, list[str]]]:
    """For each query, the distinct canonical and raw hashes seen over `reps` runs."""
    with tempfile.TemporaryDirectory() as td:
        con = open_locked(db, threads, td)
        out: dict[str, dict[str, list[str]]] = {}
        for name, sql in QUERIES.items():
            canon: set[str] = set()
            raw: set[str] = set()
            for _ in range(reps):
                cur = con.execute(sql)
                cols = [(d[0], str(d[1])) for d in cur.description or []]
                rows = cur.fetchall()
                canon.add(canonical_hash(cols, rows))
                raw.add(raw_hash(rows))
            out[name] = {"canonical": sorted(canon), "raw": sorted(raw)}
        con.close()
    return out


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def child_main(db: Path, threads: int, reps: int) -> None:
    print(json.dumps(run_hashes(db, threads, reps)))


def run_child(db: Path, threads: int, reps: int) -> dict[str, dict[str, list[str]]]:
    res = subprocess.run(
        [
            sys.executable,
            __file__,
            "--child",
            str(db),
            "--threads",
            str(threads),
            "--reps",
            str(reps),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    result: dict[str, dict[str, list[str]]] = json.loads(res.stdout)
    return result


def single(h: dict[str, dict[str, list[str]]], kind: str) -> dict[str, str | None]:
    """The one hash per query, or None if the query produced more than one."""
    return {q: (v[kind][0] if len(v[kind]) == 1 else None) for q, v in h.items()}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=int, default=2_000_000)
    p.add_argument("--reps", type=int, default=50)
    p.add_argument("--threads", type=int, default=1)
    p.add_argument("--child", type=Path)
    a = p.parse_args()
    if a.child:
        child_main(a.child, a.threads, a.reps)
        return

    OUT.mkdir(exist_ok=True)
    parquet, db_a, db_b = OUT / "orders.parquet", OUT / "a.duckdb", OUT / "b.duckdb"
    t0 = time.perf_counter()
    generate_parquet(parquet, a.rows)
    build_db(parquet, db_a)
    build_db(parquet, db_b)
    t_build = time.perf_counter() - t0

    t0 = time.perf_counter()
    h1 = run_hashes(db_a, threads=1, reps=a.reps)
    t_runs = time.perf_counter() - t0
    h_rebuild = run_hashes(db_b, threads=1, reps=1)
    h_proc1 = run_child(db_a, threads=1, reps=1)
    h_proc2 = run_child(db_a, threads=1, reps=1)
    h4 = run_hashes(db_a, threads=4, reps=a.reps)

    base = single(h1, "canonical")
    crit1 = all(v is not None for v in base.values())
    crit2 = crit1 and single(h_rebuild, "canonical") == base
    crit3 = crit1 and single(h_proc1, "canonical") == base == single(h_proc2, "canonical")

    report = {
        "duckdb_version": duckdb.__version__,
        "rows": a.rows,
        "reps": a.reps,
        "build_seconds": round(t_build, 1),
        "threads1_run_seconds": round(t_runs, 1),
        "db_files_byte_identical": file_sha(db_a) == file_sha(db_b),
        "criteria": {
            "1_stable_50_runs": crit1,
            "2_stable_after_rebuild": crit2,
            "3_stable_across_processes": crit3,
        },
        "pass": crit1 and crit2 and crit3,
        "distinct_hashes_threads1": {
            q: {"canonical": len(v["canonical"]), "raw": len(v["raw"])} for q, v in h1.items()
        },
        "distinct_hashes_threads4": {
            q: {"canonical": len(v["canonical"]), "raw": len(v["raw"])} for q, v in h4.items()
        },
        "threads4_canonical_matches_threads1": {
            q: (len(v["canonical"]) == 1 and v["canonical"][0] == base[q]) for q, v in h4.items()
        },
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
