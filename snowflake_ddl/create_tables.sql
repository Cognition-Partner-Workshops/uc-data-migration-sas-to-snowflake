-- ============================================================================
-- Snowflake DDL for SAS-to-Snowflake migration sample datasets
-- Source: sample_data/ (CUST_ACCOUNTS.csv, DAILY_BALANCE.csv, MONTHLY_AMB.csv)
--
-- Conventions
-- -----------
-- - All tables live in a dedicated schema (SAS_MIGRATION)
-- - Column names match the CSV headers (UPPER_SNAKE_CASE)
-- - Clustering keys chosen for the most common query patterns
-- - NOT NULL on columns that have no missing values in the source data
-- - DATE / TIMESTAMP types replace SAS date values / formatted strings
-- - DECIMAL(12,2) for monetary amounts (mirrors SAS 8-byte numeric precision)
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS SAS_MIGRATION;
USE SCHEMA SAS_MIGRATION;

-- ----------------------------------------------------------------------------
-- 1. CUST_ACCOUNTS  –  Customer account master
--    Source rows : 1 980
--    Source cols : customer_id, account_id, account_type, is_active,
--                 start_date, end_date
-- ----------------------------------------------------------------------------

CREATE OR REPLACE TABLE SAS_MIGRATION.CUST_ACCOUNTS (
    CUSTOMER_ID     INTEGER       NOT NULL
        COMMENT 'Unique customer identifier',
    ACCOUNT_ID      VARCHAR(36)   NOT NULL
        COMMENT 'Unique account identifier (UUID-style hex string)',
    ACCOUNT_TYPE    VARCHAR(20)   NOT NULL
        COMMENT 'Account category: CHECKING, SAVINGS, or CREDIT',
    IS_ACTIVE       VARCHAR(10)   NOT NULL
        COMMENT 'Account status: ACTIVE or INACTIVE',
    START_DATE      DATE          NOT NULL
        COMMENT 'Date the account was opened (ISO-8601)',
    END_DATE        DATE          NULL
        COMMENT 'Date the account was closed; NULL if still active',

    -- Constraints
    CONSTRAINT pk_cust_accounts PRIMARY KEY (CUSTOMER_ID, ACCOUNT_ID),
    CONSTRAINT chk_account_type CHECK (ACCOUNT_TYPE IN ('CHECKING', 'SAVINGS', 'CREDIT')),
    CONSTRAINT chk_is_active    CHECK (IS_ACTIVE IN ('ACTIVE', 'INACTIVE')),
    CONSTRAINT chk_date_order   CHECK (END_DATE IS NULL OR END_DATE >= START_DATE)
)
CLUSTER BY (CUSTOMER_ID)
COMMENT = 'Customer account master table migrated from SAS sas7bdat / CSV';

-- ----------------------------------------------------------------------------
-- 2. DAILY_BALANCE  –  Daily end-of-day balances
--    Source rows : 122 760
--    Source cols : customer_id, account_id, date, end_of_day_balance, month
-- ----------------------------------------------------------------------------

CREATE OR REPLACE TABLE SAS_MIGRATION.DAILY_BALANCE (
    CUSTOMER_ID         INTEGER        NOT NULL
        COMMENT 'FK to CUST_ACCOUNTS.CUSTOMER_ID',
    ACCOUNT_ID          VARCHAR(36)    NOT NULL
        COMMENT 'FK to CUST_ACCOUNTS.ACCOUNT_ID',
    BALANCE_DATE        DATE           NOT NULL
        COMMENT 'Calendar date for the balance snapshot (renamed from "date")',
    END_OF_DAY_BALANCE  DECIMAL(12,2)  NOT NULL
        COMMENT 'Account balance at end of day',
    MONTH               VARCHAR(7)     NOT NULL
        COMMENT 'Year-month partition key (YYYY-MM format)',

    -- Constraints
    CONSTRAINT pk_daily_balance PRIMARY KEY (CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE),
    CONSTRAINT fk_daily_balance_account
        FOREIGN KEY (CUSTOMER_ID, ACCOUNT_ID)
        REFERENCES SAS_MIGRATION.CUST_ACCOUNTS (CUSTOMER_ID, ACCOUNT_ID)
)
CLUSTER BY (CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE)
COMMENT = 'Daily end-of-day balance records migrated from SAS';

-- ----------------------------------------------------------------------------
-- 3. MONTHLY_AMB  –  Monthly average balance
--    Source rows : 1 980
--    Source cols : customer_id, account_id, reporting_month_yyyymm,
--                 average_monthly_balance, date_computed
-- ----------------------------------------------------------------------------

CREATE OR REPLACE TABLE SAS_MIGRATION.MONTHLY_AMB (
    CUSTOMER_ID                INTEGER        NOT NULL
        COMMENT 'FK to CUST_ACCOUNTS.CUSTOMER_ID',
    ACCOUNT_ID                 VARCHAR(36)    NOT NULL
        COMMENT 'FK to CUST_ACCOUNTS.ACCOUNT_ID',
    REPORTING_MONTH_YYYYMM     INTEGER        NOT NULL
        COMMENT 'Reporting month as YYYYMM integer (e.g. 202507)',
    AVERAGE_MONTHLY_BALANCE    DECIMAL(12,2)  NOT NULL
        COMMENT 'Mean of DAILY_BALANCE.END_OF_DAY_BALANCE for the month',
    DATE_COMPUTED              DATE           NOT NULL
        COMMENT 'Date on which the AMB was calculated',

    -- Constraints
    CONSTRAINT pk_monthly_amb PRIMARY KEY (CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM),
    CONSTRAINT fk_monthly_amb_account
        FOREIGN KEY (CUSTOMER_ID, ACCOUNT_ID)
        REFERENCES SAS_MIGRATION.CUST_ACCOUNTS (CUSTOMER_ID, ACCOUNT_ID)
)
CLUSTER BY (CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM)
COMMENT = 'Monthly average balance aggregates migrated from SAS';
