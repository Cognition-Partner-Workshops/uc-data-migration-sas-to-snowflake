/*=====================================================================
  JOB05_CREDIT_RISK_SCORING.sql — Snowflake conversion of
  credit_risk_scoring.sas (Model CRM-2023-Q4-v2 scorecard execution)

  Source: ts-sas-legacy-analytics/Programs/Banking/credit_risk_scoring.sas
  SAS schedule: Weekly Sunday 02:00 via Control-M BANK_WEEKLY_01
                (also step 3 of BatchJobs/run_daily_banking.sas)
  Snowflake: Runs as TASK_WEEKLY_CREDIT_RISK (see orchestration/tasks.sql,
             owned by the integration PR)

  Inputs:  STG_BANK.CUST_ACCOUNTS_DAILY (JOB01 output),
           ORA_DW.BUREAU_SCORES, ORA_DW.PAYMENT_HISTORY, ORA_DW.COLLATERAL
  Outputs: CURATED.RISK_SCORES   (append — PROC APPEND semantics)
           CURATED.RISK_MIGRATION (append — PROC APPEND semantics)
           REPORTS.RISK_SUMMARY  (replace — PROC MEANS OUT= semantics)

  Why Snowflake SQL (not Snowpark):
    Steps 1, 3 and 5 are PROC SQL / PROC MEANS (set-based). The step-2
    DATA step is a row-wise scorecard: every statement is an IF/ELSE
    ladder or arithmetic on the current row only — no RETAIN, no
    first./last., no hash objects, no multi-output OUTPUT. It maps 1:1 to
    CASE expressions, so SQL is the faithful and simplest target.

  Conversion notes (see docs/JOB05_credit_risk_scoring.md for the full
  construct map and quirk list):
    - SAS numerics are 8-byte doubles -> input numerics are FLOAT so the
      scorecard arithmetic rounds exactly as SAS does.
    - Every SAS comparison on a possibly-missing value is guarded by
      `not missing()` in the source, so SAS "missing < any number"
      semantics never fire; CASE ... IS NULL branches reproduce the
      explicit `else` defaults.
    - The correlated sub-select on BUREAU_SCORES (latest score on or
      before the score date) becomes a window MAX in a CTE; ties on the
      max date would duplicate account rows exactly as in SAS.
    - SAS `ne` on a missing RISK_RATING is TRUE; the SAS `or ... is null`
      clause already makes the predicate explicit and is kept verbatim.
    - PROC MEANS `n=N_ACCOUNTS` names only the FIRST analysis variable's
      count; the counts of LGD, EAD and EXPECTED_LOSS are emitted under
      the analysis-variable names themselves (SAS default naming). This
      quirk is reproduced, not fixed.
    - %lock / unlock -> no-op (Snowflake DML is transactional).
    - SAS formats (percent8.4, dollar18.2, date9.) are presentation-
      layer and are not applied; raw values are stored.
    - SCORE_TIMESTAMP = datetime() -> CURRENT_TIMESTAMP(); it is
      non-deterministic and excluded from reconciliation.
=====================================================================*/

-- Session variables (set by the orchestrator task or caller).
-- Defaults mirror the SAS macro signature:
--   %credit_risk_scoring(score_date=&CURR_DT, model_id=CRM-2023-Q4-v2)
-- SET score_date = TO_DATE('2024-01-31');
-- SET model_id   = 'CRM-2023-Q4-v2';

