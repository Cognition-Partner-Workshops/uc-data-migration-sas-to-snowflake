-- =============================================================================
-- Snowflake DDL: SAS-to-Snowflake Migration Target Tables
-- Database: SAS_MIGRATION_DB
-- Schema: BANKING
-- =============================================================================

CREATE DATABASE IF NOT EXISTS SAS_MIGRATION_DB;
CREATE SCHEMA IF NOT EXISTS SAS_MIGRATION_DB.BANKING;

USE SCHEMA SAS_MIGRATION_DB.BANKING;

-- =============================================================================
-- Table: CUST_ACCOUNTS
-- Source: sample_data/CUST_ACCOUNTS.sas7bdat
-- Description: Customer account master with account lifecycle dates.
-- =============================================================================
CREATE OR REPLACE TABLE CUST_ACCOUNTS (
    CUSTOMER_ID     INTEGER        NOT NULL,
    ACCOUNT_ID      VARCHAR(8)     NOT NULL,
    ACCOUNT_TYPE    VARCHAR(16)    NOT NULL,
    IS_ACTIVE       VARCHAR(8)     NOT NULL,
    START_DATE      DATE           NOT NULL,
    END_DATE        DATE,

    CONSTRAINT PK_CUST_ACCOUNTS PRIMARY KEY (CUSTOMER_ID, ACCOUNT_ID),
    CONSTRAINT CHK_ACCOUNT_TYPE CHECK (ACCOUNT_TYPE IN ('CHECKING', 'CREDIT', 'SAVINGS')),
    CONSTRAINT CHK_IS_ACTIVE CHECK (IS_ACTIVE IN ('ACTIVE', 'INACTIVE')),
    CONSTRAINT CHK_DATE_RANGE CHECK (END_DATE IS NULL OR END_DATE >= START_DATE)
)
CLUSTER BY (CUSTOMER_ID)
COMMENT = 'Customer account master migrated from SAS CUST_ACCOUNTS dataset';

-- =============================================================================
-- Table: DAILY_BALANCE
-- Source: sample_data/DAILY_BALANCE.sas7bdat
-- Description: End-of-day balance snapshots per account per day.
-- =============================================================================
CREATE OR REPLACE TABLE DAILY_BALANCE (
    CUSTOMER_ID         INTEGER        NOT NULL,
    ACCOUNT_ID          VARCHAR(8)     NOT NULL,
    BALANCE_DATE        DATE           NOT NULL,
    END_OF_DAY_BALANCE  NUMBER(12,2)   NOT NULL,
    BALANCE_MONTH       VARCHAR(7)     NOT NULL,

    CONSTRAINT PK_DAILY_BALANCE PRIMARY KEY (CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE),
    CONSTRAINT FK_DAILY_BAL_ACCT FOREIGN KEY (CUSTOMER_ID, ACCOUNT_ID)
        REFERENCES CUST_ACCOUNTS (CUSTOMER_ID, ACCOUNT_ID)
)
CLUSTER BY (BALANCE_MONTH, CUSTOMER_ID)
COMMENT = 'Daily end-of-day balance snapshots migrated from SAS DAILY_BALANCE dataset';

-- =============================================================================
-- Table: MONTHLY_AMB
-- Source: sample_data/MONTHLY_AMB.sas7bdat
-- Description: Average monthly balance aggregation per account.
-- =============================================================================
CREATE OR REPLACE TABLE MONTHLY_AMB (
    CUSTOMER_ID              INTEGER        NOT NULL,
    ACCOUNT_ID               VARCHAR(8)     NOT NULL,
    REPORTING_MONTH_YYYYMM   INTEGER        NOT NULL,
    AVERAGE_MONTHLY_BALANCE  NUMBER(12,2)   NOT NULL,
    DATE_COMPUTED            DATE           NOT NULL,

    CONSTRAINT PK_MONTHLY_AMB PRIMARY KEY (CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM),
    CONSTRAINT FK_MONTHLY_AMB_ACCT FOREIGN KEY (CUSTOMER_ID, ACCOUNT_ID)
        REFERENCES CUST_ACCOUNTS (CUSTOMER_ID, ACCOUNT_ID),
    CONSTRAINT CHK_REPORTING_MONTH CHECK (
        REPORTING_MONTH_YYYYMM BETWEEN 190001 AND 209912
    )
)
CLUSTER BY (REPORTING_MONTH_YYYYMM, CUSTOMER_ID)
COMMENT = 'Monthly average balance aggregation migrated from SAS MONTHLY_AMB dataset';

-- =============================================================================
-- Staging tables for COPY INTO bulk loads (no constraints, all VARCHAR)
-- =============================================================================

CREATE OR REPLACE TABLE STG_CUST_ACCOUNTS (
    CUSTOMER_ID     VARCHAR(20),
    ACCOUNT_ID      VARCHAR(8),
    ACCOUNT_TYPE    VARCHAR(16),
    IS_ACTIVE       VARCHAR(8),
    START_DATE      VARCHAR(20),
    END_DATE        VARCHAR(20)
)
COMMENT = 'Staging table for CUST_ACCOUNTS CSV bulk load';

CREATE OR REPLACE TABLE STG_DAILY_BALANCE (
    CUSTOMER_ID         VARCHAR(20),
    ACCOUNT_ID          VARCHAR(8),
    BALANCE_DATE        VARCHAR(20),
    END_OF_DAY_BALANCE  VARCHAR(20),
    BALANCE_MONTH       VARCHAR(10)
)
COMMENT = 'Staging table for DAILY_BALANCE CSV bulk load';

CREATE OR REPLACE TABLE STG_MONTHLY_AMB (
    CUSTOMER_ID              VARCHAR(20),
    ACCOUNT_ID               VARCHAR(8),
    REPORTING_MONTH_YYYYMM   VARCHAR(10),
    AVERAGE_MONTHLY_BALANCE  VARCHAR(20),
    DATE_COMPUTED            VARCHAR(20)
)
COMMENT = 'Staging table for MONTHLY_AMB CSV bulk load';

-- =============================================================================
-- File format for CSV loading
-- =============================================================================

CREATE OR REPLACE FILE FORMAT SAS_MIGRATION_CSV_FORMAT
    TYPE = 'CSV'
    FIELD_DELIMITER = ','
    SKIP_HEADER = 1
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    NULL_IF = ('', 'NA', 'NULL', 'NaN')
    EMPTY_FIELD_AS_NULL = TRUE
    TRIM_SPACE = TRUE
    ERROR_ON_COLUMN_COUNT_MISMATCH = TRUE;

-- =============================================================================
-- Internal stage for migration files
-- =============================================================================

CREATE OR REPLACE STAGE SAS_MIGRATION_STAGE
    FILE_FORMAT = SAS_MIGRATION_CSV_FORMAT
    COMMENT = 'Internal stage for SAS migration CSV files';
