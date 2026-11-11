/*=====================================================================
  JOB07_CUSTOMER_PROFITABILITY.sql — Snowflake conversion of
  customer_profitability.sas

  Source: ts-sas-legacy-analytics/Programs/Reports/customer_profitability.sas
  SAS schedule: Monthly 10th business day via Control-M BANK_MONTHLY_03
  Snowflake: Runs as a monthly Snowflake Task (orchestration owned by
             the integration PR; nothing here edits tasks.sql)

  Inputs:  STAGING.CUST_ACCOUNTS_DAILY   (SAS STG_BANK.CUST_ACCOUNTS_DAILY)
           CURATED.DAILY_TRANSACTIONS    (SAS CURATED.DAILY_TRANSACTIONS)
           CURATED.RISK_SCORES           (SAS CURATED.RISK_SCORES)
           ORA_DW.COST_OF_FUNDS is listed in the SAS header but never
           referenced in the SAS body, so it is not referenced here.
  Outputs: REPORTS.CUSTOMER_PNL, REPORTS.SEGMENT_PROFITABILITY,
           REPORTS.BRANCH_PROFITABILITY

  !! BLOCKING LEGACY DEFECT (reproduced, not fixed) !!
    Step 2 (SAS lines 65-80) selects t.CUSTOMER_ID from
    CURATED.DAILY_TRANSACTIONS. That table has no CUSTOMER_ID column
    (daily_transaction_processing.sas appends WORK.TXN_WITH_BALANCE with
    PROC APPEND ... FORCE into the base created by Data/load_seed_data.sas,
    which drops the enrichment columns). SAS fails with
      ERROR: PROC SQL: the following columns were not found in the
             contributing tables: CUSTOMER_ID
    This script references the same column so it fails the same way on
    the estate data (Snowflake: "invalid identifier 'T.CUSTOMER_ID'").
    The minimal fix would be a join through STAGING.CUST_ACCOUNTS_DAILY
    on ACCOUNT_ID — it is NOT applied here; it is a business decision.

  Conversion notes:
    - SAS macro var &report_month (default &PREV_YM) → session variable
      $report_month. &month_start / &month_end → TO_DATE / LAST_DAY.
    - PROC SQL "calculated" refs → CTE (lending/deposit sums computed
      once in a CTE, NET_INTEREST_INCOME derived from them).
    - SAS numerics are IEEE doubles: all numeric inputs are expected as
      FLOAT so that CURRENT_BALANCE * INTEREST_RATE / 12 is evaluated in
      double precision like SAS (NUMBER division would rescale).
    - SAS missing-value rules: SUM() ignores missing (same as SQL);
      sum(x, y, 0) → COALESCE(x,0)+COALESCE(y,0); "x > 0" with x missing
      is false (same as SQL NULL → ELSE branch); PROFIT_TIER else-branch
      catches missing NET_PROFIT (same as SQL).
    - SAS "SCORE_DATE = (select max ...)" is a SAS equality where a
      missing subquery result would match missing SCORE_DATE rows →
      EQUAL_NULL reproduces that; plain "=" would not.
    - MERGE ... BY CUSTOMER_ID; IF a; with three one-row-per-key inputs
      → LEFT JOIN from INTEREST_INCOME.
    - PROC MEANS NWAY CLASS → GROUP BY with "class value IS NOT NULL"
      (PROC MEANS drops observations with a missing CLASS value);
      n=N_CUSTOMERS → COUNT(first VAR variable = TOTAL_REVENUE);
      sum= keeps the analysis-variable names; mean(NET_PROFIT) → AVG.
    - SAS formats dollar18.2 / percent8.4 are presentation-only; raw
      values are stored. $CUSTSEG./$REGION. have OTHER buckets — see
      docs/JOB07_customer_profitability.md (open question).
    - %export_xlsx (SAS lines 159-163) writes a spreadsheet to
      &REPORT_PATH; file export is out of scope for the table migration
      and is flagged in the docs. %nobs log note and PROC DATASETS
      WORK cleanup have no Snowflake equivalent (nothing to persist).
=====================================================================*/

-- Session variable (set by the orchestrator task or caller)
-- SET report_month = '202401';