-- ----------------------------------------------------------------
-- Step 1: Assemble Scoring Input Features   (SAS lines 32-86)
-- ----------------------------------------------------------------
CREATE OR REPLACE TEMPORARY TABLE WORK_SCORE_INPUT AS
WITH bureau_latest AS (
    -- SAS lines 76-78: b.SCORE_DATE = (select max(SCORE_DATE) ...
    --   where CUSTOMER_ID = b.CUSTOMER_ID and SCORE_DATE <= "&score_date"d)
    SELECT
        b.*,
        MAX(CASE WHEN b.SCORE_DATE <= $score_date THEN b.SCORE_DATE END)
            OVER (PARTITION BY b.CUSTOMER_ID) AS MAX_SCORE_DATE
    FROM ORA_DW.BUREAU_SCORES b
)
SELECT
    a.ACCOUNT_ID,
    a.CUSTOMER_ID,
    a.ACCOUNT_TYPE,
    a.CURRENT_BALANCE,
    a.CREDIT_LIMIT,
    a.ACCT_AGE_MONTHS,
    a.DAYS_INACTIVE,
    a.UTILIZATION_PCT,
    a.CUSTOMER_SEGMENT,
    a.REGION_CODE,

    -- Bureau scores
    b.FICO_SCORE,
    b.VANTAGE_SCORE,
    b.BUREAU_INQS_6MO,
    b.BUREAU_TRADES_OPEN,
    b.BUREAU_DEROGS,
    b.BUREAU_UTIL_PCT,
    b.BUREAU_OLDEST_TRADE_MO,

    -- Payment behavior
    p.PMT_ONTIME_12MO,
    p.PMT_LATE_30_12MO,
    p.PMT_LATE_60_12MO,
    p.PMT_LATE_90_12MO,
    p.MAX_DAYS_PAST_DUE_EVER,
    p.MONTHS_SINCE_LAST_DPD,
    p.AVG_PMT_RATIO_12MO,

    -- Collateral (secured loans)
    c.COLLATERAL_VALUE,
    c.LAST_APPRAISAL_DATE,
    -- SAS lines 67-71: missing COLLATERAL_VALUE fails `> 0` -> LTV missing
    CASE
        WHEN c.COLLATERAL_VALUE > 0
            THEN a.CURRENT_BALANCE / c.COLLATERAL_VALUE
        ELSE NULL
    END AS LTV

FROM STG_BANK.CUST_ACCOUNTS_DAILY a
LEFT JOIN bureau_latest b
    ON a.CUSTOMER_ID = b.CUSTOMER_ID
   AND b.SCORE_DATE = b.MAX_SCORE_DATE
LEFT JOIN ORA_DW.PAYMENT_HISTORY p
    ON a.ACCOUNT_ID = p.ACCOUNT_ID
LEFT JOIN ORA_DW.COLLATERAL c
    ON a.ACCOUNT_ID = c.ACCOUNT_ID
WHERE a.SNAPSHOT_DATE = $score_date
  AND a.ACCOUNT_TYPE IN ('MTG', 'AUTO', 'PERS', 'CC', 'LOC', 'HELC');

