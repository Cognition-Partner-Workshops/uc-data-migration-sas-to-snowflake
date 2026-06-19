-- ============================================================================
-- Snowflake COPY INTO statements and post-load validation queries
--
-- Prerequisites
-- -------------
-- 1. Tables created via create_tables.sql
-- 2. CSV files uploaded to a Snowflake stage (internal or external)
--    Example:  PUT file://./sample_data/CUST_ACCOUNTS.csv @SAS_MIGRATION.SAS_STAGE;
-- ============================================================================

USE SCHEMA SAS_MIGRATION;

-- ============================================================================
-- Stage definition (adjust URL/credentials for your environment)
-- ============================================================================

CREATE STAGE IF NOT EXISTS SAS_MIGRATION.SAS_STAGE
    FILE_FORMAT = (
        TYPE            = 'CSV'
        FIELD_DELIMITER = ','
        SKIP_HEADER     = 1
        FIELD_OPTIONALLY_ENCLOSED_BY = '"'
        NULL_IF         = ('', 'NA', '.')            -- SAS missing values
        DATE_FORMAT     = 'YYYY-MM-DD'
        TIMESTAMP_FORMAT = 'YYYY-MM-DD HH24:MI:SS'
        TRIM_SPACE      = TRUE
        ERROR_ON_COLUMN_COUNT_MISMATCH = TRUE
    )
    COMMENT = 'Stage for SAS migration CSV files';


-- ============================================================================
-- COPY INTO  –  Bulk load from staged CSV files
-- ============================================================================

-- 1. CUST_ACCOUNTS
COPY INTO SAS_MIGRATION.CUST_ACCOUNTS (
    CUSTOMER_ID,
    ACCOUNT_ID,
    ACCOUNT_TYPE,
    IS_ACTIVE,
    START_DATE,
    END_DATE
)
FROM @SAS_MIGRATION.SAS_STAGE/CUST_ACCOUNTS.csv
FILE_FORMAT = (FORMAT_NAME = 'SAS_MIGRATION.SAS_STAGE')
ON_ERROR    = 'ABORT_STATEMENT'
PURGE       = FALSE;

-- 2. DAILY_BALANCE
-- Note: the CSV header "date" is mapped to BALANCE_DATE in the table
COPY INTO SAS_MIGRATION.DAILY_BALANCE (
    CUSTOMER_ID,
    ACCOUNT_ID,
    BALANCE_DATE,
    END_OF_DAY_BALANCE,
    MONTH
)
FROM (
    SELECT
        $1,                                    -- customer_id
        $2,                                    -- account_id
        TO_DATE($3, 'YYYY-MM-DD'),             -- date → BALANCE_DATE
        $4::DECIMAL(12,2),                     -- end_of_day_balance
        $5                                     -- month
    FROM @SAS_MIGRATION.SAS_STAGE/DAILY_BALANCE.csv
)
FILE_FORMAT = (
    TYPE            = 'CSV'
    FIELD_DELIMITER = ','
    SKIP_HEADER     = 1
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    NULL_IF         = ('', 'NA', '.')
    TRIM_SPACE      = TRUE
)
ON_ERROR = 'ABORT_STATEMENT'
PURGE    = FALSE;

-- 3. MONTHLY_AMB
COPY INTO SAS_MIGRATION.MONTHLY_AMB (
    CUSTOMER_ID,
    ACCOUNT_ID,
    REPORTING_MONTH_YYYYMM,
    AVERAGE_MONTHLY_BALANCE,
    DATE_COMPUTED
)
FROM (
    SELECT
        $1,                                    -- customer_id
        $2,                                    -- account_id
        $3::INTEGER,                           -- reporting_month_yyyymm
        $4::DECIMAL(12,2),                     -- average_monthly_balance
        TO_DATE($5, 'YYYY-MM-DD')              -- date_computed
    FROM @SAS_MIGRATION.SAS_STAGE/MONTHLY_AMB.csv
)
FILE_FORMAT = (
    TYPE            = 'CSV'
    FIELD_DELIMITER = ','
    SKIP_HEADER     = 1
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    NULL_IF         = ('', 'NA', '.')
    TRIM_SPACE      = TRUE
)
ON_ERROR = 'ABORT_STATEMENT'
PURGE    = FALSE;


-- ============================================================================
-- Validation queries  –  Row counts
-- ============================================================================

-- Expected row counts (from source CSV wc -l minus header):
--   CUST_ACCOUNTS : 1 980
--   DAILY_BALANCE : 122 760
--   MONTHLY_AMB   : 1 980

