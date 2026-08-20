-- Singular test: bikes + docks should never exceed nameplate capacity by more
-- than a small margin.
--
-- A handful of stations legitimately break this -- operators overfill during
-- rebalancing, and valet stations have no fixed dock count -- so this asserts
-- that it stays rare rather than that it never happens. A test that fails on
-- normal operations gets muted, and a muted test protects nothing.

with breaches as (

    select count(*) as breach_count
    from {{ ref('fct_station_availability') }}
    where capacity is not null
      and capacity > 0
      and usable_docks > capacity * 1.5

), total as (

    select count(*) as total_count from {{ ref('fct_station_availability') }}

)

select
    b.breach_count,
    t.total_count,
    b.breach_count::double / nullif(t.total_count, 0) as breach_rate
from breaches b
cross join total t
where b.breach_count::double / nullif(t.total_count, 0) > 0.01
