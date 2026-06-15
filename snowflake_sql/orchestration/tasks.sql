/*=====================================================================
  tasks.sql — Snowflake Task orchestration replacing SAS Control-M

  This file replaces the SAS BatchJobs/run_daily_banking.sas
  orchestrator and its Control-M scheduling. Each Control-M job becomes
  a Snowflake Task; the SAS sequential %run_step dependency chain is
  preserved through Task AFTER predecessors.

  Source: ts-sas-legacy-analytics/BatchJobs/run_daily_banking.sas
          (%run_step steps 1-4, executed in dependency order)

  Control-M job mapping:
    Control-M job    SAS program                      Schedule           Depends on
    --------------   ------------------------------   ----------------   ----------
    BANK_DAILY_01    load_customer_accounts.sas       Daily 06:00        BANK_MASTER (root)
    BANK_DAILY_02    daily_transaction_processing.sas Daily 07:30        BANK_DAILY_01
    BANK_WEEKLY_01   credit_risk_scoring.sas          Weekly Sun         BANK_DAILY_02
    BANK_MONTHLY_01  monthly_regulatory_reporting.sas Monthly 3rd BD     BANK_WEEKLY_01
    INS_DAILY_01     claims_processing.sas            Daily 08:00        (independent)

  Snowflake equivalent: a single Task DAG rooted at
  TASK_DAILY_BANKING_ROOT chaining
    JOB01 -> JOB02 -> CREDIT_RISK -> MONTHLY_REGULATORY
  plus a standalone TASK_INS_CLAIMS_PROCESSING.

  Cadence note: a Snowflake Task may have EITHER a SCHEDULE (root) OR an
  AFTER predecessor (child) — not both. The root runs the DAG daily, and
  the weekly / monthly Control-M cadences are reproduced with WHEN
  predicates that gate the dependent tasks to their intended run days
  while preserving the dependency ordering.
=====================================================================*/

-- Use a dedicated warehouse for batch processing
CREATE WAREHOUSE IF NOT EXISTS WH_BATCH_ETL
    WAREHOUSE_SIZE = 'MEDIUM'
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    COMMENT = 'Batch ETL warehouse — replaces SAS grid resources';


-- =====================================================================
-- Daily banking DAG: root + chained steps (replaces run_daily_banking.sas)
-- =====================================================================

-- Root task: triggers the daily chain (replaces Control-M BANK_MASTER)
-- BANK_MASTER fired at 05:45; the first real step (BANK_DAILY_01) runs 06:00.
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_DAILY_BANKING_ROOT
    WAREHOUSE = WH_BATCH_ETL
    SCHEDULE  = 'USING CRON 0 6 * * * America/New_York'
    COMMENT   = 'Daily banking batch root — replaces Control-M BANK_MASTER'
AS
    -- Step boundary marker; downstream tasks carry the actual ETL.
    SELECT CURRENT_DATE() AS run_date;


-- JOB01: Load Customer Accounts (replaces BANK_DAILY_01, Daily 06:00)
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_JOB01_LOAD_CUST_ACCOUNTS
    WAREHOUSE = WH_BATCH_ETL
    AFTER FINANCE_DB.PUBLIC.TASK_DAILY_BANKING_ROOT
    COMMENT = 'Load customer accounts — replaces load_customer_accounts.sas (BANK_DAILY_01)'
AS
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB01_LOAD_CUST_ACCOUNTS.sql;


-- JOB02: Daily Transaction Processing (replaces BANK_DAILY_02, Daily 07:30 after 01)
-- The fixed 07:30 slot is superseded by dependency ordering: this task
-- starts as soon as JOB01 succeeds.
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_JOB02_DAILY_TRANSACTIONS
    WAREHOUSE = WH_BATCH_ETL
    AFTER FINANCE_DB.PUBLIC.TASK_JOB01_LOAD_CUST_ACCOUNTS
    COMMENT = 'Daily transaction ETL — replaces daily_transaction_processing.sas (BANK_DAILY_02)'
