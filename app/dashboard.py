"""Streamlit dashboard over the marts.

    streamlit run app/dashboard.py

Reads the DuckDB warehouse read-only, so it is safe to leave open while the
ingester and dbt are running.
"""
from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gbfs.config import WAREHOUSE_PATH  # noqa: E402
from gbfs.warehouse import connect  # noqa: E402

st.set_page_config(page_title="Bike-share availability", layout="wide")


@st.cache_data(ttl=300)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    con = connect(read_only=True)
    try:
        return con.execute(sql, list(params)).df()
    finally:
        con.close()


def main() -> None:
    st.title("Bike-share station availability")

    if not WAREHOUSE_PATH.exists():
        st.error(
            "No warehouse found. Run `python scripts/run_ingest.py` and then "
            "`dbt build` from the dbt/ directory first."
        )
        return

    try:
        systems = query("SELECT DISTINCT system_key FROM main_marts.dim_station ORDER BY 1")
    except Exception as exc:  # noqa: BLE001
        st.error("Could not read the marts -- has `dbt build` run? ({})".format(exc))
        return

    if systems.empty:
        st.warning("The warehouse is empty. Take a snapshot first.")
        return

    system = st.sidebar.selectbox("System", systems["system_key"])

    coverage = query(
        """
        SELECT
            count(DISTINCT station_key)  AS stations,
            count(*)                     AS station_hours,
            min(snapshot_hour)           AS first_hour,
            max(snapshot_hour)           AS last_hour
        FROM main_marts.agg_station_hourly
        WHERE system_key = ?
        """,
        (system,),
    ).iloc[0]

    c1, c2, c3 = st.columns(3)
    c1.metric("Stations", "{:,}".format(int(coverage["stations"])))
    c2.metric("Station-hours", "{:,}".format(int(coverage["station_hours"])))
    c3.metric(
        "History",
        "{} h".format(
            int(
                (pd.Timestamp(coverage["last_hour"]) - pd.Timestamp(coverage["first_hour"]))
                .total_seconds()
                // 3600
                + 1
            )
        ),
    )

    hours_of_history = coverage["station_hours"] / max(int(coverage["stations"]), 1)
    if hours_of_history < 24:
        st.info(
            "Only {:.0f} hours of history so far. Daily patterns need a few days "
            "of collection before the charts below mean much.".format(hours_of_history)
        )

    st.subheader("Stations most often empty")
    st.caption(
        "Share of observed time with zero bikes available. These are the "
        "stations a rebalancing team would prioritise."
    )
    worst = query(
        """
        SELECT
            station_name,
            avg(pct_time_empty)   AS pct_empty,
            avg(pct_time_full)    AS pct_full,
            avg(avg_bikes_available) AS avg_bikes,
            max(capacity)         AS capacity,
            sum(snapshot_count)   AS observations
        FROM main_marts.agg_station_hourly
        WHERE system_key = ?
        GROUP BY station_name
        HAVING sum(snapshot_count) >= 3
        ORDER BY pct_empty DESC
        LIMIT 20
        """,
        (system,),
    )
    st.dataframe(worst, use_container_width=True, hide_index=True)

    st.subheader("Availability by hour of day")
    profile = query(
        """
        SELECT
            hour_of_day,
            is_weekend,
            avg(avg_occupancy_rate) AS occupancy
        FROM main_marts.agg_station_hourly
        WHERE system_key = ?
        GROUP BY 1, 2
        ORDER BY 1
        """,
        (system,),
    )
    if len(profile) > 1:
        profile["day_type"] = profile["is_weekend"].map({True: "Weekend", False: "Weekday"})
        chart = (
            alt.Chart(profile)
            .mark_line(point=True)
            .encode(
                x=alt.X("hour_of_day:Q", title="Hour of day"),
                y=alt.Y("occupancy:Q", title="Mean occupancy rate"),
                color=alt.Color("day_type:N", title=""),
            )
            .properties(height=320)
        )
        st.altair_chart(chart, use_container_width=True)
    else:
        st.caption("Needs more than one hour of data.")

    st.subheader("Station map")
    stations = query(
        """
        SELECT station_name, latitude AS lat, longitude AS lon, capacity
        FROM main_marts.dim_station
        WHERE system_key = ? AND latitude IS NOT NULL
        """,
        (system,),
    )
    st.map(stations, size=20)


if __name__ == "__main__":
    main()