SELECT 'CUST_ACCOUNTS' AS TABLE_NAME, COUNT(*) AS ROW_COUNT,
       CASE WHEN COUNT(*) = 1980  THEN 'PASS' ELSE 'FAIL' END AS STATUS
FROM SAS_MIGRATION.CUST_ACCOUNTS
UNION ALL
SELECT 'DAILY_BALANCE', COUNT(*),
       CASE WHEN COUNT(*) = 122760 THEN 'PASS' ELSE 'FAIL' END
FROM SAS_MIGRATION.DAILY_BALANCE
UNION ALL
SELECT 'MONTHLY_AMB', COUNT(*),
       CASE WHEN COUNT(*) = 1980  THEN 'PASS' ELSE 'FAIL' END
FROM SAS_MIGRATION.MONTHLY_AMB;


-- ============================================================================
-- Validation queries  –  Checksums / aggregates
-- ============================================================================

-- Checksum: sum of CUSTOMER_ID (deterministic, works for integer PKs)
SELECT 'CUST_ACCOUNTS' AS TABLE_NAME,
       SUM(CUSTOMER_ID) AS SUM_CUSTOMER_ID,
       COUNT(DISTINCT CUSTOMER_ID) AS DISTINCT_CUSTOMERS,
       COUNT(DISTINCT ACCOUNT_ID)  AS DISTINCT_ACCOUNTS
FROM SAS_MIGRATION.CUST_ACCOUNTS;

SELECT 'DAILY_BALANCE' AS TABLE_NAME,
       SUM(CUSTOMER_ID)            AS SUM_CUSTOMER_ID,
       ROUND(SUM(END_OF_DAY_BALANCE), 2) AS SUM_BALANCE,
       MIN(BALANCE_DATE)           AS MIN_DATE,
       MAX(BALANCE_DATE)           AS MAX_DATE,
       COUNT(DISTINCT MONTH)       AS DISTINCT_MONTHS
FROM SAS_MIGRATION.DAILY_BALANCE;

SELECT 'MONTHLY_AMB' AS TABLE_NAME,
       SUM(CUSTOMER_ID)                         AS SUM_CUSTOMER_ID,
       ROUND(SUM(AVERAGE_MONTHLY_BALANCE), 2)   AS SUM_AMB,
       COUNT(DISTINCT REPORTING_MONTH_YYYYMM)   AS DISTINCT_MONTHS,
       MIN(DATE_COMPUTED)                        AS MIN_COMPUTED,
       MAX(DATE_COMPUTED)                        AS MAX_COMPUTED
FROM SAS_MIGRATION.MONTHLY_AMB;


-- ============================================================================
-- Validation queries  –  Referential integrity
-- ============================================================================

-- Orphan daily balances (should return 0 rows)
SELECT db.CUSTOMER_ID, db.ACCOUNT_ID, COUNT(*) AS ORPHAN_COUNT
FROM SAS_MIGRATION.DAILY_BALANCE db
LEFT JOIN SAS_MIGRATION.CUST_ACCOUNTS ca
    ON db.CUSTOMER_ID = ca.CUSTOMER_ID
   AND db.ACCOUNT_ID  = ca.ACCOUNT_ID
WHERE ca.CUSTOMER_ID IS NULL
GROUP BY db.CUSTOMER_ID, db.ACCOUNT_ID;

-- Orphan monthly AMB records (should return 0 rows)
SELECT ma.CUSTOMER_ID, ma.ACCOUNT_ID, COUNT(*) AS ORPHAN_COUNT
FROM SAS_MIGRATION.MONTHLY_AMB ma
LEFT JOIN SAS_MIGRATION.CUST_ACCOUNTS ca
    ON ma.CUSTOMER_ID = ca.CUSTOMER_ID
   AND ma.ACCOUNT_ID  = ca.ACCOUNT_ID
WHERE ca.CUSTOMER_ID IS NULL
GROUP BY ma.CUSTOMER_ID, ma.ACCOUNT_ID;


-- ============================================================================
-- Validation queries  –  AMB vs Daily Balance cross-check
-- ============================================================================

-- Verify the monthly average balance roughly matches the mean of daily
-- balances for the same account and month.  A tolerance of 0.01 accounts
-- for rounding differences between SAS and Snowflake.
SELECT
    db.CUSTOMER_ID,
    db.ACCOUNT_ID,
    CAST(REPLACE(LEFT(db.MONTH, 7), '-', '') AS INTEGER) AS REPORTING_MONTH,
    ROUND(AVG(db.END_OF_DAY_BALANCE), 2)                 AS CALCULATED_AMB,
    ma.AVERAGE_MONTHLY_BALANCE                            AS STORED_AMB,
    ABS(ROUND(AVG(db.END_OF_DAY_BALANCE), 2) - ma.AVERAGE_MONTHLY_BALANCE) AS DIFF
