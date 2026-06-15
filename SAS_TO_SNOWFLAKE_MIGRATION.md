# SAS-to-Snowflake Migration Guide

## Overview

This document describes the schema mapping, data type conversions, and loading strategy for migrating banking datasets from SAS (`.sas7bdat`) format to Snowflake.

## Source Datasets

| Dataset | SAS File | Rows | Description |
|---------|----------|------|-------------|
| CUST_ACCOUNTS | `sample_data/CUST_ACCOUNTS.sas7bdat` | 1,980 | Customer account master with lifecycle dates |
| DAILY_BALANCE | `sample_data/DAILY_BALANCE.sas7bdat` | 122,760 | End-of-day balance snapshots per account |
| MONTHLY_AMB | `sample_data/MONTHLY_AMB.sas7bdat` | 1,980 | Average monthly balance aggregation |

## Migration Scenarios

### Scenario 1 — Baseline Migration

Full data migration with identical row counts to the source. Minor date format variation in `MONTHLY_AMB.date_computed` (DD-MM-YYYY vs YYYY-MM-DD in source).

- `CUST_ACCOUNTS`: 1,980 rows (identical to source)
- `DAILY_BALANCE`: 122,760 rows (identical to source)
- `MONTHLY_AMB`: 1,709 rows (subset — missing some account months)

### Scenario 2 — Delta/Incremental Migration

Partial data load simulating an incremental migration with date format variations requiring conversion handling.

- `CUST_ACCOUNTS`: 1,980 rows (identical to source)
- `DAILY_BALANCE`: 16,802 rows (subset, DD-MM-YYYY date format)
- `MONTHLY_AMB`: 1,709 rows (subset, DD-MM-YYYY date format)

---

## Schema Mapping

### CUST_ACCOUNTS

| SAS Column | SAS Format | SAS Storage | Snowflake Column | Snowflake Type | Constraints |
|---|---|---|---|---|---|
| customer_id | BEST12 | 8 bytes (double) | CUSTOMER_ID | INTEGER | NOT NULL, PK |
| account_id | $8 | 8 bytes (char) | ACCOUNT_ID | VARCHAR(8) | NOT NULL, PK |
| account_type | $8 | 8 bytes (char) | ACCOUNT_TYPE | VARCHAR(16) | NOT NULL, CHECK IN ('CHECKING','CREDIT','SAVINGS') |
| is_active | $8 | 8 bytes (char) | IS_ACTIVE | VARCHAR(8) | NOT NULL, CHECK IN ('ACTIVE','INACTIVE') |
| start_date | YYMMDD10 | 8 bytes (double) | START_DATE | DATE | NOT NULL |
| end_date | YYMMDD10 | 8 bytes (double) | END_DATE | DATE | Nullable (NULL for active accounts) |

**Primary Key:** `(CUSTOMER_ID, ACCOUNT_ID)`
**Clustering Key:** `CUSTOMER_ID`

### DAILY_BALANCE

| SAS Column | SAS Format | SAS Storage | Snowflake Column | Snowflake Type | Constraints |
|---|---|---|---|---|---|
| customer_id | BEST12 | 8 bytes (double) | CUSTOMER_ID | INTEGER | NOT NULL, PK, FK |
| account_id | $8 | 8 bytes (char) | ACCOUNT_ID | VARCHAR(8) | NOT NULL, PK, FK |
| date | YYMMDD10 | 8 bytes (double) | BALANCE_DATE | DATE | NOT NULL, PK |
| end_of_day_balance | BEST12 | 8 bytes (double) | END_OF_DAY_BALANCE | NUMBER(12,2) | NOT NULL |
| month | DATE7 | 8 bytes (double) | BALANCE_MONTH | VARCHAR(7) | NOT NULL |

**Primary Key:** `(CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE)`
**Foreign Key:** `(CUSTOMER_ID, ACCOUNT_ID)` → `CUST_ACCOUNTS`
**Clustering Key:** `(BALANCE_MONTH, CUSTOMER_ID)`

### MONTHLY_AMB

| SAS Column | SAS Format | SAS Storage | Snowflake Column | Snowflake Type | Constraints |
|---|---|---|---|---|---|
| customer_id | BEST12 | 8 bytes (double) | CUSTOMER_ID | INTEGER | NOT NULL, PK, FK |
| account_id | $8 | 8 bytes (char) | ACCOUNT_ID | VARCHAR(8) | NOT NULL, PK, FK |
| reporting_month_yyyymm | BEST12 | 8 bytes (double) | REPORTING_MONTH_YYYYMM | INTEGER | NOT NULL, PK, CHECK 190001–209912 |
| average_monthly_balance | BEST12 | 8 bytes (double) | AVERAGE_MONTHLY_BALANCE | NUMBER(12,2) | NOT NULL |
| date_computed | YYMMDD10 | 8 bytes (double) | DATE_COMPUTED | DATE | NOT NULL |

**Primary Key:** `(CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM)`
**Foreign Key:** `(CUSTOMER_ID, ACCOUNT_ID)` → `CUST_ACCOUNTS`
**Clustering Key:** `(REPORTING_MONTH_YYYYMM, CUSTOMER_ID)`

---

## Data Type Conversion Rules

