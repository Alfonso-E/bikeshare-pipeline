# Bike-share collector

Scheduled collection of live bike-share station data into a Parquet lake.

This repo is **collection only** — the raw material, not the analysis. GBFS
feeds publish only *current* state, with no historical archive to download, so
the history has to be built by snapshotting the feed on a schedule and keeping
every observation.

```
GBFS API  ──▶  Parquet lake  ──▶  (your analysis lives downstream)
 (live)        append-only, hive-partitioned
```

## Quickstart

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python scripts/run_ingest.py
python scripts/run_quality.py
```

Then read the lake with whatever you like:

```python
import duckdb
df = duckdb.sql("""
    SELECT * FROM read_parquet(
        'data/raw/station_status/*/*/*.parquet',
        hive_partitioning = true, union_by_name = true
    )
""").df()
```

`hive_partitioning` turns the `system=` and `date=` directory names into real
columns, so filtering on them skips files instead of scanning the whole lake.

## Getting it collecting

Push to GitHub and `ingest.yml` snapshots every 30 minutes on its own, then
commits the new Parquet files back to the repo:

```bash
gh repo create bikeshare-pipeline --public --source=. --push
```

Give it a few days before expecting daily patterns to be visible.

## What lands on disk

```
data/raw/
  station_status/system=nyc/date=2026-08-20/status_<feed_last_updated>.parquet
  station_information/system=nyc/date=2026-08-20/info.parquet
```

**`station_status`** — one row per station per feed tick, every run.

| column | type | notes |
| --- | --- | --- |
| `system_key` | str | from `config/systems.yml` |
| `snapshot_ts` | timestamp, UTC | when *we* fetched |
| `feed_last_updated` | int64 | unix seconds, the feed's own stamp |
| `station_id` | str | stringified — some systems emit ints |
| `num_bikes_available` | int32 | |
| `num_ebikes_available` | int32 | null if the feed doesn't say |
| `num_bikes_disabled` | int32 | |
| `num_docks_available` | int32 | |
| `num_docks_disabled` | int32 | |
| `is_installed` | bool | |
| `is_renting` | bool | |
| `is_returning` | bool | |
| `last_reported` | int64 | unix seconds, as the station reported it |

**`station_information`** — one row per station, once per day.

| column | type |
| --- | --- |
| `system_key`, `snapshot_ts`, `station_id` | str / timestamp / str |
| `name` | str |
| `lat`, `lon` | float64 |
| `capacity` | int32 |
| `region_id` | str |
| `station_type` | str |

Fields are normalised to a stable schema and stable types, so files stay
mergeable across days. Nothing is cleaned, filtered, or corrected beyond that —
a field the feed didn't send is null, not zero. What's in the lake is what the
API said.

## Design decisions

**Append-only, never rewritten.** The lake is a faithful log of what the API
returned at a point in time. Anything judgemental happens downstream, which
means a mistake in your cleaning logic is fixable rather than baked in.

**Idempotent.** Each file is named for the feed's own `last_updated`, so a retry
inside one TTL window is a no-op instead of a duplicate row. Schedulers retry;
the collector has to be boring about it.

**Atomic writes.** Parquet is written to a temp name and renamed, so a killed
process can't leave a half-written file that breaks every later read.

**Timestamps are UTC, deliberately.** No local-time columns are derived here.
Deriving them is a real decision with a real trap in it, and it belongs to
whoever is doing the analysis.

**Integrity checks, not data quality checks.** `scripts/run_quality.py` verifies
that *collection* worked: snapshots arrived, none were written twice, the
scheduler is alive. It says nothing about whether the contents are plausible.
That's yours to find out.

## Things to know before you trust the series

- **Coverage will have gaps.** GitHub schedules are delayed under load and pause
  on repos idle for 60 days. Count observations per hour before assuming a
  regular interval.
- **The feed's tick rate is not your sample rate.** `ttl` is 60s for most
  systems; collecting every 30 minutes means you see one moment per half hour,
  not an average of it.
- **Trips are not in here.** GBFS publishes inventory levels only. Any notion of
  flow has to be inferred, with the limitations that implies.
- **Storage grows** at roughly 30 kB per snapshot — about 0.5 GB a year for one
  system at 48 snapshots a day.

## Adding a city

Set `enabled: true` in `config/systems.yml`. Discovery URLs come from the
[MobilityData GBFS catalog](https://github.com/MobilityData/gbfs/blob/master/systems.csv).
The collector handles both the v1/v2 language-nested and v3 flat feed shapes;
`tests/test_ingest.py` covers both.

## Tests

```bash
pytest tests -q
```

18 tests over the normalisers and feed-shape handling. Every case is a real
inconsistency seen in live GBFS feeds, not a hypothetical.

## Status

Verified end to end on 2026-08-20 against Citi Bike NYC: 8 snapshots collected,
2,508 stations, 20,064 rows. Tests pass, integrity checks pass.
