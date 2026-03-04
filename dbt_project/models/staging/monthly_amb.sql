-- =============================================================================
-- dbt Model: monthly_amb
-- SAS Job Equivalent: JOB03_CALC_AMB
-- Lineage: FINANCE_DB.STAGING.STG_CUST_ACCOUNTS
--        + FINANCE_DB.STAGING.STG_DAILY_BALANCE
--       -> FINANCE_DB.STAGING.MONTHLY_AMB
--
-- Calculates the Average Monthly Balance (AMB) for each active customer
-- account by joining staged account metadata with daily balance records,
-- then aggregating daily end-of-day balances per account per month.
--
-- Business rules (from SAS JOB03_CALC_AMB / SF_lineage.json):
--   - Only ACTIVE accounts are included (WHERE is_active = 'ACTIVE')
--   - AMB = AVG(end_of_day_balance) grouped by customer, account, and month
--   - reporting_month_yyyymm is stored as an integer in YYYYMM format
--   - date_computed is the first day of the month following the reporting month
-- =============================================================================

{{
    config(
        materialized='table',
        schema='STAGING',
        alias='MONTHLY_AMB'
    )
}}

WITH active_accounts AS (

    SELECT
        CUSTOMER_ID,
        ACCOUNT_ID
    FROM {{ ref('stg_cust_accounts') }}
    WHERE IS_ACTIVE = 'ACTIVE'

),

daily_balances AS (

    SELECT
        CUSTOMER_ID,
        ACCOUNT_ID,
        DATE,
        END_OF_DAY_BALANCE,
        MONTH
    FROM {{ ref('stg_daily_balance') }}

),

/*
    Join daily balances to active accounts only.
    This mirrors the SAS PROC SQL / DATA step join in JOB03_CALC_AMB
    where WORK.CUST_ACCOUNTS is inner-joined with WORK.DAILY_BALANCE
    on both customer_id and account_id.
*/
filtered_balances AS (

    SELECT
        db.CUSTOMER_ID,
        db.ACCOUNT_ID,
        db.DATE,
        db.END_OF_DAY_BALANCE,
        db.MONTH
    FROM daily_balances AS db
    INNER JOIN active_accounts AS ca
        ON  db.CUSTOMER_ID = ca.CUSTOMER_ID
        AND db.ACCOUNT_ID  = ca.ACCOUNT_ID

),

/*
    Aggregate: compute Average Monthly Balance per customer-account-month.
    Convert the YYYY-MM string month to YYYYMM integer for the output column.
    date_computed is set to the first day of the next month (the date the
    calculation would have been run in the SAS batch cycle).
*/
monthly_aggregation AS (

    SELECT
        CUSTOMER_ID,
        ACCOUNT_ID,
        (YEAR(MIN(DATE)) * 100 + MONTH(MIN(DATE)))::INTEGER  AS REPORTING_MONTH_YYYYMM,
        ROUND(AVG(END_OF_DAY_BALANCE), 2)                     AS AVERAGE_MONTHLY_BALANCE,
        DATEADD(
            MONTH,
            1,
            DATE_TRUNC('MONTH', MIN(DATE))
        )::DATE                                                AS DATE_COMPUTED,
        MONTH                                                  AS MONTH_KEY
    FROM filtered_balances
    GROUP BY CUSTOMER_ID, ACCOUNT_ID, MONTH

)

SELECT
    CUSTOMER_ID,
    ACCOUNT_ID,
    REPORTING_MONTH_YYYYMM,
    AVERAGE_MONTHLY_BALANCE,
    DATE_COMPUTED
FROM monthly_aggregation
