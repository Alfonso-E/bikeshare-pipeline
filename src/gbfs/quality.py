"""Integrity checks on the collector itself.

Scope note: these check that *collection* worked -- that snapshots arrived, that
none were written twice, that the scheduler is still alive. They deliberately
do NOT characterise the contents of the data. Whether a field is plausible,
whether categories are consistent, whether stations go missing -- that is Data
Understanding, and finding it out is the analyst's job, not the collector's.

Each check returns a Check with a severity. `error` fails the run; `warn` is
recorded but does not block, because a pipeline that halts on every anomaly
gets switched off by whoever operates it.
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

        # 1. Did anything arrive at all?
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

        # 2. Did we write the same feed tick twice? This is the check that
        #    catches a broken idempotency key or an overlapping scheduler run.
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

        # 3. Rows without a station id cannot be joined to anything later.
        null_ids = _scalar(
            con, "SELECT count(*) FROM raw.station_status WHERE station_id IS NULL"
        )
        checks.append(
            Check(
                "station_id_not_null",
                null_ids == 0,
                "error",
                null_ids,
                "station_id must be set",
            )
        )

        # 4. Is the scheduler still alive? A stale newest snapshot means
        #    collection stopped, which is silent otherwise.
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

        # 5. Every system in the config should actually be producing data.
        systems_with_data = _scalar(
            con, "SELECT count(DISTINCT system_key) FROM raw.station_status"
        )
        checks.append(
            Check(
                "systems_reporting",
                bool(systems_with_data),
                "warn",
                systems_with_data,
                "at least one enabled system should have snapshots",
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
