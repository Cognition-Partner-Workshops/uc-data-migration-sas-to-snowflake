/*
  stg_daily_transactions.sql
  Migrated from: Programs/Banking/daily_transaction_processing.sas (Step 1-2)

  SAS Original:
    DATA step validation + PROC SQL enrichment join

  dbt Equivalent:
    Staging model with validation logic expressed as SQL CASE/WHERE
    replaces the DATA step validation and rejected-record routing
*/

with source as (
    select * from {{ source('banking_raw', 'daily_transactions') }}
),

validated as (
    select
        *,
        case
            when transaction_id is null then 'Missing TRANSACTION_ID'
            when account_id is null then 'Missing ACCOUNT_ID'
            when transaction_amount is null then 'Missing TRANSACTION_AMOUNT'
            when abs(transaction_amount) > 10000000 then 'Amount exceeds threshold'
            when transaction_type not in ('DEP','WDR','TRF','PMT','FEE','INT','ADJ','REV','CHG','REF')
                then 'Invalid transaction type'
            when transaction_date > current_date() then 'Future dated'
            else null
        end as rejection_reason
    from source
),

-- Equivalent of the SAS "output WORK.TXN_VALIDATED" path
accepted as (
    select * from validated
    where rejection_reason is null
)

select * from accepted
