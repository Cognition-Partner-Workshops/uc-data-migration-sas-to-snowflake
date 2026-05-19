/*
  mart_daily_transactions.sql
  Migrated from: Programs/Banking/daily_transaction_processing.sas (Step 2 + 5)

  SAS Original:
    PROC SQL enrichment join + DATA step running balance with RETAIN

  dbt Equivalent:
    SQL JOIN replaces PROC SQL, window function replaces RETAIN + BY-group logic
    SAS PROC APPEND to CURATED.DAILY_TRANSACTIONS becomes incremental materialization
*/

{{
    config(
        materialized='incremental',
        unique_key='transaction_id',
        incremental_strategy='merge'
    )
}}

with transactions as (
    select * from {{ ref('stg_daily_transactions') }}
),

accounts as (
    select * from {{ ref('int_account_metrics') }}
),

-- SAS: PROC SQL creating WORK.TXN_ENRICHED (join transactions to accounts)
-- running_balance computed via window function (replaces RETAIN + BY-group)
base as (
    select
        t.transaction_id,
        t.account_id,
        t.transaction_date,
        t.transaction_type,
        t.transaction_amount,
        t.description,
        a.account_type,
        a.customer_id,
        a.customer_segment,
        a.region_code,
        a.branch_id,
        a.current_balance,
        a.risk_rating,

        -- SAS: RETAIN RUNNING_BALANCE — replaced by window function
        sum(
            case
                when t.transaction_type in ('DEP','INT','REF','REV')
                    then t.transaction_amount
                when t.transaction_type in ('WDR','PMT','FEE','CHG')
                    then -abs(t.transaction_amount)
                when t.transaction_type in ('TRF','ADJ')
                    then t.transaction_amount
                else 0
            end
        ) over (
            partition by t.account_id
            order by t.transaction_date, t.transaction_id
            rows unbounded preceding
        ) + a.current_balance as running_balance,

        {{ format_account_type('a.account_type') }} as account_type_desc,
        {{ format_txn_category('t.transaction_type') }} as transaction_type_desc

    from transactions t
    left join accounts a
        on t.account_id = a.account_id
),

-- Derive pre/post balances from running_balance so multi-txn accounts are correct
enriched as (
    select
        *,
        running_balance as post_txn_balance,
        lag(running_balance, 1, current_balance) over (
            partition by account_id
            order by transaction_date, transaction_id
        ) as pre_txn_balance
    from base
)

select * from enriched

{% if is_incremental() %}
where transaction_date >= (select max(transaction_date) from {{ this }})
{% endif %}
