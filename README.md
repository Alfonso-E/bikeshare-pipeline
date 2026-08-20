# Bike-share availability pipeline

A scheduled data pipeline that collects live bike-share station data, models it
into a tested warehouse, and serves it as a dashboard and a forecast.

The dataset does not exist until this runs. GBFS feeds publish only *current*
state — there is no historical archive to download — so the history is built by
snapshotting the feed on a schedule and keeping every observation.

```
GBFS API  ──▶  Parquet lake  ──▶  DuckDB + dbt  ──▶  marts  ──▶  Streamlit
 (live)        (raw, append-      (staging /            │        forecast
                only, hive-        intermediate /       │
                partitioned)       marts, 42 tests)     └──▶  scikit-learn
```

## Why this project

Most portfolio projects load a static CSV, so they never touch the parts of the
job that are actually hard: data that arrives continuously, schemas that change
without warning, checks that catch a broken upstream before it reaches a
dashboard, and the discipline to test a time series without leaking the future.
This one is built around those.

## Quickstart

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Take a snapshot, build the warehouse, check it:

```bash
python scripts/run_ingest.py
cd dbt && dbt build --profiles-dir . && cd ..
python scripts/run_quality.py
```

Look at it:

```bash
streamlit run app/dashboard.py
```

Train the forecaster (needs roughly a day of collection first):

```bash
python scripts/train_model.py --system nyc
```

## Layout

| Path | What it is |
| --- | --- |
| `config/systems.yml` | Which GBFS systems to poll. NYC is on; three more are configured and off. |
| `src/gbfs/` | Discovery, ingestion, warehouse helpers, quality checks. |
| `scripts/` | CLI entrypoints: ingest, quality, train. |
| `dbt/` | staging → intermediate → marts, plus tests and the timezone seed. |
| `app/dashboard.py` | Streamlit dashboard over the marts. |
| `tests/` | pytest — normalisers and the leakage-sensitive modelling logic. |
| `.github/workflows/` | `ci.yml` on every push, `ingest.yml` every 30 minutes. |

## Design decisions

**The raw layer is append-only and never cleaned.** Snapshots land as Parquet
partitioned by `system=` and `date=`, exactly as the API returned them. All
cleaning happens in dbt, which means a mistake in the cleaning logic is a
`dbt build` away from being fixed rather than an unrecoverable data loss.

**Ingestion is idempotent.** Each file is named for the feed's own
`last_updated` value, so a retry inside one TTL window is a no-op instead of a
duplicate row. Schedulers retry; the pipeline has to be boring about it.

**Timezones are handled explicitly.** Snapshots are stored in UTC, but
`hour_of_day` is derived after converting to each system's local timezone via
the `system_timezones` seed. Extracting the hour directly from a UTC timestamp
returns whatever timezone the machine running dbt is set to — the kind of bug
that produces plausible charts and only surfaces when someone else runs it.

**Every table declares its grain.** `fct_station_availability` is one row per
station per feed tick, `agg_station_hourly` is one row per station per local
hour, and both assert it with a `unique_combination` test. When one starts
failing, a join has fanned out or a snapshot has been double-written.

**Quality checks are split by what a failure means.** The raw-layer checks in
`src/gbfs/quality.py` are contract checks on the upstream API; a failure means
the *feed* changed. The dbt tests check the models; a failure means *our* logic
is wrong. Warn-level checks are recorded but don't halt the run, because a
pipeline that stops on every real-world anomaly gets switched off.

## Known limitations

These are properties of the source data, not things left to finish.

**Trips are inferred, not observed.** GBFS publishes inventory levels, never
trips. `net_bike_change` differences consecutive snapshots, so a departure and
an arrival inside the same interval cancel out and turnover is undercounted.
Real trip counts would need the operator's monthly trip-history exports.

**Rebalancing looks like demand.** A truck dropping off twelve bikes is
indistinguishable from twelve riders returning them. Large positive jumps at
low-traffic hours are the signature, but nothing here separates the two.

**Coverage has gaps.** GitHub's scheduled runners are delayed under load and
pause on repos idle for 60 days. `snapshot_count` on every hourly row exposes
how thin that hour is, so models can drop the sparse ones instead of quietly
averaging over them.

**Storage grows.** Roughly 30 kB per snapshot, about 0.5 GB per year for one
system at 48 snapshots a day. Fine for a portfolio repo; when it stops being
fine, point the raw directory at object storage — dbt reads a glob either way.

## Modelling notes

`scripts/train_model.py` predicts a station's availability one hour ahead.

The split is temporal and global: rows are ordered by hour and the final 20% is
held out. A random split leaks the future through the lag features and produces
an excellent, meaningless score.

Two baselines are reported next to the model, and they matter more than its own
numbers — persistence (next hour equals this hour) and the station's historical
mean for that hour-of-week. Availability is strongly autocorrelated at a
one-hour horizon, so persistence is genuinely hard to beat, and the script says
plainly when the model fails to. A model that doesn't beat persistence has
learned nothing worth deploying; reporting that is the point.

`tests/test_model.py` asserts the properties that fail silently when broken:
lags never cross station boundaries, the target really is `t+1`, no feature is
identical to the target, and train always precedes test.

## Adding a city

Set `enabled: true` in `config/systems.yml` and add a row to
`dbt/seeds/system_timezones.csv`. Discovery URLs come from the
[MobilityData GBFS catalog](https://github.com/MobilityData/gbfs/blob/master/systems.csv).
The ingester handles both the v1/v2 language-nested and v3 flat feed shapes.

## Status

Verified end to end on 2026-08-20: 26 pytest tests pass, `dbt build` is 42/42,
quality checks pass with one expected warning (Citi Bike emits a placeholder
`last_reported` for dead docks). The forecaster is wired and unit-tested but
needs a few days of collected history before its numbers mean anything.
