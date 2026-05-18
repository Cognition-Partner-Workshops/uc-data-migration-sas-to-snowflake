/*
  Migrated from: Formats/banking_formats.sas — value $CUSTSEG
*/

{% macro format_customer_segment(column) %}
case {{ column }}
    when 'RET'  then 'Retail'
    when 'PREM' then 'Premium'
    when 'PB'   then 'Private Banking'
    when 'SMB'  then 'Small Business'
    when 'COMM' then 'Commercial'
    when 'CORP' then 'Corporate'
    else 'Unclassified'
end
{% endmacro %}
