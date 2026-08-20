{{ config(materialized='view') }}

-- Status joined to metadata, with the derived measures every downstream mart
-- needs. Kept as one intermediate view so the occupancy definition lives in
-- exactly one place.

with status as (

    select * from {{ ref('stg_station_status') }}

), stations as (

    select * from {{ ref('stg_station_information') }}

), timezones as (

    select * from {{ ref('system_timezones') }}

)

select
    st.system_key,
    st.station_id,
    sn.station_name,
    sn.latitude,
    sn.longitude,
    sn.region_id,
    sn.capacity,

    st.snapshot_ts,

    -- Snapshots are stored in UTC. Rider-facing time-of-day analysis has to
    -- happen in the system's own timezone, so convert once, here, and let
    -- everything downstream read hour-of-day off this column. Extracting the
    -- hour straight from a TIMESTAMPTZ instead would return whatever timezone
    -- the machine running dbt is set to -- a bug that only shows up when
    -- someone else runs the project.
    st.snapshot_ts at time zone tz.timezone           as snapshot_ts_local,
    tz.timezone                                       as system_timezone,

    st.feed_updated_at,
    st.station_reported_at,

    st.bikes_available,
    st.ebikes_available,
    st.classic_bikes_available,
    st.bikes_disabled,
    st.docks_available,
    st.docks_disabled,

    -- Usable docks, not nameplate capacity. A station with half its docks
    -- broken is effectively smaller, and occupancy should reflect that.
    st.bikes_available + st.docks_available          as usable_docks,

    case
        when st.bikes_available + st.docks_available > 0
        then st.bikes_available::double
             / (st.bikes_available + st.docks_available)
    end                                              as occupancy_rate,

    -- The two states that actually hurt a rider: nothing to take, nowhere to
    -- put it back. These are the targets worth predicting.
    st.bikes_available = 0                           as is_empty,
    st.docks_available = 0                           as is_full,

    st.is_installed,
    st.is_renting,
    st.is_returning,

    -- A station can be installed but not renting (maintenance, event closure).
    -- Excluding these keeps demand models from learning outage patterns.
    st.is_installed and st.is_renting and st.is_returning as is_operational

from status st
left join timezones tz
       on st.system_key = tz.system_key
left join stations sn
       on st.system_key = sn.system_key
      and st.station_id = sn.station_id