-- Step 1: Interest Income by Customer  (SAS lines 35-60)
CREATE OR REPLACE TABLE REPORTS.CUSTOMER_PNL AS
WITH interest_income AS (
    SELECT
        a.CUSTOMER_ID,
        MAX(a.CUSTOMER_SEGMENT) AS CUSTOMER_SEGMENT,
        MAX(a.REGION_CODE)      AS REGION_CODE,
        MAX(a.BRANCH_ID)        AS BRANCH_ID,
        SUM(CASE WHEN a.ACCOUNT_TYPE IN ('MTG','AUTO','PERS','CC','LOC','HELC')
                 THEN a.CURRENT_BALANCE * a.INTEREST_RATE / 12 ELSE 0 END)
            AS LENDING_INCOME,
        SUM(CASE WHEN a.ACCOUNT_TYPE IN ('CHK','SAV','MMA','CD','IRA')
                 THEN a.CURRENT_BALANCE * a.INTEREST_RATE / 12 ELSE 0 END)
            AS DEPOSIT_COST,
        COUNT(DISTINCT a.ACCOUNT_ID) AS NUM_ACCOUNTS,
        SUM(a.CURRENT_BALANCE)       AS TOTAL_RELATIONSHIP
    FROM STAGING.CUST_ACCOUNTS_DAILY a
    WHERE a.SNAPSHOT_DATE = LAST_DAY(TO_DATE($report_month || '01', 'YYYYMMDD'))
    GROUP BY a.CUSTOMER_ID
),
interest_income_calc AS (
    -- "calculated LENDING_INCOME - calculated DEPOSIT_COST" (SAS line 52)
    SELECT
        CUSTOMER_ID, CUSTOMER_SEGMENT, REGION_CODE, BRANCH_ID,
        LENDING_INCOME, DEPOSIT_COST,
        LENDING_INCOME - DEPOSIT_COST AS NET_INTEREST_INCOME,
        NUM_ACCOUNTS, TOTAL_RELATIONSHIP
    FROM interest_income
),
-- Step 2: Fee Income from Transactions  (SAS lines 65-80)
-- t.CUSTOMER_ID does not exist on CURATED.DAILY_TRANSACTIONS in the
-- estate: this fails exactly as SAS does. Deliberately NOT rewritten.
fee_income AS (
    SELECT
        t.CUSTOMER_ID,
        SUM(CASE WHEN t.TRANSACTION_TYPE = 'FEE'
                 THEN ABS(t.TRANSACTION_AMOUNT) ELSE 0 END) AS FEE_INCOME,
        SUM(CASE WHEN t.TRANSACTION_TYPE = 'INT'
                 THEN ABS(t.TRANSACTION_AMOUNT) ELSE 0 END) AS INT_CREDITED,
        COUNT(*) AS TXN_VOLUME
    FROM CURATED.DAILY_TRANSACTIONS t
    WHERE t.TRANSACTION_DATE BETWEEN TO_DATE($report_month || '01', 'YYYYMMDD')
                                 AND LAST_DAY(TO_DATE($report_month || '01', 'YYYYMMDD'))
    GROUP BY t.CUSTOMER_ID
),
-- Step 3: Expected Credit Loss by Customer  (SAS lines 85-95)
ecl AS (
    SELECT
        r.CUSTOMER_ID,
        SUM(r.EXPECTED_LOSS) AS TOTAL_ECL
    FROM CURATED.RISK_SCORES r
    WHERE EQUAL_NULL(r.SCORE_DATE,
                     (SELECT MAX(SCORE_DATE) FROM CURATED.RISK_SCORES
                       WHERE SCORE_DATE <= LAST_DAY(TO_DATE($report_month || '01', 'YYYYMMDD'))))
    GROUP BY r.CUSTOMER_ID
),
-- Step 4: Customer P&L Assembly  (SAS lines 100-135)
pnl AS (
    SELECT
        i.CUSTOMER_ID,
        i.CUSTOMER_SEGMENT,
        i.REGION_CODE,
        i.BRANCH_ID,
        i.LENDING_INCOME,
        i.DEPOSIT_COST,
        i.NET_INTEREST_INCOME,
        i.NUM_ACCOUNTS,
        i.TOTAL_RELATIONSHIP,
        f.FEE_INCOME,
        f.INT_CREDITED,
        f.TXN_VOLUME,
        e.TOTAL_ECL,
        -- OPERATING_COST = NUM_ACCOUNTS * 15  (line 109)
        i.NUM_ACCOUNTS * 15 AS OPERATING_COST,
        -- TOTAL_REVENUE = sum(NET_INTEREST_INCOME, FEE_INCOME, 0)  (line 113)
        COALESCE(i.NET_INTEREST_INCOME, 0) + COALESCE(f.FEE_INCOME, 0) AS TOTAL_REVENUE
    FROM interest_income_calc i
    LEFT JOIN fee_income f ON f.CUSTOMER_ID = i.CUSTOMER_ID
    LEFT JOIN ecl        e ON e.CUSTOMER_ID = i.CUSTOMER_ID
),
pnl_profit AS (
    SELECT
        p.*,
        -- NET_PROFIT = TOTAL_REVENUE - OPERATING_COST - coalesce(TOTAL_ECL, 0)  (line 117)
        p.TOTAL_REVENUE - p.OPERATING_COST - COALESCE(p.TOTAL_ECL, 0) AS NET_PROFIT
    FROM pnl p
)
SELECT
    CUSTOMER_ID,
    CUSTOMER_SEGMENT,
    REGION_CODE,
    BRANCH_ID,
    LENDING_INCOME,
    DEPOSIT_COST,
    NET_INTEREST_INCOME,
    NUM_ACCOUNTS,
    TOTAL_RELATIONSHIP,
    FEE_INCOME,
    INT_CREDITED,
    TXN_VOLUME,
    TOTAL_ECL,
    OPERATING_COST,
    TOTAL_REVENUE,
    NET_PROFIT,
    -- ROA (annualized)  (lines 121-124): missing when TOTAL_RELATIONSHIP is not > 0
    CASE WHEN TOTAL_RELATIONSHIP > 0
         THEN (NET_PROFIT * 12) / TOTAL_RELATIONSHIP
         ELSE NULL END AS ROA,
    -- PROFIT_TIER $20  (lines 128-132)
    CAST(CASE WHEN NET_PROFIT >= 500 THEN 'Highly Profitable'
              WHEN NET_PROFIT >= 100 THEN 'Profitable'
              WHEN NET_PROFIT >= 0   THEN 'Marginal'
              ELSE 'Unprofitable' END AS VARCHAR(20)) AS PROFIT_TIER,
    -- REPORT_MONTH = "&report_month"  (line 134)
    CAST($report_month AS VARCHAR(6)) AS REPORT_MONTH
