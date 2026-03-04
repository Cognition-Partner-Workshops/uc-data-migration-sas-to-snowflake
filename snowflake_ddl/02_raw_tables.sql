-- =============================================================================
-- Snowflake DDL: RAW Schema Tables
-- These tables receive data as-is from the SAS source system.
-- Column types are inferred from the sample CSV data files.
-- =============================================================================

USE DATABASE FINANCE_DB;
USE SCHEMA RAW;

-- ---------------------------------------------------------------------------
-- RAW.CUST_ACCOUNTS
-- Source: SAS Finance_DB.RAW.CUST_ACCOUNTS
-- Loaded by: JOB01_LOAD_CUST_ACCOUNTS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS RAW.CUST_ACCOUNTS (
    CUSTOMER_ID     INTEGER        NOT NULL   COMMENT 'Unique identifier for the customer',
    ACCOUNT_ID      VARCHAR(8)     NOT NULL   COMMENT 'Unique identifier for the account (hex string)',
    ACCOUNT_TYPE    VARCHAR(10)    NOT NULL   COMMENT 'Type of account: CHECKING, SAVINGS, or CREDIT',
    IS_ACTIVE       VARCHAR(10)    NOT NULL   COMMENT 'Account status: ACTIVE or INACTIVE',
    START_DATE      DATE           NOT NULL   COMMENT 'Date the account was opened',
    END_DATE        DATE                      COMMENT 'Date the account was closed (NULL if still active)'
)
COMMENT = 'Raw customer account records ingested from the SAS source system';

-- ---------------------------------------------------------------------------
-- RAW.DAILY_BALANCE
-- Source: SAS Finance_DB.RAW.DAILY_BALANCE
-- Loaded by: JOB02_LOAD_DAILY_BALANCE
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS RAW.DAILY_BALANCE (
    CUSTOMER_ID         INTEGER        NOT NULL   COMMENT 'Unique identifier for the customer',
    ACCOUNT_ID          VARCHAR(8)     NOT NULL   COMMENT 'Unique identifier for the account (hex string)',
    DATE                DATE           NOT NULL   COMMENT 'Calendar date of the balance snapshot',
    END_OF_DAY_BALANCE  NUMBER(12,2)   NOT NULL   COMMENT 'Account balance at end of day',
    MONTH               VARCHAR(7)     NOT NULL   COMMENT 'Year-month period in YYYY-MM format'
)
COMMENT = 'Raw daily end-of-day balance records ingested from the SAS source system';
