/*
  Migrated from: Formats/banking_formats.sas — value $ACCTSTAT
*/

{% macro format_account_status(column) %}
case {{ column }}
    when 'A' then 'Active'
    when 'C' then 'Closed'
    when 'D' then 'Dormant'
    when 'F' then 'Frozen'
    when 'R' then 'Restricted'
    when 'S' then 'Suspended'
    when 'P' then 'Pending'
    when 'W' then 'Written Off'
    else 'Unknown'
end
{% endmacro %}
