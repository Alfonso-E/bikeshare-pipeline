{{ config(materialized='view') }}

-- Cleaned availability snapshots, one row per (system, station, feed tick).
--
-- Two things are handled here rather than at ingest time, on purpose: the raw
-- layer stays a faithful record of the API, and this logic stays reviewable.
--   1. Deduplication. If the scheduler double-fires inside a TTL window we can
--      end up with the same feed tick written twice under different filenames.
--   2. The bogus last_reported values Citi Bike emits for dead docks (86400 and
--      similar), which would otherwise turn into 1970 timestamps downstream.

with source as (

    select * from {{ source('raw', 'station_status') }}

), deduplicated as (

    select
        *,
        row_number() over (
            partition by system_key, station_id, feed_last_updated
            order by snapshot_ts
        ) as _row_num
    from source
    where station_id is not null

)

select
    system_key,
    station_id,

    snapshot_ts,
    to_timestamp(feed_last_updated)                         as feed_updated_at,

    -- Only trust last_reported if it actually looks like a unix timestamp.
    case
        when last_reported >= 1000000000 then to_timestamp(last_reported)
    end                                                     as station_reported_at,

    coalesce(num_bikes_available, 0)                        as bikes_available,
    coalesce(num_ebikes_available, 0)                       as ebikes_available,
    coalesce(num_bikes_available, 0)
        - coalesce(num_ebikes_available, 0)                 as classic_bikes_available,
    coalesce(num_bikes_disabled, 0)                         as bikes_disabled,
    coalesce(num_docks_available, 0)                        as docks_available,
    coalesce(num_docks_disabled, 0)                         as docks_disabled,

    coalesce(is_installed, false)                           as is_installed,
    coalesce(is_renting, false)                             as is_renting,
    coalesce(is_returning, false)                           as is_returning

from deduplicated
where _row_num = 1
