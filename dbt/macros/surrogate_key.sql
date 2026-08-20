{#
    Build a stable hash key from a list of columns.

    Defined locally rather than pulled in via dbt_utils so the project has no
    package dependencies -- `dbt build` works on a clean clone with no network.
    Nulls are coalesced to a sentinel so that (null, 'a') and ('a', null) do not
    collide, which is the bug this macro exists to avoid.
#}
{% macro surrogate_key(columns) -%}
    md5(
        {%- for column in columns %}
        coalesce(cast({{ column }} as varchar), '_dbt_null_')
        {%- if not loop.last %} || '||' || {% endif -%}
        {%- endfor %}
    )
{%- endmacro %}
