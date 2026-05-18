/*
  int_account_metrics.sql
  Migrated from: Programs/Banking/load_customer_accounts.sas (Step 2)

  SAS Original:
    DATA step computing ACCT_AGE_MONTHS, DAYS_INACTIVE, UTILIZATION_PCT,
    DORMANCY_FLAG, HIGH_BALANCE_FLAG + exception routing

  dbt Equivalent:
    SQL CASE expressions replace SAS DATA step IF/THEN logic
    SAS RETAIN/derived variables become SQL computed columns
    SAS FORMAT statements become CASE expressions (see macros/format_account_type.sql)
*/

with accounts as (
    select * from {{ ref('stg_cust_accounts') }}
),

enriched as (
    select
        *,

        -- SAS: ACCT_AGE_MONTHS = intck('month', OPEN_DATE, "&run_date"d)
        months_between(current_date(), open_date) as acct_age_months,

        -- SAS: DAYS_INACTIVE = "&run_date"d - LAST_ACTIVITY_DATE
        datediff(current_date(), last_activity_date) as days_inactive,

        -- SAS: UTILIZATION_PCT (revolving accounts only)
        case
            when account_type in ('CC', 'LOC', 'HELC') and credit_limit > 0
            then (current_balance / credit_limit) * 100
            else null
        end as utilization_pct,

        -- SAS: DORMANCY_FLAG
        case
            when datediff(current_date(), last_activity_date) > 365
                 and account_status = 'A'
            then 'Y' else 'N'
        end as dormancy_flag,

        -- SAS: HIGH_BALANCE_FLAG
        case
            when current_balance >= 250000 then 'Y' else 'N'
        end as high_balance_flag,

        -- SAS FORMAT replacement: {{ format_account_type('account_type') }}
        {{ format_account_type('account_type') }} as account_type_desc,
        {{ format_account_status('account_status') }} as account_status_desc,
        {{ format_customer_segment('customer_segment') }} as customer_segment_desc,

        current_date() as snapshot_date,
        current_timestamp() as load_timestamp

    from accounts
)

select * from enriched
