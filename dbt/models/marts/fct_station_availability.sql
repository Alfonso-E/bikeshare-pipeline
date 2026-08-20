{{ config(materialized='table') }}

-- Grain: one row per station per feed tick.
--
-- The net_bike_change column is the point of this table. GBFS never publishes
-- trips, only inventory levels -- so differencing consecutive snapshots is the
-- only way to recover flow. It is an approximation: within a snapshot interval
-- a departure and an arrival cancel out, so this undercounts true turnover.
-- That limitation is real and should be stated wherever the number is used.

with availability as (

    select * from {{ ref('int_station_availability') }}

), with_lag as (

    select
        *,
        lag(bikes_available) over w                 as prev_bikes_available,
        lag(snapshot_ts) over w                     as prev_snapshot_ts
    from availability
    window w as (partition by system_key, station_id order by snapshot_ts)

)

select
    {{ surrogate_key(['system_key', 'station_id']) }} as station_key,
    system_key,
    station_id,
    station_name,
    snapshot_ts,
    snapshot_ts_local,
    system_timezone,

    -- Calendar parts come from local time; the UTC hour is kept alongside as
    -- the canonical key for anything that has to line up across systems.
    date_trunc('hour', snapshot_ts_local)           as snapshot_hour_local,
    date_trunc('hour', snapshot_ts)                 as snapshot_hour_utc,
    extract(dow from snapshot_ts_local)             as day_of_week,
    extract(hour from snapshot_ts_local)            as hour_of_day,
    extract(dow from snapshot_ts_local) in (0, 6)   as is_weekend,

    bikes_available,
    ebikes_available,
    classic_bikes_available,
    docks_available,
    usable_docks,
    capacity,
    occupancy_rate,

    is_empty,
    is_full,
    is_operational,

    bikes_available - prev_bikes_available          as net_bike_change,
    date_diff('second', prev_snapshot_ts, snapshot_ts) as seconds_since_prev_snapshot

from with_lag
