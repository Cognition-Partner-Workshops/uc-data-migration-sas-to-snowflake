/*=====================================================================
  JOB01_LOAD_CUST_ACCOUNTS.sql — Snowflake conversion of
  load_customer_accounts.sas

  Source: ts-sas-legacy-analytics/Programs/Banking/load_customer_accounts.sas
  SAS schedule: Daily 06:00 via Control-M BANK_DAILY_01
  Snowflake: Runs as a Snowflake Task (see orchestration/tasks.sql)

  Inputs:  RAW.CUST_ACCOUNTS, RAW.CUST_DEMOGRAPHICS, RAW.DAILY_RATES
  Outputs: STAGING.CUST_ACCOUNTS_DAILY, STAGING.ACCT_EXCEPTIONS

  Conversion notes:
    - SAS PROC SQL extract + DATA step merged into a single
      CREATE OR REPLACE TABLE ... AS SELECT with CASE-derived columns.
    - SAS intck('month', ...) → DATEDIFF('month', ...)
    - SAS format $ACCTTYPE. → no Snowflake equivalent (formatting is
      presentation-layer); column stored as raw code.
    - SAS %parmv region filter → session variable $run_date.
=====================================================================*/

-- Session variables (set by the orchestrator task or caller)
-- SET run_date = CURRENT_DATE();

CREATE OR REPLACE TABLE STAGING.CUST_ACCOUNTS_DAILY AS
SELECT
    a.ACCOUNT_ID,
    a.CUSTOMER_ID,
    a.ACCOUNT_TYPE,
    a.ACCOUNT_STATUS,
    a.OPEN_DATE,
    a.CLOSE_DATE,
    a.CURRENT_BALANCE,
    a.AVAILABLE_BALANCE,
    a.CREDIT_LIMIT,
    a.INTEREST_RATE,
    a.BRANCH_ID,
    a.OFFICER_ID,
    a.LAST_ACTIVITY_DATE,
    d.FIRST_NAME,
    d.LAST_NAME,
    d.SSN_HASH,
    d.DATE_OF_BIRTH,
    d.CUSTOMER_SEGMENT,
    d.RISK_RATING,
    d.REGION_CODE,
    d.PRIMARY_EMAIL,
    d.PHONE_NUMBER,

    -- Derived: Account age in months
    DATEDIFF('month', a.OPEN_DATE, $run_date) AS ACCT_AGE_MONTHS,

    -- Derived: Days since last activity
    DATEDIFF('day', a.LAST_ACTIVITY_DATE, $run_date) AS DAYS_INACTIVE,

    -- Derived: Utilization ratio for revolving accounts
    CASE
        WHEN a.ACCOUNT_TYPE IN ('CC', 'LOC', 'HELC') AND a.CREDIT_LIMIT > 0
        THEN (a.CURRENT_BALANCE / a.CREDIT_LIMIT) * 100
        ELSE NULL
    END AS UTILIZATION_PCT,

    -- Derived: Dormancy flag
    CASE
        WHEN DATEDIFF('day', a.LAST_ACTIVITY_DATE, $run_date) > 365
             AND a.ACCOUNT_STATUS = 'A'
        THEN 'Y' ELSE 'N'
    END AS DORMANCY_FLAG,

    -- Derived: High-balance flag
    CASE
        WHEN a.CURRENT_BALANCE >= 250000 THEN 'Y' ELSE 'N'
    END AS HIGH_BALANCE_FLAG,

    -- Snapshot metadata
    $run_date AS SNAPSHOT_DATE,
    CURRENT_TIMESTAMP() AS LOAD_TIMESTAMP

FROM RAW.CUST_ACCOUNTS a
INNER JOIN RAW.CUST_DEMOGRAPHICS d
    ON a.CUSTOMER_ID = d.CUSTOMER_ID
WHERE a.ACCOUNT_STATUS NOT IN ('W', 'C')
  AND a.OPEN_DATE <= $run_date
ORDER BY a.CUSTOMER_ID, a.ACCOUNT_ID;

-- Exception detection (separate table for DQ monitoring)
CREATE OR REPLACE TABLE STAGING.ACCT_EXCEPTIONS AS
SELECT
    ACCOUNT_ID,
    CUSTOMER_ID,
    ACCOUNT_TYPE,
    CURRENT_BALANCE,
    UTILIZATION_PCT,
    RISK_RATING,
    SNAPSHOT_DATE,
    CASE
        WHEN ACCOUNT_TYPE IN ('CHK','SAV','MMA','CD') AND CURRENT_BALANCE < 0
            THEN 'NEG_BAL'
        WHEN UTILIZATION_PCT > 95
            THEN 'HIGH_UTIL'
        WHEN RISK_RATING IS NULL
            THEN 'NO_RISK'
    END AS EXCEPTION_CODE,
    CASE
        WHEN ACCOUNT_TYPE IN ('CHK','SAV','MMA','CD') AND CURRENT_BALANCE < 0
            THEN 'Negative balance ' || TO_CHAR(CURRENT_BALANCE, '$999,999,999.99')
                 || ' on deposit account ' || ACCOUNT_ID
        WHEN UTILIZATION_PCT > 95
            THEN 'Utilization at ' || ROUND(UTILIZATION_PCT, 1)
                 || '% for account ' || ACCOUNT_ID
        WHEN RISK_RATING IS NULL
            THEN 'Missing risk rating for customer ' || CUSTOMER_ID
    END AS EXCEPTION_DESC
FROM STAGING.CUST_ACCOUNTS_DAILY
WHERE (ACCOUNT_TYPE IN ('CHK','SAV','MMA','CD') AND CURRENT_BALANCE < 0)
   OR UTILIZATION_PCT > 95
   OR RISK_RATING IS NULL;
