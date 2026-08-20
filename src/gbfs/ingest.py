"""Snapshot GBFS feeds to partitioned Parquet (the raw/bronze layer).

Design notes
------------
* Append-only. A snapshot is never rewritten, so the raw layer stays a faithful
  log of what the API actually said at a point in time. All cleaning happens
  downstream in dbt, which means a bug in the cleaning logic is recoverable.
* Idempotent. The filename is keyed on the feed's own last_updated stamp, so
  re-running the ingester inside one TTL window is a no-op rather than a
  duplicate row. This matters because schedulers retry.
* Schema is normalised at write time only as far as needed to keep Parquet
  files mergeable across days (stable column set, stable types). Anything
  judgemental -- outlier handling, dedup, derived metrics -- is left to dbt.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .config import RAW_DIR, Config, System
from .discover import fetch_feed, resolve_feeds

log = logging.getLogger(__name__)

# Stable column set for station_status snapshots. Feeds that omit a field get
# nulls rather than a ragged schema.
STATUS_SCHEMA = pa.schema(
    [
        ("system_key", pa.string()),
        ("snapshot_ts", pa.timestamp("us", tz="UTC")),
        ("feed_last_updated", pa.int64()),
        ("station_id", pa.string()),
        ("num_bikes_available", pa.int32()),
        ("num_ebikes_available", pa.int32()),
        ("num_bikes_disabled", pa.int32()),
        ("num_docks_available", pa.int32()),
        ("num_docks_disabled", pa.int32()),
        ("is_installed", pa.bool_()),
        ("is_renting", pa.bool_()),
        ("is_returning", pa.bool_()),
        ("last_reported", pa.int64()),
    ]
)

INFO_SCHEMA = pa.schema(
    [
        ("system_key", pa.string()),
        ("snapshot_ts", pa.timestamp("us", tz="UTC")),
        ("station_id", pa.string()),
        ("name", pa.string()),
        ("lat", pa.float64()),
        ("lon", pa.float64()),
        ("capacity", pa.int32()),
        ("region_id", pa.string()),
        ("station_type", pa.string()),
    ]
)


def _as_bool(value):
    """GBFS is inconsistent: is_renting shows up as 0/1, true/false, or "1"."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return None


def _as_int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _count_ebikes(station: dict):
    """v2 exposes num_ebikes_available; v3 uses a vehicle_types_available list."""
    if "num_ebikes_available" in station:
        return _as_int(station["num_ebikes_available"])

    types = station.get("vehicle_types_available")
    if isinstance(types, list):
        total = 0
        found = False
        for entry in types:
            type_id = str(entry.get("vehicle_type_id", "")).lower()
            if "electric" in type_id or "ebike" in type_id:
                total += _as_int(entry.get("count")) or 0
                found = True
        if found:
            return total
    return None


def normalise_status(payload: dict, system: System, snapshot_ts: datetime) -> list:
    stations = payload.get("data", {}).get("stations", [])
    feed_last_updated = _as_int(payload.get("last_updated"))
    rows = []
    for s in stations:
        station_id = s.get("station_id")
        rows.append(
            {
                "system_key": system.key,
                "snapshot_ts": snapshot_ts,
                "feed_last_updated": feed_last_updated,
                "station_id": str(station_id) if station_id is not None else None,
                "num_bikes_available": _as_int(s.get("num_bikes_available")),
                "num_ebikes_available": _count_ebikes(s),
                "num_bikes_disabled": _as_int(s.get("num_bikes_disabled")),
                "num_docks_available": _as_int(s.get("num_docks_available")),
                "num_docks_disabled": _as_int(s.get("num_docks_disabled")),
                "is_installed": _as_bool(s.get("is_installed")),
                "is_renting": _as_bool(s.get("is_renting")),
                "is_returning": _as_bool(s.get("is_returning")),
                "last_reported": _as_int(s.get("last_reported")),
            }
        )
    return rows


def normalise_information(payload: dict, system: System, snapshot_ts: datetime) -> list:
    stations = payload.get("data", {}).get("stations", [])
    rows = []
    for s in stations:
        station_id = s.get("station_id")
        region_id = s.get("region_id")
        rows.append(
            {
                "system_key": system.key,
                "snapshot_ts": snapshot_ts,
                "station_id": str(station_id) if station_id is not None else None,
                "name": s.get("name"),
                "lat": s.get("lat"),
                "lon": s.get("lon"),
                "capacity": _as_int(s.get("capacity")),
                "region_id": str(region_id) if region_id is not None else None,
                "station_type": s.get("station_type"),
            }
        )
    return rows


def _write(rows: list, schema: pa.Schema, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=schema)
    # Write to a temp name then rename, so a killed process never leaves a
    # half-written Parquet file that breaks every downstream read.
    tmp = path.with_suffix(".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)
    return table.num_rows


def ingest_system(system: System, config: Config, raw_dir: Path = RAW_DIR) -> dict:
    """Take one snapshot of one system. Returns a small result summary."""
    snapshot_ts = datetime.now(timezone.utc)
    date_partition = snapshot_ts.strftime("%Y-%m-%d")
    feeds = resolve_feeds(system, config)

    result = {"system": system.key, "snapshot_ts": snapshot_ts.isoformat()}

    # --- station_status: every run ---
    if "station_status" not in feeds:
        raise KeyError(
            "{}: feed has no station_status; got {}".format(system.key, sorted(feeds))
        )

    status_payload = fetch_feed(feeds["station_status"], config)
    feed_stamp = _as_int(status_payload.get("last_updated")) or int(snapshot_ts.timestamp())
    status_path = (
        raw_dir
        / "station_status"
        / ("system=" + system.key)
        / ("date=" + date_partition)
        / "status_{}.parquet".format(feed_stamp)
    )
    if status_path.exists():
        log.info("%s: feed unchanged (last_updated=%s), skipping", system.key, feed_stamp)
        result["status_rows"] = 0
        result["status_skipped"] = True
    else:
        rows = normalise_status(status_payload, system, snapshot_ts)
        result["status_rows"] = _write(rows, STATUS_SCHEMA, status_path)
        result["status_skipped"] = False
        log.info(
            "%s: wrote %s status rows -> %s",
            system.key,
            result["status_rows"],
            status_path.name,
        )

    # --- station_information: once a day, it barely changes ---
    info_path = (
        raw_dir
        / "station_information"
        / ("system=" + system.key)
        / ("date=" + date_partition)
        / "info.parquet"
    )
    if info_path.exists():
        result["info_rows"] = 0
        result["info_skipped"] = True
    else:
        info_payload = fetch_feed(feeds["station_information"], config)
        rows = normalise_information(info_payload, system, snapshot_ts)
        result["info_rows"] = _write(rows, INFO_SCHEMA, info_path)
        result["info_skipped"] = False
        log.info("%s: wrote %s station rows -> %s", system.key, result["info_rows"], info_path)

    return result
