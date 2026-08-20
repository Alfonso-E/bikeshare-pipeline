{{ config(materialized='view') }}

-- Station metadata, deduplicated to the most recent snapshot per station.
-- Stations get renamed and re-sited over time; we keep the latest description
-- and let the fact table carry the history.

with source as (

    select * from {{ source('raw', 'station_information') }}
    where station_id is not null

), ranked as (

    select
        *,
        row_number() over (
            partition by system_key, station_id
            order by snapshot_ts desc
        ) as _row_num
    from source

)

select
    system_key,
    station_id,
    trim(name)                          as station_name,
    lat                                 as latitude,
    lon                                 as longitude,
    capacity,
    region_id,
    coalesce(station_type, 'unknown')   as station_type,
    snapshot_ts                         as metadata_as_of
from ranked
where _row_num = 1