| SAS Type | SAS Format | Python Conversion | Snowflake Type |
|----------|-----------|-------------------|----------------|
| Numeric (double) | BEST12 | `float → int` (via `Int64`) | INTEGER |
| Numeric (double) | BEST12 | `float → round(2)` | NUMBER(12,2) |
| Numeric (double) | YYMMDD10 | SAS date → `pd.to_datetime` → ISO string | DATE |
| Numeric (double) | DATE7 | SAS date → `strftime('%Y-%m')` | VARCHAR(7) |
| Character ($8) | — | `.str.strip()` | VARCHAR(8/16) |

### Key Conversion Notes

1. **SAS Dates**: Stored as days since 1960-01-01 internally. `pyreadstat` often auto-converts these to Python datetime or ISO strings. The script handles both representations.

2. **Numeric IDs**: SAS stores all numbers as 8-byte doubles. `customer_id` (e.g., `1001.0`) must be cast to integer for Snowflake.

3. **String Encoding**: SAS files use session encoding (typically LATIN1 or UTF-8). `pyreadstat` handles decoding. Trailing spaces in fixed-width character fields are stripped.

4. **Date Format Variations**: Scenario 2 uses DD-MM-YYYY format in some date columns. The Python pipeline normalizes all dates to YYYY-MM-DD (ISO 8601) before export.

5. **NULL Handling**: SAS missing values (`.`) map to Python `NaN`/`None`, exported as empty strings in CSV, loaded as NULL in Snowflake via `NULL_IF = ('')`.

---

## Loading Strategy

### Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│  SAS7BDAT   │────▶│ Python ETL   │────▶│  CSV Files  │────▶│  Snowflake   │
│  (source)   │     │ (transform)  │     │  (staging)  │     │  (target)    │
└─────────────┘     └──────────────┘     └─────────────┘     └──────────────┘
```

### Step-by-Step Process

#### 1. Extract & Transform (Python)

```bash
# Process baseline (main sample_data/ files)
python migration/scripts/sas_to_snowflake.py --scenario baseline

# Process scenario 1
python migration/scripts/sas_to_snowflake.py \
    --source-dir sample_data/Scenario1 --scenario scenario1

# Process scenario 2
python migration/scripts/sas_to_snowflake.py \
    --source-dir sample_data/Scenario2 --scenario scenario2
```

Output: cleaned CSV files in `migration/output/<scenario>/`

#### 2. Stage Files in Snowflake

```sql
-- Upload CSVs to internal stage
PUT file://migration/output/baseline/CUST_ACCOUNTS.csv @SAS_MIGRATION_STAGE;
PUT file://migration/output/baseline/DAILY_BALANCE.csv @SAS_MIGRATION_STAGE;
PUT file://migration/output/baseline/MONTHLY_AMB.csv @SAS_MIGRATION_STAGE;
```

#### 3. Bulk Load via COPY INTO

```sql
-- Load in dependency order: master table first, then detail tables
\i migration/output/baseline/load_CUST_ACCOUNTS.sql
\i migration/output/baseline/load_DAILY_BALANCE.sql
\i migration/output/baseline/load_MONTHLY_AMB.sql
```

The load scripts use a staging-table pattern:
1. `COPY INTO STG_<table>` — raw string load from CSV
2. `INSERT INTO <table> SELECT TRY_CAST(...)` — type-safe promotion
3. `TRUNCATE TABLE STG_<table>` — cleanup

#### 4. Validate

```bash
# Generate validation queries with source statistics baked in
python migration/validation/validate_migration.py

# Run local SAS-vs-CSV parity checks
python migration/validation/validate_migration.py --source-dir sample_data
```

Output: `migration/validation/validation_queries.sql` — run in Snowflake to verify.

---

## Validation Strategy

### Level 1: Row Count Parity

Compare `number_rows` from SAS metadata against `COUNT(*)` in Snowflake.

### Level 2: Column Checksums

For each numeric column, compare `SUM()` and `COUNT(DISTINCT)` between source and target.

### Level 3: Null Distributions

Verify null counts per column match between source and target.

### Level 4: Hash-based Record Comparison

MD5 hash of concatenated row values for sample record spot-checks.

### Level 5: Full Reconciliation Report

Single query returning pass/fail for all tables across row counts and column sums.

---

## File Structure

```
migration/
├── ddl/
│   └── snowflake_tables.sql       # CREATE TABLE + staging + file format DDL
├── scripts/
│   └── sas_to_snowflake.py        # Main ETL: read SAS → transform → export CSV
├── validation/
│   ├── validate_migration.py      # Validation query generator + local checks
│   └── validation_queries.sql     # Generated Snowflake validation queries
└── output/                        # Generated at runtime
    ├── baseline/
    ├── scenario1/
    └── scenario2/
```

---

## Dependencies

```
pandas>=1.5.0
pyreadstat>=1.2.0
```

Install:
```bash
pip install pandas pyreadstat
```

---

## Assumptions & Limitations

1. **No Snowflake connection required** — scripts generate SQL and CSV artifacts for manual or automated deployment.
2. **Date handling** assumes source SAS files use YYMMDD10 or DATE7 formats. Custom SAS date formats would require additional conversion logic.
3. **Character encoding** assumes UTF-8 compatible source data. EBCDIC or other mainframe encodings would need pre-processing.
4. **Incremental loads** (Scenario 2) assume MERGE/UPSERT logic is handled separately; the generated `COPY INTO` performs full inserts.
5. **Clustering keys** are chosen based on common query patterns (filter by customer, partition by month). Production tuning should be based on actual workload analysis.