FROM pnl_profit
ORDER BY CUSTOMER_ID;

-- Step 5a: Segment summary  (SAS lines 140-148)
-- PROC MEANS NWAY CLASS CUSTOMER_SEGMENT: observations with a missing
-- class value are excluded; n= is the N of the first VAR variable.
CREATE OR REPLACE TABLE REPORTS.SEGMENT_PROFITABILITY AS
SELECT
    CUSTOMER_SEGMENT,
    COUNT(TOTAL_REVENUE)   AS N_CUSTOMERS,
    SUM(TOTAL_REVENUE)     AS TOTAL_REVENUE,
    SUM(OPERATING_COST)    AS OPERATING_COST,
    SUM(TOTAL_ECL)         AS TOTAL_ECL,
    SUM(NET_PROFIT)        AS NET_PROFIT,
    SUM(TOTAL_RELATIONSHIP) AS TOTAL_RELATIONSHIP,
    AVG(NET_PROFIT)        AS AVG_PROFIT_PER_CUSTOMER
FROM REPORTS.CUSTOMER_PNL
WHERE CUSTOMER_SEGMENT IS NOT NULL
GROUP BY CUSTOMER_SEGMENT
ORDER BY CUSTOMER_SEGMENT;

-- Step 5b: Branch summary  (SAS lines 150-157)
CREATE OR REPLACE TABLE REPORTS.BRANCH_PROFITABILITY AS
SELECT
    BRANCH_ID,
    REGION_CODE,
    COUNT(TOTAL_REVENUE)   AS N_CUSTOMERS,
    SUM(TOTAL_REVENUE)     AS TOTAL_REVENUE,
    SUM(OPERATING_COST)    AS OPERATING_COST,
    SUM(TOTAL_ECL)         AS TOTAL_ECL,
    SUM(NET_PROFIT)        AS NET_PROFIT
FROM REPORTS.CUSTOMER_PNL
WHERE BRANCH_ID IS NOT NULL AND REGION_CODE IS NOT NULL
GROUP BY BRANCH_ID, REGION_CODE
ORDER BY BRANCH_ID, REGION_CODE;

-- %export_xlsx(REPORTS.SEGMENT_PROFITABILITY -> &REPORT_PATH/PROFITABILITY_&report_month..xlsx)
-- is a file-system side effect with no table equivalent; flagged in docs, not reproduced.
