/*
  format_account_type.sql
  Migrated from: Formats/banking_formats.sas — value $ACCTTYPE

  SAS PROC FORMAT creates a format catalog entry.
  dbt equivalent: a Jinja macro returning a CASE expression.
  Usage: {{ format_account_type('account_type') }}
*/

{% macro format_account_type(column) %}
case {{ column }}
    when 'CHK'  then 'Checking'
    when 'SAV'  then 'Savings'
    when 'MMA'  then 'Money Market'
    when 'CD'   then 'Certificate of Deposit'
    when 'IRA'  then 'Individual Retirement'
    when 'LOC'  then 'Line of Credit'
    when 'MTG'  then 'Mortgage'
    when 'AUTO' then 'Auto Loan'
    when 'PERS' then 'Personal Loan'
    when 'CC'   then 'Credit Card'
    when 'HELC' then 'Home Equity LOC'
    else 'Unknown'
end
{% endmacro %}
