/*=====================================================================
  JOB03_CALC_AMB.sql — Monthly Average Balance Calculation

  SAS equivalent: inline batch step JOB03_CALC_AMB (see lineage/
  SAS_lineage.json) — DATA step deriving reporting_month_yyyymm from
  DAILY_BALANCE.date, PROC SQL mean() per customer/account/month, then
  PROC APPEND into MONTHLY_AMB.

  Inputs:  STAGING.CUST_ACCOUNTS, STAGING.DAILY_BALANCE
  Outputs: STAGING.MONTHLY_AMB  (append — one row per account per month)

  Conversion notes:
    - cats(put(year(date),4.), put(month(date),z2.)) → TO_CHAR(date,'YYYYMM')
    - PROC SQL mean()                                → AVG()
    - today() as date_computed                      → $run_date
    - PROC APPEND base=monthly_amb force            → INSERT INTO
    - The SAS batch processes the month that has just completed (the
      WORK.DAILY_BALANCE extract at run time holds the prior month).
      That scope is made explicit here with the $run_date variable so a
      replay of a historical run (e.g. run_date = 2025-08-01 → 202507)
      reproduces the SAS baseline exactly.
    - AMB is stored at 2 decimal places, as in the SAS baseline.

  *** IMPORTANT — SOURCE-PARITY NOTE ***
  The SAS logic computes AMB for ALL accounts regardless of is_active
  status. Do NOT add a WHERE c.is_active = 'ACTIVE' filter here — that
  would silently exclude inactive accounts and cause a Sum Amount
  divergence ($773K gap on Scenario1). If the business wants to exclude
  inactive accounts, that is a separate remediation decision, not a
  migration side effect.
=====================================================================*/

-- run_date: supplied by the caller/orchestrator; defaults to today.
SET run_date = (SELECT COALESCE(TRY_TO_DATE(GETVARIABLE('RUN_DATE')), CURRENT_DATE()));

CREATE TABLE IF NOT EXISTS STAGING.MONTHLY_AMB (
    CUSTOMER_ID              NUMBER,
    ACCOUNT_ID               VARCHAR,
    REPORTING_MONTH_YYYYMM   VARCHAR(6),
    AVERAGE_MONTHLY_BALANCE  NUMBER(18, 2),
    DATE_COMPUTED            DATE
);

INSERT INTO STAGING.MONTHLY_AMB
    (CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM, AVERAGE_MONTHLY_BALANCE, DATE_COMPUTED)
WITH daily_balance_with_month AS (
    -- SAS Step 1: derive reporting month (YYYYMM) from the balance date
    SELECT
        d.CUSTOMER_ID,
        d.ACCOUNT_ID,
        d.END_OF_DAY_BALANCE,
        TO_CHAR(d.DATE, 'YYYYMM') AS REPORTING_MONTH_YYYYMM
    FROM STAGING.DAILY_BALANCE d
    WHERE DATE_TRUNC('month', d.DATE) = DATE_TRUNC('month', DATEADD('month', -1, $run_date))
)
-- SAS Step 2: AMB per customer/account/month
SELECT
    c.CUSTOMER_ID,
    c.ACCOUNT_ID,
    d.REPORTING_MONTH_YYYYMM,
    ROUND(AVG(d.END_OF_DAY_BALANCE), 2) AS AVERAGE_MONTHLY_BALANCE,
    $run_date                           AS DATE_COMPUTED
FROM STAGING.CUST_ACCOUNTS c
INNER JOIN daily_balance_with_month d
    ON c.CUSTOMER_ID = d.CUSTOMER_ID
   AND c.ACCOUNT_ID  = d.ACCOUNT_ID
-- NOTE: No is_active filter — matches SAS source faithfully.
GROUP BY
    c.CUSTOMER_ID,
    c.ACCOUNT_ID,
    d.REPORTING_MONTH_YYYYMM
ORDER BY
    c.CUSTOMER_ID,
    c.ACCOUNT_ID,
    d.REPORTING_MONTH_YYYYMM;