-- ----------------------------------------------------------------
-- Step 2: Apply Scorecard Model             (SAS lines 92-197)
-- Coefficients from validated Model CRM-2023-Q4-v2
-- ----------------------------------------------------------------
CREATE OR REPLACE TEMPORARY TABLE WORK_SCORED AS
WITH woe AS (
    SELECT
        i.*,
        -3.2145::FLOAT AS INTERCEPT,                      -- line 96

        -- FICO score contribution (lines 99-107); population average for missing
        CASE
            WHEN FICO_SCORE IS NULL THEN  0.198
            WHEN FICO_SCORE >= 760  THEN -1.204
            WHEN FICO_SCORE >= 720  THEN -0.812
            WHEN FICO_SCORE >= 680  THEN -0.356
            WHEN FICO_SCORE >= 640  THEN  0.198
            WHEN FICO_SCORE >= 600  THEN  0.654
            ELSE 1.102
        END::FLOAT AS WOE_FICO,

        -- Utilization contribution (lines 110-118)
        CASE
            WHEN UTILIZATION_PCT IS NULL THEN  0
            WHEN UTILIZATION_PCT <= 10   THEN -0.956
            WHEN UTILIZATION_PCT <= 30   THEN -0.521
            WHEN UTILIZATION_PCT <= 50   THEN -0.102
            WHEN UTILIZATION_PCT <= 70   THEN  0.334
            WHEN UTILIZATION_PCT <= 90   THEN  0.789
            ELSE 1.245
        END::FLOAT AS WOE_UTIL,

        -- Payment history contribution (lines 121-126)
        CASE
            WHEN PMT_LATE_90_12MO IS NULL THEN  0
            WHEN PMT_LATE_90_12MO = 0     THEN -0.678
            WHEN PMT_LATE_90_12MO = 1     THEN  0.445
            ELSE 1.567
        END::FLOAT AS WOE_DPD,

        -- Account age contribution (lines 129-135)
        CASE
            WHEN ACCT_AGE_MONTHS IS NULL THEN  0
            WHEN ACCT_AGE_MONTHS >= 120  THEN -0.534
            WHEN ACCT_AGE_MONTHS >= 60   THEN -0.289
            WHEN ACCT_AGE_MONTHS >= 24   THEN  0.045
            ELSE 0.456
        END::FLOAT AS WOE_AGE,

        -- LTV contribution, secured only (lines 138-147)
        CASE
            WHEN ACCOUNT_TYPE NOT IN ('MTG', 'AUTO', 'HELC') THEN 0
            WHEN LTV IS NULL  THEN  0
            WHEN LTV <= 0.60  THEN -0.712
            WHEN LTV <= 0.80  THEN -0.234
            WHEN LTV <= 1.00  THEN  0.356
            ELSE 0.889
        END::FLOAT AS WOE_LTV
    FROM WORK_SCORE_INPUT i
),
odds AS (
    SELECT
        w.*,
        -- lines 150-155
        INTERCEPT
          + 0.412 * WOE_FICO
          + 0.198 * WOE_UTIL
          + 0.289 * WOE_DPD
          + 0.067 * WOE_AGE
          + 0.134 * WOE_LTV AS LOG_ODDS
    FROM woe w
),
scored AS (
    SELECT
        o.*,
        1 / (1 + EXP(-LOG_ODDS)) AS PD,                   -- line 157

        -- LGD estimation (lines 161-168)
        CASE
            WHEN ACCOUNT_TYPE IN ('MTG', 'AUTO', 'HELC') THEN
                CASE
                    WHEN LTV IS NOT NULL
                        THEN GREATEST(0, LEAST(1, (LTV - 0.5) * 0.8))
                    ELSE 0.40
                END
            WHEN ACCOUNT_TYPE = 'CC' THEN 0.75
            ELSE 0.50
        END::FLOAT AS LGD,

        -- EAD estimation (lines 172-175); NULL arithmetic propagates as SAS missing does
        CASE
            WHEN ACCOUNT_TYPE IN ('CC', 'LOC', 'HELC')
                THEN CURRENT_BALANCE + 0.50 * (CREDIT_LIMIT - CURRENT_BALANCE)
            ELSE CURRENT_BALANCE
        END::FLOAT AS EAD
    FROM odds o
)
SELECT
    ACCOUNT_ID,
    CUSTOMER_ID,
    ACCOUNT_TYPE,
    CURRENT_BALANCE,
    CREDIT_LIMIT,
    ACCT_AGE_MONTHS,
    DAYS_INACTIVE,
    UTILIZATION_PCT,
    CUSTOMER_SEGMENT,
    REGION_CODE,
    FICO_SCORE,
    VANTAGE_SCORE,
    BUREAU_INQS_6MO,
    BUREAU_TRADES_OPEN,
    BUREAU_DEROGS,
    BUREAU_UTIL_PCT,
    BUREAU_OLDEST_TRADE_MO,
    PMT_ONTIME_12MO,
    PMT_LATE_30_12MO,
    PMT_LATE_60_12MO,
    PMT_LATE_90_12MO,
    MAX_DAYS_PAST_DUE_EVER,
    MONTHS_SINCE_LAST_DPD,
    AVG_PMT_RATIO_12MO,
    COLLATERAL_VALUE,
    LAST_APPRAISAL_DATE,
    LTV,
    PD,
    LGD,
    EAD,
    PD * LGD * EAD AS EXPECTED_LOSS,                      -- line 179

    -- Risk Rating Assignment (lines 183-189)
    CASE
        WHEN PD < 0.005 THEN 1
        WHEN PD < 0.01  THEN 2
        WHEN PD < 0.03  THEN 3
        WHEN PD < 0.07  THEN 4
        WHEN PD < 0.15  THEN 5
        WHEN PD < 0.30  THEN 6
        ELSE 7
    END AS NEW_RISK_RATING,

    $score_date::DATE          AS SCORE_DATE,             -- line 191
    $model_id::VARCHAR         AS MODEL_ID,               -- line 192
    CURRENT_TIMESTAMP()        AS SCORE_TIMESTAMP         -- line 193
FROM scored;
-- line 196: INTERCEPT, WOE_*, LOG_ODDS are dropped (not selected above)

