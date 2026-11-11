/*=====================================================================
  tasks.sql — Snowflake Task orchestration replacing SAS Control-M

  This file replaces the SAS BatchJobs/run_daily_banking.sas and
  BatchJobs/run_daily_insurance.sas orchestrators and their Control-M
  scheduling. Each SAS batch step becomes a Snowflake Task with
  explicit dependency chaining.

  SAS Control-M schedule (program headers) and batch order:
    BANK_DAILY_01   → load_customer_accounts.sas       Daily 06:00   (run_daily_banking step 1)
    BANK_DAILY_02   → daily_transaction_processing.sas  Daily 07:30   (run_daily_banking step 2)
    BANK_WEEKLY_01  → credit_risk_scoring.sas           Weekly Sun 02:00 (also run_daily_banking step 3)
    BANK_MONTHLY_01 → monthly_regulatory_reporting.sas  Monthly 3rd BD (run_daily_banking step 4)
    BANK_MONTHLY_03 → customer_profitability.sas        Monthly 10th BD (no BatchJobs orchestrator)
    INS_DAILY_01    → claims_processing.sas             Daily 08:00   (run_daily_insurance step 1)
    INS_MONTHLY_01  → policy_valuation.sas              header: Monthly 5th BD;
                                                        batch: run_daily_insurance step 2 (daily 07:00)

  Snowflake equivalent:
    Banking DAG rooted at TASK_DAILY_BANKING_ROOT, chaining
      JOB01 → JOB02 → JOB03 → JOB04 and JOB02 → JOB07 (calendar-gated).
    Insurance DAG rooted at TASK_INS_CLAIMS_PROCESSING → TASK_JOB06_POLICY_VALUATION.
    Standalone weekly TASK_WEEKLY_CREDIT_RISK (JOB05).

  Business date: every SAS program defaults its date parameter to &CURR_DT
  (production autoexec.sas: today()) and the monthly reports to &PREV_YM
  (production autoexec.sas: previous month). The tasks pass CURRENT_DATE()
  / the previous month the same way. Session variables do not cross task
  boundaries, so each task that runs a $variable-driven script sets its
  own variables in a Snowflake Scripting block before EXECUTE IMMEDIATE FROM.

  Calendar gates: Snowflake Task WHEN conditions only accept
  SYSTEM$STREAM_HAS_DATA / SYSTEM$GET_PREDECESSOR_RETURN_VALUE /
  SYSTEM$PREDECESSORS_STATE / SYSTEM$TASK_COMPLETION_STATE (error 091464
  otherwise), so "Nth business day" gates live inside the task body.
  CRON cannot express business days either; the approximations are
  documented next to each task and in docs/INTEGRATION.md.

  Deployment artefacts expected on @FINANCE_DB.PUBLIC.SQL_STAGE:
    JOB01_LOAD_CUST_ACCOUNTS.sql, JOB03_CALC_AMB.sql,
    JOB04_MONTHLY_REGULATORY.sql, JOB05_CREDIT_RISK_SCORING.sql,
    JOB06_POLICY_VALUATION.sql, JOB07_CUSTOMER_PROFITABILITY.sql
    (snowflake_sql/), and JOB02_daily_transaction_processing.py (snowpark/).

  The warehouse, schedule, and error notification are configurable
  via session variables or ALTER TASK.
=====================================================================*/

-- Use a dedicated warehouse for batch processing
CREATE WAREHOUSE IF NOT EXISTS WH_BATCH_ETL
    WAREHOUSE_SIZE = 'MEDIUM'
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    COMMENT = 'Batch ETL warehouse — replaces SAS grid resources';


-- =====================================================================
-- Snowpark stored procedures called by the tasks
-- =====================================================================

-- JOB02: snowpark/JOB02_daily_transaction_processing.py, entry point handler(session, txnDate)
-- (SAS %daily_transaction_processing(txn_date=&CURR_DT)). Upload the module to
-- the stage before creating the procedure.
CREATE OR REPLACE PROCEDURE FINANCE_DB.PUBLIC.SP_DAILY_TRANSACTION_PROCESSING(TXN_DATE VARCHAR)
    RETURNS VARCHAR
    LANGUAGE PYTHON
    RUNTIME_VERSION = '3.10'
    PACKAGES = ('snowflake-snowpark-python')
    IMPORTS = ('@FINANCE_DB.PUBLIC.SQL_STAGE/JOB02_daily_transaction_processing.py')
    HANDLER = 'JOB02_daily_transaction_processing.handler'
    COMMENT = 'Daily transaction ETL — Snowpark conversion of daily_transaction_processing.sas';


-- =====================================================================
-- Daily banking DAG (replaces run_daily_banking.sas / Control-M BANK_MASTER)
-- =====================================================================

