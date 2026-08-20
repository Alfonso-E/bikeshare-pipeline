"""DuckDB warehouse helpers.

dbt reads the Parquet lake directly (see dbt/models/staging/_sources.yml), so
this module is not on the critical path of the transform. It exists so that
ad-hoc SQL, the quality checks, and the Streamlit app can all open the same
database and query the raw layer without re-deriving glob patterns.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from .config import RAW_DIR, WAREHOUSE_PATH

STATUS_GLOB = "station_status/*/*/*.parquet"
INFO_GLOB = "station_information/*/*/*.parquet"


def connect(read_only: bool = False, path: Path = WAREHOUSE_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def register_raw_views(con, raw_dir: Path = RAW_DIR) -> None:
    """(Re)create views over the Parquet lake.

    hive_partitioning pulls system= and date= out of the directory names, so
    filtering on them prunes files instead of scanning the whole lake.
    """
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")

    # DuckDB cannot bind a parameter inside CREATE VIEW, so the path is
    # interpolated. It comes from config, not user input, but escape quotes
    # anyway rather than leave a string-building habit in the codebase.
    for view, glob in (
        ("station_status", STATUS_GLOB),
        ("station_information", INFO_GLOB),
    ):
        path_literal = (raw_dir / glob).as_posix().replace("'", "''")
        con.execute(
            """
            CREATE OR REPLACE VIEW raw.{view} AS
            SELECT * FROM read_parquet(
                '{path}', hive_partitioning = true, union_by_name = true
            )
            """.format(view=view, path=path_literal)
        )


def raw_is_populated(raw_dir: Path = RAW_DIR) -> bool:
    return any((raw_dir / "station_status").rglob("*.parquet"))