-- ----------------------------------------------------------------
-- Step 3: Risk Migration Matrix             (SAS lines 202-224)
-- ----------------------------------------------------------------
CREATE OR REPLACE TEMPORARY TABLE WORK_RISK_MIGRATION AS
SELECT
    $score_date::DATE      AS SCORE_DATE,
    a.ACCOUNT_ID,
    a.RISK_RATING          AS PREV_RATING,
    s.NEW_RISK_RATING      AS CURR_RATING,
    CASE
        WHEN a.RISK_RATING IS NULL                THEN 'NEW'
        WHEN s.NEW_RISK_RATING < a.RISK_RATING    THEN 'UPGRADE'
        WHEN s.NEW_RISK_RATING > a.RISK_RATING    THEN 'DOWNGRADE'
        ELSE 'STABLE'
    END::VARCHAR(10)       AS MIGRATION_DIRECTION,       -- length=10
    s.PD,
    s.EXPECTED_LOSS
FROM WORK_SCORED s
INNER JOIN STG_BANK.CUST_ACCOUNTS_DAILY a
    ON s.ACCOUNT_ID = a.ACCOUNT_ID
WHERE a.SNAPSHOT_DATE = $score_date
  -- SAS: a missing RISK_RATING is `ne` any number; the explicit IS NULL
  -- arm makes the same rows qualify under SQL three-valued logic.
  AND (a.RISK_RATING <> s.NEW_RISK_RATING
       OR a.RISK_RATING IS NULL);

-- ----------------------------------------------------------------
-- Step 4: Load Scores and Migration to Curated (SAS lines 229-241)
-- PROC APPEND ... FORCE: base is created on first run, rows are
-- appended on every run (no dedup by SCORE_DATE — legacy behaviour).
-- %lock / %lock(..., unlock) have no Snowflake equivalent (transactional).
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS CURATED.RISK_SCORES AS
    SELECT * FROM WORK_SCORED WHERE 1 = 0;
INSERT INTO CURATED.RISK_SCORES
    SELECT * FROM WORK_SCORED;

CREATE TABLE IF NOT EXISTS CURATED.RISK_MIGRATION AS
    SELECT * FROM WORK_RISK_MIGRATION WHERE 1 = 0;
INSERT INTO CURATED.RISK_MIGRATION
    SELECT * FROM WORK_RISK_MIGRATION;

-- ----------------------------------------------------------------
-- Step 5: Risk Summary Report               (SAS lines 246-256)
-- PROC MEANS NOPRINT NWAY CLASS ACCOUNT_TYPE NEW_RISK_RATING
--   VAR PD LGD EAD EXPECTED_LOSS
--   OUTPUT OUT=REPORTS.RISK_SUMMARY(drop=_TYPE_ _FREQ_)
--     n=N_ACCOUNTS mean(PD)=AVG_PD mean(LGD)=AVG_LGD
--     sum(EAD)=TOTAL_EAD sum(EXPECTED_LOSS)=TOTAL_EL
-- `n=N_ACCOUNTS` renames only the first VAR (PD); SAS emits the
-- remaining N statistics under the analysis-variable names LGD, EAD,
-- EXPECTED_LOSS. Column order and names below are the SAS output.
-- Rows with a missing CLASS value are excluded by PROC MEANS default.
-- ----------------------------------------------------------------
CREATE OR REPLACE TABLE REPORTS.RISK_SUMMARY AS
SELECT
    ACCOUNT_TYPE,
    NEW_RISK_RATING,
    COUNT(PD)            AS N_ACCOUNTS,
    COUNT(LGD)           AS LGD,
    COUNT(EAD)           AS EAD,
    COUNT(EXPECTED_LOSS) AS EXPECTED_LOSS,
    AVG(PD)              AS AVG_PD,
    AVG(LGD)             AS AVG_LGD,
    SUM(EAD)             AS TOTAL_EAD,
    SUM(EXPECTED_LOSS)   AS TOTAL_EL
FROM WORK_SCORED
WHERE ACCOUNT_TYPE IS NOT NULL
  AND NEW_RISK_RATING IS NOT NULL
GROUP BY ACCOUNT_TYPE, NEW_RISK_RATING
ORDER BY ACCOUNT_TYPE, NEW_RISK_RATING;

-- SAS lines 264-266: proc datasets delete SCORE_INPUT SCORED RISK_MIGRATION
DROP TABLE IF EXISTS WORK_SCORE_INPUT;
DROP TABLE IF EXISTS WORK_SCORED;
DROP TABLE IF EXISTS WORK_RISK_MIGRATION;
