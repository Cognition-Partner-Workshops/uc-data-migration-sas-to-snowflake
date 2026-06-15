/*=====================================================================
  tasks.sql — Snowflake Task orchestration replacing SAS Control-M

  This file replaces the SAS BatchJobs/run_daily_banking.sas
  orchestrator and its Control-M scheduling. Each SAS batch step
  becomes a Snowflake Task with explicit dependency chaining.

  SAS Control-M schedule:
    BANK_DAILY_01  → load_customer_accounts.sas    Daily 06:00
    BANK_DAILY_02  → daily_transaction_processing   Daily 07:30
    BANK_WEEKLY_01 → credit_risk_scoring            Weekly Sun 02:00
    BANK_MONTHLY_01→ monthly_regulatory_reporting    Monthly 3rd BD

  Snowflake equivalent: Task DAG rooted at TASK_DAILY_BANKING_ROOT,
  chaining JOB01 → JOB02 → JOB03 → JOB04 with conditional logic.

  The warehouse, schedule, and error notification are configurable
  via session variables or ALTER TASK.
=====================================================================*/

-- Use a dedicated warehouse for batch processing
CREATE WAREHOUSE IF NOT EXISTS WH_BATCH_ETL
    WAREHOUSE_SIZE = 'MEDIUM'
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    COMMENT = 'Batch ETL warehouse — replaces SAS grid resources';

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
    AFTER FINANCE_DB.PUBLIC.TASK_DAILY_BANKING_ROOT
    COMMENT = 'Load customer accounts — replaces load_customer_accounts.sas'
AS
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB01_LOAD_CUST_ACCOUNTS.sql;


-- JOB02: Daily Transaction Processing (replaces BANK_DAILY_02)
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_JOB02_DAILY_TRANSACTIONS
    WAREHOUSE = WH_BATCH_ETL
    AFTER FINANCE_DB.PUBLIC.TASK_JOB01_LOAD_CUST_ACCOUNTS
    COMMENT = 'Daily transaction ETL — replaces daily_transaction_processing.sas'
AS
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB02_DAILY_TRANSACTIONS.sql;


-- JOB03: Calculate Monthly Average Balance
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_JOB03_CALC_AMB
    WAREHOUSE = WH_BATCH_ETL
    AFTER FINANCE_DB.PUBLIC.TASK_JOB02_DAILY_TRANSACTIONS
    COMMENT = 'Calculate monthly AMB — no is_active filter (matches SAS source)'
AS
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB03_CALC_AMB.sql;


-- JOB04: Monthly Regulatory Reporting (conditional — runs on 3rd business day)
-- Replaces Control-M BANK_MONTHLY_01
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_JOB04_MONTHLY_REGULATORY
    WAREHOUSE = WH_BATCH_ETL
    AFTER FINANCE_DB.PUBLIC.TASK_JOB03_CALC_AMB
    WHEN SYSTEM$STREAM_HAS_DATA('FINANCE_DB.PUBLIC.MONTHLY_TRIGGER_STREAM')
         OR DAYOFMONTH(CURRENT_DATE()) <= 5
    COMMENT = 'Monthly regulatory reporting — replaces monthly_regulatory_reporting.sas'
AS
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/JOB04_MONTHLY_REGULATORY.sql;


-- Insurance daily claims (Snowpark stored procedure)
-- Replaces Control-M INS_DAILY_01
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_INS_CLAIMS_PROCESSING
    WAREHOUSE = WH_BATCH_ETL
    SCHEDULE  = 'USING CRON 0 8 * * * America/New_York'
    COMMENT   = 'Daily claims processing — Snowpark Python, replaces claims_processing.sas'
AS
    CALL FINANCE_DB.PUBLIC.SP_CLAIMS_PROCESSING(CURRENT_DATE()::VARCHAR);


-- Weekly credit risk scoring (replaces BANK_WEEKLY_01)
CREATE OR REPLACE TASK FINANCE_DB.PUBLIC.TASK_WEEKLY_CREDIT_RISK
    WAREHOUSE = WH_BATCH_ETL
    SCHEDULE  = 'USING CRON 0 2 * * SUN America/New_York'
    COMMENT   = 'Weekly credit risk scoring — replaces credit_risk_scoring.sas'
AS
    EXECUTE IMMEDIATE FROM @FINANCE_DB.PUBLIC.SQL_STAGE/credit_risk_scoring.sql;


-- =====================================================================
-- Enable the task DAG (tasks are created suspended by default)
-- =====================================================================
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB04_MONTHLY_REGULATORY RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB03_CALC_AMB RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB02_DAILY_TRANSACTIONS RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_JOB01_LOAD_CUST_ACCOUNTS RESUME;
ALTER TASK FINANCE_DB.PUBLIC.TASK_DAILY_BANKING_ROOT RESUME;
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