AS
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB02_DAILY_TRANSACTIONS.sql;


-- BANK_WEEKLY_01: Credit Risk Scoring (Weekly Sun, after BANK_DAILY_02)
-- AFTER JOB02 preserves the dependency; WHEN gates execution to Sundays
-- so the body only runs on the weekly cadence.
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_WEEKLY_CREDIT_RISK
    WAREHOUSE = WH_BATCH_ETL
    AFTER FINANCE_DB.PUBLIC.TASK_JOB02_DAILY_TRANSACTIONS
    WHEN DAYNAME(CURRENT_DATE()) = 'Sun'
    COMMENT = 'Weekly credit risk scoring — replaces credit_risk_scoring.sas (BANK_WEEKLY_01)'
AS
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/credit_risk_scoring.sql;


-- BANK_MONTHLY_01: Monthly Regulatory Reporting (Monthly 3rd BD, after BANK_WEEKLY_01)
-- AFTER the weekly task preserves the chain; WHEN gates execution to exactly
-- the 3rd business day. The calendar day of the 3rd business day depends on
-- which ISO weekday the 1st of the month falls on:
--   1st = Mon/Tue/Wed -> calendar day 3
--   1st = Thu/Fri/Sat -> calendar day 5
--   1st = Sun         -> calendar day 4
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_JOB04_MONTHLY_REGULATORY
    WAREHOUSE = WH_BATCH_ETL
    AFTER FINANCE_DB.PUBLIC.TASK_WEEKLY_CREDIT_RISK
    WHEN DAYOFMONTH(CURRENT_DATE()) = CASE DAYOFWEEKISO(DATE_TRUNC('MONTH', CURRENT_DATE()))
             WHEN 1 THEN 3 WHEN 2 THEN 3 WHEN 3 THEN 3
             WHEN 4 THEN 5 WHEN 5 THEN 5 WHEN 6 THEN 5
             WHEN 7 THEN 4 END
    COMMENT = 'Monthly regulatory reporting — replaces monthly_regulatory_reporting.sas (BANK_MONTHLY_01)'
AS
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB04_MONTHLY_REGULATORY.sql;


-- =====================================================================
-- Insurance daily claims — independent of the banking DAG
-- =====================================================================

-- INS_DAILY_01: Claims Processing (Daily 08:00, independent)
-- Standalone root with its own schedule (Snowpark stored procedure).
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_INS_CLAIMS_PROCESSING
    WAREHOUSE = WH_BATCH_ETL
    SCHEDULE  = 'USING CRON 0 8 * * * America/New_York'
    COMMENT   = 'Daily claims processing — Snowpark Python, replaces claims_processing.sas (INS_DAILY_01)'
AS
    CALL FINANCE_DB.PUBLIC.SP_CLAIMS_PROCESSING(CURRENT_DATE()::VARCHAR);


-- =====================================================================
-- Enable the task DAG. Child tasks must be resumed before their root,
-- and the root resumed last, so resume from the leaves up.
-- =====================================================================
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB04_MONTHLY_REGULATORY RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_WEEKLY_CREDIT_RISK RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB02_DAILY_TRANSACTIONS RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB01_LOAD_CUST_ACCOUNTS RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_DAILY_BANKING_ROOT RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_INS_CLAIMS_PROCESSING RESUME;


-- =====================================================================
-- Monitoring: Query task execution history
-- =====================================================================
-- SELECT *
-- FROM TABLE(INFORMATION_SCHEMA.TASK_HISTORY(
--     TASK_NAME => 'TASK_DAILY_BANKING_ROOT',
--     SCHEDULED_TIME_RANGE_START => DATEADD('day', -1, CURRENT_TIMESTAMP())
-- ))
-- ORDER BY SCHEDULED_TIME DESC;
