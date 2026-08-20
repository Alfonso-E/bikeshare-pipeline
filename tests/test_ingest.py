"""Unit tests for the ingest normalisers.

These are pure-function tests -- no network, no filesystem. Every case here is
a real inconsistency observed in live GBFS feeds, not a hypothetical.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gbfs.config import System  # noqa: E402
from gbfs.discover import _extract_feed_list  # noqa: E402
from gbfs.ingest import (  # noqa: E402
    _as_bool,
    _as_int,
    _count_ebikes,
    normalise_information,
    normalise_status,
)

SYSTEM = System(key="test", name="Test", timezone="UTC", discovery_url="http://x")
TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "value,expected",
    [(1, True), (0, False), (True, True), ("true", True), ("0", False), (None, None)],
)
def test_as_bool_handles_every_shape_gbfs_uses(value, expected):
    assert _as_bool(value) is expected


def test_as_int_rejects_junk_without_raising():
    assert _as_int("12") == 12
    assert _as_int("not a number") is None
    assert _as_int(None) is None
    # bool is an int subclass in Python; treating True as 1 here would silently
    # turn a status flag into a bike count.
    assert _as_int(True) is None


def test_count_ebikes_prefers_the_v2_field():
    assert _count_ebikes({"num_ebikes_available": 4}) == 4


def test_count_ebikes_sums_the_v3_vehicle_types_list():
    station = {
        "vehicle_types_available": [
            {"vehicle_type_id": "classic_bike", "count": 7},
            {"vehicle_type_id": "electric_bike", "count": 3},
        ]
    }
    assert _count_ebikes(station) == 3


def test_count_ebikes_returns_none_when_the_feed_says_nothing():
    assert _count_ebikes({"num_bikes_available": 5}) is None


def test_normalise_status_stringifies_numeric_station_ids():
    """Some systems emit ints, some strings. Joins break if we keep both."""
    payload = {"last_updated": 1700000000, "data": {"stations": [{"station_id": 42}]}}
    rows = normalise_status(payload, SYSTEM, TS)
    assert rows[0]["station_id"] == "42"


def test_normalise_status_keeps_missing_fields_as_null_not_zero():
    """A field the feed never sent is unknown, not empty. Zero would be a lie."""
    payload = {"last_updated": 1700000000, "data": {"stations": [{"station_id": "a"}]}}
    rows = normalise_status(payload, SYSTEM, TS)
    assert rows[0]["num_bikes_available"] is None
    assert rows[0]["num_ebikes_available"] is None


def test_normalise_status_on_empty_feed():
    rows = normalise_status({"last_updated": 1, "data": {"stations": []}}, SYSTEM, TS)
    assert rows == []


def test_normalise_information_trims_to_the_expected_columns():
    payload = {
        "data": {
            "stations": [
                {
                    "station_id": "a",
                    "name": "Main St",
                    "lat": 1.0,
                    "lon": 2.0,
                    "capacity": 20,
                    "eightd_has_key_dispenser": False,
                }
            ]
        }
    }
    rows = normalise_information(payload, SYSTEM, TS)
    assert rows[0]["name"] == "Main St"
    # Operator-specific extras are dropped here so the Parquet schema stays
    # stable when a system adds a field mid-week.
    assert "eightd_has_key_dispenser" not in rows[0]


def test_extract_feed_list_handles_v2_language_nesting():
    payload = {"data": {"en": {"feeds": [{"name": "station_status", "url": "u"}]}}}
    assert _extract_feed_list(payload)[0]["name"] == "station_status"


def test_extract_feed_list_handles_v3_flat_shape():
    payload = {"data": {"feeds": [{"name": "station_status", "url": "u"}]}}
    assert _extract_feed_list(payload)[0]["name"] == "station_status"


def test_extract_feed_list_falls_back_to_another_language():
    payload = {"data": {"fr": {"feeds": [{"name": "station_status", "url": "u"}]}}}
    assert _extract_feed_list(payload)[0]["name"] == "station_status"


def test_extract_feed_list_raises_on_a_shape_we_do_not_understand():
    with pytest.raises(ValueError):
        _extract_feed_list({"data": {}})
