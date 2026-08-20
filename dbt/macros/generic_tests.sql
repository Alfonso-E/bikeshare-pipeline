{#
    Two generic tests the project leans on. Written locally instead of
    importing dbt_utils / dbt_expectations so `dbt build` needs no packages.
#}

{% test value_between(model, column_name, min_value, max_value) %}
-- Fails on any row outside [min_value, max_value]. Nulls pass; use not_null
-- alongside this when the column is required.
select *
from {{ model }}
where {{ column_name }} is not null
  and ({{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }})
{% endtest %}


{% test unique_combination(model, columns) %}
-- Composite uniqueness, i.e. the grain of the table. Every fact and aggregate
-- in this project declares its grain with this test; if one starts failing,
-- the ingester has duplicated a snapshot or a join has fanned out.
select
    {{ columns | join(', ') }},
    count(*) as row_count
from {{ model }}
group by {{ range(1, columns | length + 1) | join(', ') }}
having count(*) > 1
{% endtest %}