FROM SAS_MIGRATION.DAILY_BALANCE db
JOIN SAS_MIGRATION.MONTHLY_AMB ma
    ON db.CUSTOMER_ID = ma.CUSTOMER_ID
   AND db.ACCOUNT_ID  = ma.ACCOUNT_ID
   AND CAST(REPLACE(LEFT(db.MONTH, 7), '-', '') AS INTEGER) = ma.REPORTING_MONTH_YYYYMM
GROUP BY db.CUSTOMER_ID, db.ACCOUNT_ID, db.MONTH,
         ma.AVERAGE_MONTHLY_BALANCE, ma.REPORTING_MONTH_YYYYMM
HAVING ABS(ROUND(AVG(db.END_OF_DAY_BALANCE), 2) - ma.AVERAGE_MONTHLY_BALANCE) > 0.01
ORDER BY DIFF DESC;


-- ============================================================================
-- Validation queries  –  Null checks
-- ============================================================================

SELECT 'CUST_ACCOUNTS_NULL_CHECK' AS CHECK_NAME,
       SUM(CASE WHEN CUSTOMER_ID IS NULL THEN 1 ELSE 0 END)  AS NULL_CUSTOMER_ID,
       SUM(CASE WHEN ACCOUNT_ID  IS NULL THEN 1 ELSE 0 END)  AS NULL_ACCOUNT_ID,
       SUM(CASE WHEN ACCOUNT_TYPE IS NULL THEN 1 ELSE 0 END) AS NULL_ACCOUNT_TYPE,
       SUM(CASE WHEN IS_ACTIVE   IS NULL THEN 1 ELSE 0 END)  AS NULL_IS_ACTIVE,
       SUM(CASE WHEN START_DATE  IS NULL THEN 1 ELSE 0 END)  AS NULL_START_DATE
FROM SAS_MIGRATION.CUST_ACCOUNTS;

SELECT 'DAILY_BALANCE_NULL_CHECK' AS CHECK_NAME,
       SUM(CASE WHEN CUSTOMER_ID        IS NULL THEN 1 ELSE 0 END) AS NULL_CUSTOMER_ID,
       SUM(CASE WHEN ACCOUNT_ID         IS NULL THEN 1 ELSE 0 END) AS NULL_ACCOUNT_ID,
       SUM(CASE WHEN BALANCE_DATE       IS NULL THEN 1 ELSE 0 END) AS NULL_BALANCE_DATE,
       SUM(CASE WHEN END_OF_DAY_BALANCE IS NULL THEN 1 ELSE 0 END) AS NULL_BALANCE,
       SUM(CASE WHEN MONTH              IS NULL THEN 1 ELSE 0 END) AS NULL_MONTH
FROM SAS_MIGRATION.DAILY_BALANCE;

SELECT 'MONTHLY_AMB_NULL_CHECK' AS CHECK_NAME,
       SUM(CASE WHEN CUSTOMER_ID              IS NULL THEN 1 ELSE 0 END) AS NULL_CUSTOMER_ID,
       SUM(CASE WHEN ACCOUNT_ID               IS NULL THEN 1 ELSE 0 END) AS NULL_ACCOUNT_ID,
       SUM(CASE WHEN REPORTING_MONTH_YYYYMM   IS NULL THEN 1 ELSE 0 END) AS NULL_MONTH,
       SUM(CASE WHEN AVERAGE_MONTHLY_BALANCE  IS NULL THEN 1 ELSE 0 END) AS NULL_AMB,
       SUM(CASE WHEN DATE_COMPUTED            IS NULL THEN 1 ELSE 0 END) AS NULL_DATE_COMPUTED
FROM SAS_MIGRATION.MONTHLY_AMB;


-- ============================================================================
-- Validation queries  –  Uniqueness
-- ============================================================================

-- All PKs should be unique; these queries return 0 rows if constraints hold.
SELECT CUSTOMER_ID, ACCOUNT_ID, COUNT(*) AS DUP_COUNT
FROM SAS_MIGRATION.CUST_ACCOUNTS
GROUP BY CUSTOMER_ID, ACCOUNT_ID
HAVING COUNT(*) > 1;

SELECT CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE, COUNT(*) AS DUP_COUNT
FROM SAS_MIGRATION.DAILY_BALANCE
GROUP BY CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE
HAVING COUNT(*) > 1;

SELECT CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM, COUNT(*) AS DUP_COUNT
FROM SAS_MIGRATION.MONTHLY_AMB
GROUP BY CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM
HAVING COUNT(*) > 1;