-- Root task: triggers the daily chain (replaces Control-M BANK_MASTER)
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_DAILY_BANKING_ROOT
    WAREHOUSE = WH_BATCH_ETL
    SCHEDULE  = 'USING CRON 0 6 * * * America/New_York'
    COMMENT   = 'Daily banking batch root — replaces Control-M BANK_MASTER'
AS
    -- Set session variables for downstream tasks
    SELECT CURRENT_DATE() AS run_date;


-- JOB01: Load Customer Accounts (replaces BANK_DAILY_01)
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_JOB01_LOAD_CUST_ACCOUNTS
    WAREHOUSE = WH_BATCH_ETL
    COMMENT = 'Load customer accounts — replaces load_customer_accounts.sas'
    AFTER FINANCE_DB.PUBLIC.TASK_DAILY_BANKING_ROOT
AS
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB01_LOAD_CUST_ACCOUNTS.sql;


-- JOB02: Daily Transaction Processing (replaces BANK_DAILY_02, run_daily_banking step 2)
-- Snowpark Python: snowpark/JOB02_daily_transaction_processing.py via
-- SP_DAILY_TRANSACTION_PROCESSING (registered above). txn_date = &CURR_DT.
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_JOB02_DAILY_TRANSACTIONS
    WAREHOUSE = WH_BATCH_ETL
    COMMENT = 'Daily transaction ETL — Snowpark Python, replaces daily_transaction_processing.sas'
    AFTER FINANCE_DB.PUBLIC.TASK_JOB01_LOAD_CUST_ACCOUNTS
AS
    CALL FINANCE_DB.PUBLIC.SP_DAILY_TRANSACTION_PROCESSING(CURRENT_DATE()::VARCHAR);


-- JOB03: Calculate Monthly Average Balance
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_JOB03_CALC_AMB
    WAREHOUSE = WH_BATCH_ETL
    COMMENT = 'Calculate monthly AMB — no is_active filter (matches SAS source)'
    AFTER FINANCE_DB.PUBLIC.TASK_JOB02_DAILY_TRANSACTIONS
AS
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB03_CALC_AMB.sql;


-- JOB04: Monthly Regulatory Reporting (conditional — runs on 3rd business day)
-- Replaces Control-M BANK_MONTHLY_01
-- The former WHEN clause (SYSTEM$STREAM_HAS_DATA(...) OR DAYOFMONTH(CURRENT_DATE()) <= 5)
-- is rejected by Snowflake (091464: DAYOFMONTH is not allowed in a task condition),
-- so the same OR gate is evaluated inside the body. Gate semantics unchanged:
-- day-of-month <= 5 approximates "3rd business day" (Control-M business-day
-- calendars have no Snowflake equivalent).
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_JOB04_MONTHLY_REGULATORY
    WAREHOUSE = WH_BATCH_ETL
    COMMENT = 'Monthly regulatory reporting — replaces monthly_regulatory_reporting.sas'
    AFTER FINANCE_DB.PUBLIC.TASK_JOB03_CALC_AMB
AS
BEGIN
    IF (SYSTEM$STREAM_HAS_DATA('FINANCE_DB.PUBLIC.MONTHLY_TRIGGER_STREAM')
        OR DAYOFMONTH(CURRENT_DATE()) <= 5) THEN
        EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB04_MONTHLY_REGULATORY.sql;
    END IF;
END;


-- JOB07: Customer Profitability (replaces Control-M BANK_MONTHLY_03, monthly 10th business day)
-- Snowflake SQL: snowflake_sql/JOB07_CUSTOMER_PROFITABILITY.sql
-- Not part of any BatchJobs/ orchestrator in SAS; it reads STG_BANK.CUST_ACCOUNTS_DAILY
-- (JOB01), CURATED.DAILY_TRANSACTIONS (JOB02) and CURATED.RISK_SCORES (JOB05), so it is
-- chained AFTER JOB02 in the daily DAG. The JOB05 dependency cannot be expressed:
-- TASK_WEEKLY_CREDIT_RISK is a separate root (own CRON) and AFTER only works inside one
-- DAG; the script itself takes the latest RISK_SCORES on or before month-end, as SAS does.
-- Calendar gate: the 10th business day falls on the 14th in 5 of 7 month-start weekdays
-- (12th/13th otherwise, later with holidays); DAYOFMONTH = 14 is the closest fixed-day
-- approximation. report_month = &PREV_YM = previous calendar month (autoexec.sas).
-- NOTE: on the estate data the script fails exactly like SAS (CURATED.DAILY_TRANSACTIONS
-- has no CUSTOMER_ID) — a legacy defect flagged in PR #23, deliberately not fixed here.
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_JOB07_CUSTOMER_PROFITABILITY
    WAREHOUSE = WH_BATCH_ETL
    COMMENT = 'Monthly customer profitability — replaces customer_profitability.sas (BANK_MONTHLY_03, 10th BD ~ day 14)'
    AFTER FINANCE_DB.PUBLIC.TASK_JOB02_DAILY_TRANSACTIONS
