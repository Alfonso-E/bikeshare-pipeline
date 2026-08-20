"""Tests for the feature/split logic in scripts/train_model.py.

The pipeline cannot yet produce multiple days of real history, so these run on
a synthetic frame. That is deliberate: the properties worth testing here --
that lags are per station, that the split is temporal, that no future value
reaches the feature side -- are exactly the ones that produce a silently
inflated score when they break, and they are testable without real data.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from train_model import TARGET, build_features, evaluate, temporal_split  # noqa: E402


def synthetic_hourly(n_stations: int = 3, n_hours: int = 72) -> pd.DataFrame:
    hours = pd.date_range("2026-01-01", periods=n_hours, freq="h")
    rows = []
    for s in range(n_stations):
        for i, hour in enumerate(hours):
            rows.append(
                {
                    "station_key": "station_{}".format(s),
                    "system_key": "test",
                    "station_id": str(s),
                    "station_name": "Station {}".format(s),
                    "snapshot_hour": hour,
                    "day_of_week": hour.dayofweek,
                    "hour_of_day": hour.hour,
                    "is_weekend": hour.dayofweek in (5, 6),
                    "capacity": 20,
                    "snapshot_count": 60,
                    "avg_bikes_available": 10 + 5 * np.sin(i / 6) + s,
                    "avg_ebikes_available": 2.0,
                    "avg_docks_available": 10.0,
                    "avg_occupancy_rate": 0.5,
                    "pct_time_empty": 0.0,
                    "pct_time_full": 0.0,
                }
            )
    return pd.DataFrame(rows)


def test_lag_features_never_cross_station_boundaries():
    df = build_features(synthetic_hourly(), min_hours=12)
    for key, group in df.groupby("station_key"):
        group = group.sort_values("snapshot_hour")
        # lag_1h at row i must equal avg_bikes_available at row i-1 of the
        # SAME station. If groupby were missing, it would pick up the previous
        # station's last value at each boundary.
        expected = group["avg_bikes_available"].shift(1).dropna()
        actual = group["bikes_lag_1h"].loc[expected.index]
        assert np.allclose(actual, expected), key


def test_target_is_the_next_hour_not_the_current_one():
    df = build_features(synthetic_hourly(), min_hours=12)
    group = df[df["station_key"] == "station_0"].sort_values("snapshot_hour")
    shifted = group["avg_bikes_available"].shift(-1).dropna()
    assert np.allclose(group[TARGET].loc[shifted.index], shifted)


def test_no_feature_equals_the_target():
    """A feature identical to the target is the classic leakage signature."""
    df = build_features(synthetic_hourly(), min_hours=12)
    from train_model import FEATURES

    for feature in FEATURES:
        assert not np.allclose(df[feature], df[TARGET]), feature


def test_split_is_temporal_with_no_overlap():
    df = build_features(synthetic_hourly(), min_hours=12)
    train, test, cutoff = temporal_split(df, test_fraction=0.2)
    assert len(train) and len(test)
    # Every training timestamp precedes every test timestamp. This is the
    # property a random split destroys.
    assert train["snapshot_hour"].max() <= test["snapshot_hour"].min()
    assert train["snapshot_hour"].max() <= cutoff


def test_split_holds_out_roughly_the_requested_fraction():
    df = build_features(synthetic_hourly(), min_hours=12)
    _, test, _ = temporal_split(df, test_fraction=0.25)
    assert 0.15 < len(test) / len(df) < 0.35


def test_stations_below_the_history_threshold_are_dropped():
    df = synthetic_hourly(n_stations=2, n_hours=72)
    thin = synthetic_hourly(n_stations=1, n_hours=4)
    thin["station_key"] = "station_thin"
    combined = pd.concat([df, thin], ignore_index=True)

    result = build_features(combined, min_hours=12)
    assert "station_thin" not in set(result["station_key"])


def test_build_features_refuses_rather_than_returning_junk_on_thin_data():
    with pytest.raises(SystemExit):
        build_features(synthetic_hourly(n_stations=1, n_hours=3), min_hours=12)


def test_evaluate_matches_hand_computed_error():
    metrics = evaluate([1.0, 2.0, 3.0], [1.0, 2.0, 5.0])
    assert metrics["mae"] == pytest.approx(2 / 3)
    assert metrics["rmse"] == pytest.approx(np.sqrt(4 / 3))
