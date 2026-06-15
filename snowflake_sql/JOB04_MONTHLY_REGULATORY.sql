/*=====================================================================
  JOB04_MONTHLY_REGULATORY.sql — Snowflake conversion of
  monthly_regulatory_reporting.sas

  Source: ts-sas-legacy-analytics/Programs/Banking/monthly_regulatory_reporting.sas
  SAS schedule: Monthly 3rd business day via Control-M BANK_MONTHLY_01
  Snowflake: Runs as a monthly Snowflake Task (see orchestration/tasks.sql)

  Inputs:  STAGING.CUST_ACCOUNTS_DAILY, RAW.LOAN_DETAILS, RAW.COLLATERAL
  Outputs: REPORTS.MONTHLY_RWA, REPORTS.DELINQUENCY_AGING,
           REPORTS.LLP_COVERAGE, REPORTS.CAPITAL_ADEQUACY

  Conversion notes:
    - SAS macro vars &report_month, &month_end → session var $report_month
      and DATE_FROM_PARTS / LAST_DAY functions.
    - SAS "calculated" column refs → Snowflake CTEs or subqueries.
    - Risk weights reproduced EXACTLY from SAS (LOC=1.00, not 0.75).
      See the worked example in the playbook: a previous conversion
      attempt set LOC to 0.75, and the parity check caught it.
=====================================================================*/

-- Session variable: SET report_month = '202401';

-- Step 1: Risk-Weighted Assets by Category (Basel III standardized)
CREATE OR REPLACE TABLE REPORTS.MONTHLY_RWA AS
WITH base AS (
    SELECT
        a.ACCOUNT_TYPE,
        a.CUSTOMER_SEGMENT,
        a.CURRENT_BALANCE,
        l.LTV,
        CASE
            WHEN a.ACCOUNT_TYPE IN ('CHK','SAV','MMA')     THEN 0.00
            WHEN a.ACCOUNT_TYPE = 'CD'                     THEN 0.00
            WHEN a.ACCOUNT_TYPE = 'MTG' AND l.LTV <= 0.80  THEN 0.35
            WHEN a.ACCOUNT_TYPE = 'MTG' AND l.LTV >  0.80  THEN 0.50
            WHEN a.ACCOUNT_TYPE = 'HELC'                   THEN 0.50
            WHEN a.ACCOUNT_TYPE IN ('AUTO','PERS')         THEN 0.75
            WHEN a.ACCOUNT_TYPE = 'CC'                     THEN 0.75
            -- LOC risk weight = 1.00, matching SAS source exactly.
            -- A prior conversion attempt used 0.75 here; the parity
            -- check flagged the RWA divergence.
            WHEN a.ACCOUNT_TYPE = 'LOC'                    THEN 1.00
            ELSE 1.00
        END AS RISK_WEIGHT
    FROM STAGING.CUST_ACCOUNTS_DAILY a
    LEFT JOIN RAW.LOAN_DETAILS l
        ON a.ACCOUNT_ID = l.ACCOUNT_ID
    WHERE a.SNAPSHOT_DATE = LAST_DAY(TO_DATE($report_month || '01', 'YYYYMMDD'))
)
SELECT
    $report_month                        AS REPORT_MONTH,
    ACCOUNT_TYPE,
    CUSTOMER_SEGMENT,
    RISK_WEIGHT,
    COUNT(*)                             AS N_ACCOUNTS,
    SUM(CURRENT_BALANCE)                 AS TOTAL_EXPOSURE,
    SUM(CURRENT_BALANCE * RISK_WEIGHT)   AS RWA
FROM base
GROUP BY 1, 2, 3, 4
ORDER BY ACCOUNT_TYPE, CUSTOMER_SEGMENT;


-- Step 2: Delinquency Aging — 30/60/90/120/180+ Buckets
CREATE OR REPLACE TABLE REPORTS.DELINQUENCY_AGING AS
SELECT
    $report_month AS REPORT_MONTH,
    a.ACCOUNT_TYPE,
    a.REGION_CODE,
    CASE
        WHEN l.DAYS_PAST_DUE = 0              THEN 'Current'
        WHEN l.DAYS_PAST_DUE BETWEEN 1 AND 29   THEN '1-29'
        WHEN l.DAYS_PAST_DUE BETWEEN 30 AND 59  THEN '30-59'
        WHEN l.DAYS_PAST_DUE BETWEEN 60 AND 89  THEN '60-89'
        WHEN l.DAYS_PAST_DUE BETWEEN 90 AND 119 THEN '90-119'
        WHEN l.DAYS_PAST_DUE BETWEEN 120 AND 179 THEN '120-179'
        WHEN l.DAYS_PAST_DUE >= 180            THEN '180+'
        ELSE 'Unknown'
    END AS DELINQ_BUCKET,
    COUNT(*)               AS N_ACCOUNTS,
    SUM(a.CURRENT_BALANCE) AS TOTAL_BALANCE,
    SUM(l.PAST_DUE_AMOUNT) AS TOTAL_PAST_DUE
