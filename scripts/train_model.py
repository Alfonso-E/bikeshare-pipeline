"""Baseline forecaster: predict a station's bike availability one hour ahead.

    python scripts/train_model.py
    python scripts/train_model.py --system nyc --min-hours 48

What this does and does not claim
--------------------------------
The split is temporal, never random. Rows are ordered by hour and the last 20%
is held out, so the model is always tested on time it has not seen. A random
split on a time series leaks the future into training through the lag features
and produces a beautiful, meaningless score.

Every feature is knowable at prediction time. Lags are taken at t and the
target is at t+1, so nothing from the future is on the input side.

Two baselines are reported alongside the model, and they matter more than the
model's own numbers: persistence (assume the next hour equals this hour) and
the station's historical mean for that hour-of-week. Bike availability is
strongly autocorrelated, so persistence is genuinely hard to beat. A model that
does not beat it has learned nothing worth deploying, and saying so is the
point of running them.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gbfs.config import DATA_DIR, WAREHOUSE_PATH  # noqa: E402
from gbfs.warehouse import connect  # noqa: E402

TARGET = "bikes_next_hour"
FEATURES = [
    "avg_bikes_available",
    "bikes_lag_1h",
    "bikes_lag_2h",
    "bikes_lag_3h",
    "bikes_rolling_3h",
    "avg_occupancy_rate",
    "pct_time_empty",
    "pct_time_full",
    "capacity",
    "hour_sin",
    "hour_cos",
    "is_weekend_int",
]


def load_hourly(system: str | None) -> pd.DataFrame:
    if not WAREHOUSE_PATH.exists():
        raise SystemExit(
            "no warehouse at {}. Run the ingester and `dbt build` first.".format(WAREHOUSE_PATH)
        )
    con = connect(read_only=True)
    try:
        sql = "SELECT * FROM main_marts.agg_station_hourly"
        params: list = []
        if system:
            sql += " WHERE system_key = ?"
            params.append(system)
        sql += " ORDER BY station_key, snapshot_hour"
        return con.execute(sql, params).df()
    finally:
        con.close()


def build_features(df: pd.DataFrame, min_hours: int) -> pd.DataFrame:
    df = df.sort_values(["station_key", "snapshot_hour"]).copy()

    # Stations with almost no history cannot support lag features and would
    # otherwise contribute rows that are mostly imputation.
    counts = df.groupby("station_key")["snapshot_hour"].transform("size")
    df = df[counts >= min_hours]
    if df.empty:
        raise SystemExit(
            "no station has {} hours of history yet. Let the ingester run "
            "longer, or lower --min-hours.".format(min_hours)
        )

    g = df.groupby("station_key")["avg_bikes_available"]
    df["bikes_lag_1h"] = g.shift(1)
    df["bikes_lag_2h"] = g.shift(2)
    df["bikes_lag_3h"] = g.shift(3)
    df["bikes_rolling_3h"] = g.shift(1).rolling(3, min_periods=1).mean()

    # Cyclical encoding: hour 23 and hour 0 are adjacent, and a raw integer
    # tells a linear model they are 23 apart.
    df["hour_sin"] = np.sin(2 * np.pi * df["hour_of_day"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour_of_day"] / 24)
    df["is_weekend_int"] = df["is_weekend"].astype(int)

    # The target: next hour's availability, within the same station.
    df[TARGET] = df.groupby("station_key")["avg_bikes_available"].shift(-1)

    # Persistence baseline is just the current hour carried forward.
    df["baseline_persistence"] = df["avg_bikes_available"]

    df["capacity"] = df["capacity"].fillna(df["capacity"].median())

    modelling = df.dropna(subset=FEATURES + [TARGET])
    if modelling.empty:
        raise SystemExit(
            "every row was dropped building lag features, which means the "
            "warehouse holds fewer than ~5 distinct hours.\nKeep the ingester "
            "running and re-run `dbt build`, then try again."
        )
    return modelling


def temporal_split(df: pd.DataFrame, test_fraction: float = 0.2):
    """Split on the time axis, globally -- not per station, and never randomly."""
    cutoff = df["snapshot_hour"].quantile(1 - test_fraction)
    train = df[df["snapshot_hour"] <= cutoff]
    test = df[df["snapshot_hour"] > cutoff]
    return train, test, cutoff


def evaluate(y_true, y_pred) -> dict:
    err = np.asarray(y_true) - np.asarray(y_pred)
    return {
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err**2))),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a 1-hour-ahead availability model.")
    parser.add_argument("--system", default=None, help="limit to one system key")
    parser.add_argument(
        "--min-hours",
        type=int,
        default=12,
        help="drop stations with fewer than this many hours of history",
    )
    parser.add_argument("--test-fraction", type=float, default=0.2)
    args = parser.parse_args()

    from sklearn.ensemble import HistGradientBoostingRegressor

    raw = load_hourly(args.system)
    print("loaded {:,} station-hours".format(len(raw)))

    df = build_features(raw, args.min_hours)
    train, test, cutoff = temporal_split(df, args.test_fraction)
    print("train {:,} rows | test {:,} rows | cutoff {}".format(len(train), len(test), cutoff))

    if len(test) < 50:
        print(
            "\nWARNING: only {} test rows. These numbers are not yet meaningful.\n"
            "Let the ingester run for a few days before drawing conclusions.".format(len(test))
        )

    model = HistGradientBoostingRegressor(
        max_depth=6, learning_rate=0.08, max_iter=300, random_state=42
    )
    model.fit(train[FEATURES], train[TARGET])
    predictions = model.predict(test[FEATURES])

    # Hour-of-week seasonal mean, learned on train only.
    seasonal = (
        train.assign(hour_of_week=train["day_of_week"] * 24 + train["hour_of_day"])
        .groupby(["station_key", "hour_of_week"])[TARGET]
        .mean()
    )
    test_how = test["day_of_week"] * 24 + test["hour_of_day"]
    seasonal_pred = (
        pd.MultiIndex.from_arrays([test["station_key"], test_how])
        .map(seasonal)
        .to_series()
        .fillna(train[TARGET].mean())
        .to_numpy()
    )

    results = {
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "cutoff": str(cutoff),
        "model_gbdt": evaluate(test[TARGET], predictions),
        "baseline_persistence": evaluate(test[TARGET], test["baseline_persistence"]),
        "baseline_seasonal_mean": evaluate(test[TARGET], seasonal_pred),
    }

    print("\n{:<24} {:>8} {:>8}".format("", "MAE", "RMSE"))
    for name in ("model_gbdt", "baseline_persistence", "baseline_seasonal_mean"):
        print(
            "{:<24} {:>8.3f} {:>8.3f}".format(
                name, results[name]["mae"], results[name]["rmse"]
            )
        )

    beat = results["model_gbdt"]["mae"] < results["baseline_persistence"]["mae"]
    results["beats_persistence"] = bool(beat)
    print(
        "\n=> the model {} persistence on MAE.".format("beats" if beat else "does NOT beat")
    )
    if not beat:
        print(
            "   That is a real result, not a failure to report. Availability is\n"
            "   highly autocorrelated at one-hour horizons; try a longer horizon,\n"
            "   more history, or weather features before adding model complexity."
        )

    out_dir = DATA_DIR / "models"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("\nmetrics written to {}".format(out_dir / "metrics.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
