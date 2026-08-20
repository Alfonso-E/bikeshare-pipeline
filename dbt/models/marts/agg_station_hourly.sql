{{ config(materialized='table') }}

-- Grain: one row per station per hour. This is the modelling table -- the
-- forecaster trains on it and the dashboard reads from it.
--
-- snapshot_count is carried through deliberately: an hour assembled from three
-- snapshots is far less trustworthy than one assembled from sixty, and any
-- honest model needs to be able to drop the thin ones.

with facts as (

    select * from {{ ref('fct_station_availability') }}

)

select
    station_key,
    system_key,
    station_id,
    station_name,
    snapshot_hour_local                                      as snapshot_hour,

    min(snapshot_hour_utc)                                  as snapshot_hour_utc,
    max(system_timezone)                                    as system_timezone,
    max(day_of_week)                                        as day_of_week,
    max(hour_of_day)                                        as hour_of_day,
    max(is_weekend)                                         as is_weekend,
    max(capacity)                                           as capacity,

    count(*)                                                as snapshot_count,
    avg(bikes_available)                                    as avg_bikes_available,
    min(bikes_available)                                    as min_bikes_available,
    max(bikes_available)                                    as max_bikes_available,
    avg(ebikes_available)                                   as avg_ebikes_available,
    avg(docks_available)                                    as avg_docks_available,
    avg(occupancy_rate)                                     as avg_occupancy_rate,

    -- Share of the hour spent in a state that fails a rider.
    avg(case when is_empty then 1.0 else 0.0 end)           as pct_time_empty,
    avg(case when is_full  then 1.0 else 0.0 end)           as pct_time_full,
    avg(case when is_operational then 1.0 else 0.0 end)     as pct_time_operational,

    -- Split the differenced flow into departures and arrivals.
    sum(case when net_bike_change < 0 then -net_bike_change else 0 end) as bikes_removed,
    sum(case when net_bike_change > 0 then  net_bike_change else 0 end) as bikes_added,
    sum(coalesce(net_bike_change, 0))                       as net_bike_change

from facts
group by 1, 2, 3, 4, 5
