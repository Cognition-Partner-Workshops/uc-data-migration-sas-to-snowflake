-- ============================================================================
-- Snowflake DDL: SAS-to-Snowflake Migration Target Schema
-- Database: SAS_MIGRATION  |  Schema: BANKING
-- ============================================================================

CREATE DATABASE IF NOT EXISTS SAS_MIGRATION;
CREATE SCHEMA IF NOT EXISTS SAS_MIGRATION.BANKING;
USE SCHEMA SAS_MIGRATION.BANKING;

-- ----------------------------------------------------------------------------
-- CUST_ACCOUNTS
-- Source: SAS dataset CUST_ACCOUNTS (sas7bdat)
-- Grain: one row per customer–account relationship
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE CUST_ACCOUNTS (
    CUSTOMER_ID     INTEGER        NOT NULL,
    ACCOUNT_ID      VARCHAR(8)     NOT NULL,
    ACCOUNT_TYPE    VARCHAR(10)    NOT NULL,
    IS_ACTIVE       VARCHAR(10)    NOT NULL,
    START_DATE      DATE           NOT NULL,
    END_DATE        DATE,

    CONSTRAINT PK_CUST_ACCOUNTS PRIMARY KEY (CUSTOMER_ID, ACCOUNT_ID),
    CONSTRAINT CK_ACCOUNT_TYPE  CHECK (ACCOUNT_TYPE IN ('CHECKING', 'CREDIT', 'SAVINGS')),
    CONSTRAINT CK_IS_ACTIVE     CHECK (IS_ACTIVE IN ('ACTIVE', 'INACTIVE'))
)
CLUSTER BY (CUSTOMER_ID)
COMMENT = 'Customer-to-account mapping migrated from SAS CUST_ACCOUNTS dataset';

-- ----------------------------------------------------------------------------
-- DAILY_BALANCE
-- Source: SAS dataset DAILY_BALANCE (sas7bdat)
-- Grain: one row per account per calendar day
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE DAILY_BALANCE (
    CUSTOMER_ID         INTEGER        NOT NULL,
    ACCOUNT_ID          VARCHAR(8)     NOT NULL,
    BALANCE_DATE        DATE           NOT NULL,
    END_OF_DAY_BALANCE  NUMBER(12,2)   NOT NULL,
    BALANCE_MONTH       VARCHAR(7)     NOT NULL,

    CONSTRAINT PK_DAILY_BALANCE PRIMARY KEY (CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE)
)
CLUSTER BY (BALANCE_DATE, CUSTOMER_ID)
COMMENT = 'Daily end-of-day account balances migrated from SAS DAILY_BALANCE dataset';

-- ----------------------------------------------------------------------------
-- MONTHLY_AMB
-- Source: SAS dataset MONTHLY_AMB (sas7bdat)
-- Grain: one row per account per reporting month
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE MONTHLY_AMB (
    CUSTOMER_ID              INTEGER        NOT NULL,
    ACCOUNT_ID               VARCHAR(8)     NOT NULL,
    REPORTING_MONTH_YYYYMM   INTEGER        NOT NULL,
    AVERAGE_MONTHLY_BALANCE  NUMBER(12,2)   NOT NULL,
    DATE_COMPUTED            DATE           NOT NULL,

    CONSTRAINT PK_MONTHLY_AMB PRIMARY KEY (CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM)
)
CLUSTER BY (REPORTING_MONTH_YYYYMM, CUSTOMER_ID)
COMMENT = 'Monthly average balances migrated from SAS MONTHLY_AMB dataset';

-- ============================================================================
-- Staging tables for COPY INTO ingestion (CSV intermediate files)
-- These receive raw CSV data before any transformations.
-- ============================================================================

CREATE OR REPLACE TABLE STG_CUST_ACCOUNTS (
    CUSTOMER_ID     VARCHAR,
    ACCOUNT_ID      VARCHAR,
    ACCOUNT_TYPE    VARCHAR,
    IS_ACTIVE       VARCHAR,
    START_DATE      VARCHAR,
    END_DATE        VARCHAR
)
COMMENT = 'Staging table for raw CUST_ACCOUNTS CSV ingestion';

CREATE OR REPLACE TABLE STG_DAILY_BALANCE (
    CUSTOMER_ID         VARCHAR,
    ACCOUNT_ID          VARCHAR,
    BALANCE_DATE        VARCHAR,
    END_OF_DAY_BALANCE  VARCHAR,
    BALANCE_MONTH       VARCHAR
)
COMMENT = 'Staging table for raw DAILY_BALANCE CSV ingestion';

CREATE OR REPLACE TABLE STG_MONTHLY_AMB (
    CUSTOMER_ID              VARCHAR,
    ACCOUNT_ID               VARCHAR,
    REPORTING_MONTH_YYYYMM   VARCHAR,
    AVERAGE_MONTHLY_BALANCE  VARCHAR,
    DATE_COMPUTED            VARCHAR
)
COMMENT = 'Staging table for raw MONTHLY_AMB CSV ingestion';

-- ============================================================================
-- INSERT ... SELECT from staging → production (type-cast + cleansing)
-- ============================================================================

-- CUST_ACCOUNTS: staging → final
INSERT INTO CUST_ACCOUNTS (
    CUSTOMER_ID, ACCOUNT_ID, ACCOUNT_TYPE, IS_ACTIVE, START_DATE, END_DATE
)
SELECT
    TRY_CAST(CUSTOMER_ID AS INTEGER),
    TRIM(ACCOUNT_ID),
    TRIM(ACCOUNT_TYPE),
    TRIM(IS_ACTIVE),
    TRY_TO_DATE(START_DATE, 'YYYY-MM-DD'),
    TRY_TO_DATE(END_DATE, 'YYYY-MM-DD')
FROM STG_CUST_ACCOUNTS
WHERE CUSTOMER_ID IS NOT NULL AND TRIM(CUSTOMER_ID) <> '';

-- DAILY_BALANCE: staging → final
INSERT INTO DAILY_BALANCE (
    CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE, END_OF_DAY_BALANCE, BALANCE_MONTH
)
SELECT
    TRY_CAST(CUSTOMER_ID AS INTEGER),
    TRIM(ACCOUNT_ID),
    TRY_TO_DATE(BALANCE_DATE, 'YYYY-MM-DD'),
    TRY_CAST(END_OF_DAY_BALANCE AS NUMBER(12,2)),
    TRIM(BALANCE_MONTH)
FROM STG_DAILY_BALANCE
WHERE CUSTOMER_ID IS NOT NULL AND TRIM(CUSTOMER_ID) <> '';

-- MONTHLY_AMB: staging → final
INSERT INTO MONTHLY_AMB (
    CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM,
    AVERAGE_MONTHLY_BALANCE, DATE_COMPUTED
)
SELECT
    TRY_CAST(CUSTOMER_ID AS INTEGER),
    TRIM(ACCOUNT_ID),
    TRY_CAST(REPORTING_MONTH_YYYYMM AS INTEGER),
    TRY_CAST(AVERAGE_MONTHLY_BALANCE AS NUMBER(12,2)),
    TRY_TO_DATE(DATE_COMPUTED, 'YYYY-MM-DD')
FROM STG_MONTHLY_AMB
WHERE CUSTOMER_ID IS NOT NULL AND TRIM(CUSTOMER_ID) <> '';