AS
BEGIN
    IF (DAYOFMONTH(CURRENT_DATE()) = 14) THEN
        EXECUTE IMMEDIATE 'SET report_month = TO_CHAR(DATEADD(month, -1, CURRENT_DATE()), ''YYYYMM'')';
        EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB07_CUSTOMER_PROFITABILITY.sql;
    END IF;
END;


-- =====================================================================
-- Daily insurance DAG (replaces run_daily_insurance.sas / Control-M INS_MASTER)
-- =====================================================================

-- Insurance daily claims (Snowpark stored procedure)
-- Replaces Control-M INS_DAILY_01 (run_daily_insurance step 1)
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_INS_CLAIMS_PROCESSING
    WAREHOUSE = WH_BATCH_ETL
    SCHEDULE  = 'USING CRON 0 8 * * * America/New_York'
    COMMENT   = 'Daily claims processing — Snowpark Python, replaces claims_processing.sas'
AS
    CALL FINANCE_DB.PUBLIC.SP_CLAIMS_PROCESSING(CURRENT_DATE()::VARCHAR);


-- JOB06: Policy Valuation (run_daily_insurance step 2, after claims_processing)
-- Snowflake SQL: snowflake_sql/JOB06_POLICY_VALUATION.sql
-- The program header says "Monthly 5th business day via Control-M INS_MONTHLY_01", but
-- the estate's batch runs it daily as step 2 of INS_MASTER; the batch order is mirrored
-- here (AFTER claims), which is the stricter of the two. val_date = &CURR_DT, lob = ALL
-- (the batch always passes lob=ALL).
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_JOB06_POLICY_VALUATION
    WAREHOUSE = WH_BATCH_ETL
    COMMENT = 'Policy valuation — replaces policy_valuation.sas (INS_MONTHLY_01 header; run_daily_insurance step 2)'
    AFTER FINANCE_DB.PUBLIC.TASK_INS_CLAIMS_PROCESSING
AS
BEGIN
    EXECUTE IMMEDIATE 'SET val_date = CURRENT_DATE()';
    EXECUTE IMMEDIATE 'SET lob = ''ALL''';
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB06_POLICY_VALUATION.sql;
END;


-- =====================================================================
-- Weekly credit risk scoring (replaces BANK_WEEKLY_01)
-- =====================================================================

-- JOB05: Snowflake SQL: snowflake_sql/JOB05_CREDIT_RISK_SCORING.sql
-- score_date = &CURR_DT, model_id = CRM-2023-Q4-v2 (SAS macro defaults).
-- NOTE: run_daily_banking.sas also runs credit_risk_scoring.sas daily as step 3
-- (between JOB02 and JOB04); the program header's weekly BANK_WEEKLY_01 schedule is
-- kept here as before. The script appends blindly (PROC APPEND semantics), so running
-- it both daily and weekly would duplicate CURATED.RISK_SCORES rows — see docs/INTEGRATION.md.
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_WEEKLY_CREDIT_RISK
    WAREHOUSE = WH_BATCH_ETL
    SCHEDULE  = 'USING CRON 0 2 * * SUN America/New_York'
    COMMENT   = 'Weekly credit risk scoring — replaces credit_risk_scoring.sas'
AS
BEGIN
    EXECUTE IMMEDIATE 'SET score_date = CURRENT_DATE()';
    EXECUTE IMMEDIATE 'SET model_id = ''CRM-2023-Q4-v2''';
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB05_CREDIT_RISK_SCORING.sql;
END;


-- =====================================================================
-- Enable the task DAG (tasks are created suspended by default)
-- Children must be resumed before their root.
-- =====================================================================
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB07_CUSTOMER_PROFITABILITY RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB04_MONTHLY_REGULATORY RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB03_CALC_AMB RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB02_DAILY_TRANSACTIONS RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB01_LOAD_CUST_ACCOUNTS RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_DAILY_BANKING_ROOT RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB06_POLICY_VALUATION RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_INS_CLAIMS_PROCESSING RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_WEEKLY_CREDIT_RISK RESUME;


-- =====================================================================
-- Monitoring: Query task execution history
-- =====================================================================
-- SELECT *
-- FROM TABLE(INFORMATION_SCHEMA.TASK_HISTORY(
--     TASK_NAME => 'TASK_DAILY_BANKING_ROOT',
--     SCHEDULED_TIME_RANGE_START => DATEADD('day', -1, CURRENT_TIMESTAMP())
-- ))
-- ORDER BY SCHEDULED_TIME DESC;
