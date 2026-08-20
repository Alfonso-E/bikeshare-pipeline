"""Data quality checks over the raw layer.

These are *contract* checks on what the API gave us, run right after ingestion.
They are deliberately separate from the dbt tests, which check the modelled
marts. The split matters: a failure here means the upstream feed changed or
broke, a dbt test failure means our own transformation logic is wrong.

Each check returns a Check with a severity. `error` fails the run; `warn` is
recorded in the report but does not block, because real feeds are messy and a
pipeline that halts on every anomaly gets switched off by whoever operates it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import QUALITY_DIR, RAW_DIR
from .warehouse import connect, register_raw_views


@dataclass
class Check:
    name: str
    passed: bool
    severity: str  # "error" | "warn"
    observed: object
    detail: str


def _scalar(con, sql: str):
    row = con.execute(sql).fetchone()
    return row[0] if row else None


def run_checks(raw_dir: Path = RAW_DIR) -> list:
    con = connect()
    try:
        register_raw_views(con, raw_dir)
        checks = []

        # 1. Did we get any rows at all?
        row_count = _scalar(con, "SELECT count(*) FROM raw.station_status")
        checks.append(
            Check(
                "status_not_empty",
                bool(row_count),
                "error",
                row_count,
                "raw.station_status must contain rows",
            )
        )
        if not row_count:
            return checks

        # 2. Primary key: one row per (system, station, snapshot).
        dupes = _scalar(
            con,
            """
            SELECT count(*) FROM (
                SELECT system_key, station_id, feed_last_updated
                FROM raw.station_status
                GROUP BY 1, 2, 3
                HAVING count(*) > 1
            )
            """,
        )
        checks.append(
            Check(
                "status_grain_unique",
                dupes == 0,
                "error",
                dupes,
                "(system_key, station_id, feed_last_updated) must be unique",
            )
        )

        # 3. No null station ids -- nothing downstream can join without one.
        null_ids = _scalar(
            con, "SELECT count(*) FROM raw.station_status WHERE station_id IS NULL"
        )
        checks.append(
            Check("station_id_not_null", null_ids == 0, "error", null_ids, "station_id must be set")
        )

        # 4. Counts cannot be negative.
        negatives = _scalar(
            con,
            """
            SELECT count(*) FROM raw.station_status
            WHERE num_bikes_available < 0 OR num_docks_available < 0
            """,
        )
        checks.append(
            Check(
                "counts_non_negative",
                negatives == 0,
                "error",
                negatives,
                "bike and dock counts must be >= 0",
            )
        )

        # 5. Freshness. If the newest snapshot is old, the scheduler is dead.
        age_minutes = _scalar(
            con,
            """
            SELECT date_diff('minute', max(snapshot_ts), now()::TIMESTAMP WITH TIME ZONE)
            FROM raw.station_status
            """,
        )
        checks.append(
            Check(
                "raw_freshness",
                age_minutes is not None and age_minutes <= 120,
                "warn",
                age_minutes,
                "newest snapshot should be under 120 minutes old",
            )
        )

        # 6. Stations reporting a bogus last_reported. Citi Bike emits 86400 for
        #    dead docks; it is not an error, but a spike means something changed.
        bogus = _scalar(
            con,
            """
            SELECT count(*) FROM raw.station_status
            WHERE last_reported IS NOT NULL AND last_reported < 1000000000
            """,
        )
        checks.append(
            Check(
                "last_reported_plausible",
                bogus == 0,
                "warn",
                bogus,
                "last_reported should look like a unix timestamp",
            )
        )

        # 7. Referential integrity against station_information.
        orphans = _scalar(
            con,
            """
            SELECT count(DISTINCT s.station_id)
            FROM raw.station_status s
            LEFT JOIN (SELECT DISTINCT system_key, station_id FROM raw.station_information) i
                   ON s.system_key = i.system_key AND s.station_id = i.station_id
            WHERE i.station_id IS NULL
            """,
        )
        checks.append(
            Check(
                "status_stations_known",
                orphans == 0,
                "warn",
                orphans,
                "every status station should appear in station_information",
            )
        )

        return checks
    finally:
        con.close()


def write_report(checks: list, out_dir: Path = QUALITY_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc)
    report = {
        "generated_at": stamp.isoformat(),
        "passed": all(c.passed for c in checks if c.severity == "error"),
        "checks": [asdict(c) for c in checks],
    }
    path = out_dir / "report_{}.json".format(stamp.strftime("%Y%m%dT%H%M%SZ"))
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out_dir / "latest.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    return path


def has_blocking_failure(checks: list) -> bool:
    return any(not c.passed and c.severity == "error" for c in checks)
