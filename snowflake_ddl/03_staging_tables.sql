-- =============================================================================
-- Snowflake DDL: STAGING Schema Tables
-- These tables contain cleansed, typed, and transformed data.
-- They correspond to the SAS WORK library staging tables and the
-- final MONTHLY_AMB computation.
-- =============================================================================

USE DATABASE FINANCE_DB;
USE SCHEMA STAGING;

-- ---------------------------------------------------------------------------
-- STAGING.STG_CUST_ACCOUNTS
-- Target of: JOB01_LOAD_CUST_ACCOUNTS
-- Equivalent to SAS WORK.CUST_ACCOUNTS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS STAGING.STG_CUST_ACCOUNTS (
    CUSTOMER_ID     INTEGER        NOT NULL   COMMENT 'Unique identifier for the customer',
    ACCOUNT_ID      VARCHAR(8)     NOT NULL   COMMENT 'Unique identifier for the account (hex string)',
    ACCOUNT_TYPE    VARCHAR(10)    NOT NULL   COMMENT 'Type of account: CHECKING, SAVINGS, or CREDIT',
    IS_ACTIVE       VARCHAR(10)    NOT NULL   COMMENT 'Account status: ACTIVE or INACTIVE',
    START_DATE      DATE           NOT NULL   COMMENT 'Date the account was opened',
    END_DATE        DATE                      COMMENT 'Date the account was closed (NULL if still active)',
    LOADED_AT       TIMESTAMP_NTZ  DEFAULT CURRENT_TIMESTAMP()  COMMENT 'Timestamp when the row was loaded into staging'
)
COMMENT = 'Staged customer account records, cleansed and type-cast from RAW';

-- ---------------------------------------------------------------------------
-- STAGING.STG_DAILY_BALANCE
-- Target of: JOB02_LOAD_DAILY_BALANCE
-- Equivalent to SAS WORK.DAILY_BALANCE
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS STAGING.STG_DAILY_BALANCE (
    CUSTOMER_ID         INTEGER        NOT NULL   COMMENT 'Unique identifier for the customer',
    ACCOUNT_ID          VARCHAR(8)     NOT NULL   COMMENT 'Unique identifier for the account (hex string)',
    DATE                DATE           NOT NULL   COMMENT 'Calendar date of the balance snapshot',
    END_OF_DAY_BALANCE  NUMBER(12,2)   NOT NULL   COMMENT 'Account balance at end of day',
    MONTH               VARCHAR(7)     NOT NULL   COMMENT 'Year-month period in YYYY-MM format',
    LOADED_AT           TIMESTAMP_NTZ  DEFAULT CURRENT_TIMESTAMP()  COMMENT 'Timestamp when the row was loaded into staging'
)
COMMENT = 'Staged daily balance records, cleansed and type-cast from RAW';

-- ---------------------------------------------------------------------------
-- STAGING.MONTHLY_AMB
-- Target of: JOB03_CALC_AMB
-- Joins STG_CUST_ACCOUNTS with STG_DAILY_BALANCE to compute
-- average monthly balance for active accounts only.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS STAGING.MONTHLY_AMB (
    CUSTOMER_ID              INTEGER        NOT NULL   COMMENT 'Unique identifier for the customer',
    ACCOUNT_ID               VARCHAR(8)     NOT NULL   COMMENT 'Unique identifier for the account (hex string)',
    REPORTING_MONTH_YYYYMM   INTEGER        NOT NULL   COMMENT 'Reporting month in YYYYMM numeric format',
    AVERAGE_MONTHLY_BALANCE  NUMBER(12,2)   NOT NULL   COMMENT 'Average of daily end-of-day balances for the month',
    DATE_COMPUTED            DATE           NOT NULL   COMMENT 'Date when the AMB calculation was performed'
)
COMMENT = 'Monthly average balance per customer account, computed from daily balances of active accounts';
