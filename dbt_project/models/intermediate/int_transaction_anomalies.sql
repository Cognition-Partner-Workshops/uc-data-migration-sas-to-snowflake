/*
  int_transaction_anomalies.sql
  Migrated from: Programs/Banking/daily_transaction_processing.sas (Step 3)

  SAS Original:
    PROC SQL computing 90-day statistics per account, then Z-score
    anomaly detection with CASE-based classification

  dbt Equivalent:
    Window functions replace the two-pass PROC SQL approach
    SAS PROC MEANS-style stats become SQL aggregations
*/

with transactions as (
    select * from {{ ref('mart_daily_transactions') }}
),

-- SAS: PROC SQL creating WORK.TXN_STATS (90-day rolling stats per account)
account_stats as (
    select
        account_id,
        avg(abs(transaction_amount)) as avg_txn_amt,
        stddev(abs(transaction_amount)) as std_txn_amt,
        count(*) as txn_count
    from {{ ref('mart_daily_transactions') }}
    where transaction_date >= date_add(current_date(), -90)
    group by account_id
),

-- SAS: PROC SQL creating WORK.TXN_ANOMALIES with Z-score and classification
anomalies as (
    select
        t.*,
        s.avg_txn_amt,
        s.std_txn_amt,
        case
            when s.std_txn_amt > 0
            then (abs(t.transaction_amount) - s.avg_txn_amt) / s.std_txn_amt
            else null
        end as z_score,
        case
            when s.std_txn_amt > 0
                 and (abs(t.transaction_amount) - s.avg_txn_amt) / s.std_txn_amt > 3
                then 'HIGH_AMOUNT'
            when t.post_txn_balance < 0
                then 'OVERDRAFT'
            when t.transaction_type = 'WDR'
                 and abs(t.transaction_amount) > t.pre_txn_balance * 0.9
                then 'LARGE_WITHDRAWAL'
            when t.customer_id is null
                then 'ORPHAN_ACCOUNT'
            else null
        end as anomaly_type
    from transactions t
    left join account_stats s
        on t.account_id = s.account_id
)

select * from anomalies
where anomaly_type is not null
