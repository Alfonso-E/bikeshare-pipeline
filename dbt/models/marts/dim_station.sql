{{ config(materialized='table') }}

-- One row per station. The conformed dimension every mart joins to, and the
-- table a BI tool should build its station-level filters from.

with stations as (

    select * from {{ ref('stg_station_information') }}

), observed as (

    select
        system_key,
        station_id,
        min(snapshot_ts)                        as first_seen_at,
        max(snapshot_ts)                        as last_seen_at,
        count(*)                                as snapshot_count,
        avg(case when is_operational then 1.0 else 0.0 end) as operational_rate
    from {{ ref('int_station_availability') }}
    group by 1, 2

)

select
    {{ surrogate_key(['s.system_key', 's.station_id']) }} as station_key,
    s.system_key,
    s.station_id,
    s.station_name,
    s.latitude,
    s.longitude,
    s.region_id,
    s.capacity,
    s.station_type,

    case
        when s.capacity is null   then 'unknown'
        when s.capacity < 15      then 'small'
        when s.capacity < 35      then 'medium'
        else 'large'
    end                                         as capacity_band,

    o.first_seen_at,
    o.last_seen_at,
    o.snapshot_count,
    o.operational_rate

from stations s
left join observed o
       on s.system_key = o.system_key
      and s.station_id = o.station_id