FROM STAGING.CUST_ACCOUNTS_DAILY a
LEFT JOIN RAW.LOAN_DETAILS l
    ON a.ACCOUNT_ID = l.ACCOUNT_ID
WHERE a.SNAPSHOT_DATE = LAST_DAY(TO_DATE($report_month || '01', 'YYYYMMDD'))
  AND a.ACCOUNT_TYPE IN ('MTG','AUTO','PERS','CC','LOC','HELC')
GROUP BY 1, 2, 3, 4
ORDER BY a.ACCOUNT_TYPE, a.REGION_CODE,
    CASE
        WHEN DELINQ_BUCKET = 'Current'  THEN 0
        WHEN DELINQ_BUCKET = '1-29'     THEN 1
        WHEN DELINQ_BUCKET = '30-59'    THEN 2
        WHEN DELINQ_BUCKET = '60-89'    THEN 3
        WHEN DELINQ_BUCKET = '90-119'   THEN 4
        WHEN DELINQ_BUCKET = '120-179'  THEN 5
        WHEN DELINQ_BUCKET = '180+'     THEN 6
        ELSE 7
    END;


-- Step 3: Loan Loss Provision Coverage
CREATE OR REPLACE TABLE REPORTS.LLP_COVERAGE AS
SELECT
    $report_month AS REPORT_MONTH,
    a.ACCOUNT_TYPE,
    COUNT(*) AS N_LOANS,
    SUM(a.CURRENT_BALANCE) AS GROSS_LOANS,
    SUM(l.ALLOWANCE_AMT)   AS TOTAL_ALLOWANCE,
    CASE
        WHEN SUM(a.CURRENT_BALANCE) > 0
        THEN SUM(l.ALLOWANCE_AMT) / SUM(a.CURRENT_BALANCE) * 100
        ELSE 0
    END AS COVERAGE_PCT,
    SUM(CASE WHEN l.DAYS_PAST_DUE >= 90 THEN a.CURRENT_BALANCE ELSE 0 END)
        AS NPL_BALANCE,
    CASE
        WHEN SUM(CASE WHEN l.DAYS_PAST_DUE >= 90 THEN a.CURRENT_BALANCE ELSE 0 END) > 0
        THEN SUM(l.ALLOWANCE_AMT) /
             SUM(CASE WHEN l.DAYS_PAST_DUE >= 90 THEN a.CURRENT_BALANCE ELSE 0 END) * 100
        ELSE 0
    END AS NPL_COVERAGE_PCT
FROM STAGING.CUST_ACCOUNTS_DAILY a
INNER JOIN RAW.LOAN_DETAILS l
    ON a.ACCOUNT_ID = l.ACCOUNT_ID
WHERE a.SNAPSHOT_DATE = LAST_DAY(TO_DATE($report_month || '01', 'YYYYMMDD'))
  AND a.ACCOUNT_TYPE IN ('MTG','AUTO','PERS','CC','LOC','HELC')
GROUP BY 1, 2;


-- Step 4: Capital Adequacy Summary
CREATE OR REPLACE TABLE REPORTS.CAPITAL_ADEQUACY AS
SELECT
    $report_month                        AS REPORT_MONTH,
    SUM(RWA)                             AS TOTAL_RWA,
    -- Placeholder capitals (would come from GL in production)
    50000000                             AS CET1_CAPITAL,
    65000000                             AS TIER1_CAPITAL,
    80000000                             AS TOTAL_CAPITAL,
    CASE WHEN SUM(RWA) > 0 THEN 50000000 / SUM(RWA) * 100 ELSE NULL END AS CET1_RATIO,
    CASE WHEN SUM(RWA) > 0 THEN 65000000 / SUM(RWA) * 100 ELSE NULL END AS TIER1_RATIO,
    CASE WHEN SUM(RWA) > 0 THEN 80000000 / SUM(RWA) * 100 ELSE NULL END AS TOTAL_CAPITAL_RATIO,
    -- Minimum requirements: CET1=4.5%, Tier1=6%, Total=8%
    CASE WHEN SUM(RWA) = 0 THEN 'PASS'
         WHEN 50000000 / SUM(RWA) * 100 >= 4.5 THEN 'PASS' ELSE 'FAIL' END AS CET1_STATUS,
    CASE WHEN SUM(RWA) = 0 THEN 'PASS'
         WHEN 65000000 / SUM(RWA) * 100 >= 6.0 THEN 'PASS' ELSE 'FAIL' END AS TIER1_STATUS,
    CASE WHEN SUM(RWA) = 0 THEN 'PASS'
         WHEN 80000000 / SUM(RWA) * 100 >= 8.0 THEN 'PASS' ELSE 'FAIL' END AS TOTAL_CAPITAL_STATUS
FROM REPORTS.MONTHLY_RWA;
