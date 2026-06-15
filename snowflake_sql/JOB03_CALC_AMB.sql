/*=====================================================================
  JOB03_CALC_AMB.sql — Monthly Average Balance Calculation

  SAS equivalent: Inline step in the batch that computes MONTHLY_AMB
  from WORK.CUST_ACCOUNTS and WORK.DAILY_BALANCE.

  Inputs:  STAGING.CUST_ACCOUNTS_DAILY, STAGING.DAILY_BALANCE
  Outputs: STAGING.MONTHLY_AMB

  *** IMPORTANT — SOURCE-PARITY NOTE ***
  The SAS logic computes AMB for ALL accounts regardless of is_active
  status. Do NOT add a WHERE c.is_active = 'ACTIVE' filter here — that
  would silently exclude inactive accounts and cause a Sum Amount
  divergence ($773K gap on Scenario1). If the business wants to exclude
  inactive accounts, that is a separate remediation decision, not a
  migration side effect.
=====================================================================*/

CREATE OR REPLACE TABLE STAGING.MONTHLY_AMB AS
SELECT
    c.CUSTOMER_ID,
    c.ACCOUNT_ID,
    d.REPORTING_MONTH_YYYYMM,
    AVG(d.END_OF_DAY_BALANCE)  AS AVERAGE_MONTHLY_BALANCE,
    CURRENT_DATE()             AS DATE_COMPUTED
FROM STAGING.CUST_ACCOUNTS_DAILY c
INNER JOIN STAGING.DAILY_BALANCE d
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
