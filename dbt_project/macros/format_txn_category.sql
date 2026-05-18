/*
  Migrated from: Formats/banking_formats.sas — value $TXNCAT
*/

{% macro format_txn_category(column) %}
case {{ column }}
    when 'DEP' then 'Deposit'
    when 'WDR' then 'Withdrawal'
    when 'TRF' then 'Transfer'
    when 'PMT' then 'Payment'
    when 'FEE' then 'Fee'
    when 'INT' then 'Interest'
    when 'ADJ' then 'Adjustment'
    when 'REV' then 'Reversal'
    when 'CHG' then 'Charge'
    when 'REF' then 'Refund'
    else 'Other'
end
{% endmacro %}
