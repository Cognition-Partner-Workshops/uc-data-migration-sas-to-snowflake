# SAS-to-Snowflake Migration Guide

Schema mapping, data type conversions, and loading strategy for migrating SAS banking datasets to Snowflake.

---

## 1. Source Datasets

| Dataset | SAS File | Rows | Description |
|---|---|---|---|
| **CUST_ACCOUNTS** | `CUST_ACCOUNTS.sas7bdat` | 1,980 | Customer-to-account relationships |
| **DAILY_BALANCE** | `DAILY_BALANCE.sas7bdat` | 122,760 | Daily end-of-day balances per account |
| **MONTHLY_AMB** | `MONTHLY_AMB.sas7bdat` | 1,980 | Monthly average balance per account |

### Migration Scenarios

| Scenario | Location | Key Differences |
|---|---|---|
| **Baseline** | `sample_data/` | Full dataset with consistent `YYYY-MM-DD` date formats |
| **Scenario 1** | `sample_data/Scenario1/` | MONTHLY_AMB has 1,709 rows (271 dropped); `date_computed` uses `DD-MM-YYYY` format |
| **Scenario 2** | `sample_data/Scenario2/` | DAILY_BALANCE reduced to 16,802 rows (delta subset); dates in `DD-MM-YYYY` format |

Scenario 1 represents a baseline migration where some accounts are filtered out during ETL. Scenario 2 simulates a delta/incremental load with a subset of daily balances and non-standard date formats requiring conversion.

---

## 2. Schema Mapping

### CUST_ACCOUNTS

| SAS Column | SAS Format | Snowflake Column | Snowflake Type | Notes |
|---|---|---|---|---|
| `customer_id` | BEST12 (numeric) | `CUSTOMER_ID` | `INTEGER NOT NULL` | Downcast from float64 → int |
| `account_id` | $8 (char) | `ACCOUNT_ID` | `VARCHAR(8) NOT NULL` | 8-char hex identifier |
| `account_type` | $8 (char) | `ACCOUNT_TYPE` | `VARCHAR(10) NOT NULL` | Constrained: CHECKING, CREDIT, SAVINGS |
| `is_active` | $8 (char) | `IS_ACTIVE` | `VARCHAR(10) NOT NULL` | Constrained: ACTIVE, INACTIVE |
| `start_date` | YYMMDD10 (date) | `START_DATE` | `DATE NOT NULL` | SAS date → YYYY-MM-DD |
| `end_date` | YYMMDD10 (date) | `END_DATE` | `DATE` | Nullable; 1,709 of 1,980 rows are NULL |

**Primary Key:** `(CUSTOMER_ID, ACCOUNT_ID)`
**Clustering Key:** `CUSTOMER_ID`

### DAILY_BALANCE

| SAS Column | SAS Format | Snowflake Column | Snowflake Type | Notes |
|---|---|---|---|---|
| `customer_id` | BEST12 (numeric) | `CUSTOMER_ID` | `INTEGER NOT NULL` | Downcast from float64 → int |
| `account_id` | $8 (char) | `ACCOUNT_ID` | `VARCHAR(8) NOT NULL` | 8-char hex identifier |
| `date` | YYMMDD10 (date) | `BALANCE_DATE` | `DATE NOT NULL` | Renamed to avoid reserved word |
| `end_of_day_balance` | BEST12 (numeric) | `END_OF_DAY_BALANCE` | `NUMBER(12,2) NOT NULL` | Range: 0.00 – 9,856.79 |
| `month` | DATE7 (date) | `BALANCE_MONTH` | `VARCHAR(7) NOT NULL` | Stored as YYYY-MM string |

**Primary Key:** `(CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE)`
**Clustering Key:** `(BALANCE_DATE, CUSTOMER_ID)` — optimized for date-range queries

### MONTHLY_AMB

| SAS Column | SAS Format | Snowflake Column | Snowflake Type | Notes |
|---|---|---|---|---|
| `customer_id` | BEST12 (numeric) | `CUSTOMER_ID` | `INTEGER NOT NULL` | Downcast from float64 → int |
| `account_id` | $8 (char) | `ACCOUNT_ID` | `VARCHAR(8) NOT NULL` | 8-char hex identifier |
| `reporting_month_yyyymm` | BEST12 (numeric) | `REPORTING_MONTH_YYYYMM` | `INTEGER NOT NULL` | Period key, e.g. 202507 |
| `average_monthly_balance` | BEST12 (numeric) | `AVERAGE_MONTHLY_BALANCE` | `NUMBER(12,2) NOT NULL` | Range: 136.96 – 7,675.76 |
| `date_computed` | YYMMDD10 (date) | `DATE_COMPUTED` | `DATE NOT NULL` | Computation timestamp |

**Primary Key:** `(CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM)`
**Clustering Key:** `(REPORTING_MONTH_YYYYMM, CUSTOMER_ID)`

---

## 3. Data Type Conversion Rules

| SAS Type | SAS Format | Pandas Intermediate | Snowflake Target | Conversion Logic |
|---|---|---|---|---|
| Numeric (float64) | BEST12 | `Int64` (nullable) | `INTEGER` | Drop fractional `.0`; cast via `Int64` |
| Numeric (float64) | BEST12 | `float64` | `NUMBER(12,2)` | Preserve 2-decimal precision |
| Character ($8) | $8 | `str` | `VARCHAR(8–10)` | Decode bytes → UTF-8; strip trailing spaces |
| Numeric date | YYMMDD10 | `datetime64[ns]` | `DATE` | pyreadstat auto-converts; format as `YYYY-MM-DD` |
| Numeric date | DATE7 | `datetime64[ns]` | `VARCHAR(7)` | Extract `YYYY-MM` string for month column |

### Scenario-Specific Date Handling

Scenario 1 and 2 CSVs use `DD-MM-YYYY` format for some date columns instead of `YYYY-MM-DD`. The Python reader normalizes all dates to ISO 8601 (`YYYY-MM-DD`) before export. In Snowflake, `TRY_TO_DATE()` with explicit format strings handles both patterns safely.

---

## 4. Loading Strategy

### Architecture

```
SAS7BDAT files
    │
    ▼  (1) sas_reader.py — pyreadstat + type coercion
CSV files (YYYY-MM-DD dates, UTF-8, no BOM)
    │
    ▼  (2) PUT → internal stage @SAS_CSV_STAGE
Snowflake stage
    │
    ▼  (3) COPY INTO → STG_* tables (all VARCHAR)
Staging tables
    │
    ▼  (4) INSERT … SELECT with TRY_CAST → final tables
Production tables
    │
    ▼  (5) validation_queries.py — reconciliation checks
Validation report
```

### Step-by-Step

#### Step 1: Extract from SAS

```bash
python -m migration.sas_reader sample_data/ -o migration/output
```

Reads `.sas7bdat` files, applies type conversions (integer downcasting, date normalization, byte decoding), and exports clean CSVs.

#### Step 2: Create Snowflake objects

Run `migration/snowflake_ddl.sql` in a Snowflake worksheet to create the database, schema, staging tables, and production tables.

#### Step 3: Generate and run load script

```bash
python -m migration.snowflake_loader migration/output/ -o migration/output/snowflake_load.sql
```

The generated SQL:
1. Creates a `FILE FORMAT` (CSV, skip header, UTF-8)
2. Creates an internal `STAGE`
3. `PUT` files from local disk to the stage
4. `COPY INTO` staging tables (all VARCHAR for safe landing)
5. `INSERT … SELECT` with `TRY_CAST` into typed production tables

#### Step 4: Validate

```bash
# Generate Snowflake-side validation SQL
python -m migration.validation_queries sql -o migration/output/validation.sql

# Or run local source-vs-target comparison
python -m migration.validation_queries local sample_data/ migration/output/
```

---

## 5. Validation Checks

| Check | Method | Source Side | Snowflake Side |
|---|---|---|---|
| **Row count** | `COUNT(*)` | `len(df)` from pyreadstat | `SELECT COUNT(*) FROM table` |
| **Column sums** | `SUM(col)` | `df[col].sum()` | `SELECT SUM(col) FROM table` |
| **Distinct counts** | `COUNT(DISTINCT col)` | `df[col].nunique()` | `SELECT COUNT(DISTINCT col) FROM table` |
| **NULL distribution** | `SUM(CASE WHEN NULL)` | `df[col].isna().sum()` | `SUM(CASE WHEN col IS NULL THEN 1 ELSE 0 END)` |
| **Row hash** | MD5 of pipe-delimited row | `hashlib.md5(row)` | `MD5(col1 \|\| '\|' \|\| col2 …)` |
| **Full table hash** | MD5 of CSV export | `hashlib.md5(csv_bytes)` | Compare against source checksum |

### Expected Baseline Counts

| Table | Rows | Distinct Customers | Distinct Accounts | SUM (numeric) |
|---|---|---|---|---|
| CUST_ACCOUNTS | 1,980 | 1,000 | 1,980 | — |
| DAILY_BALANCE | 122,760 | 1,000 | 1,980 | ~300M (EOD balance) |
| MONTHLY_AMB | 1,980 | 1,000 | 1,980 | ~5.8M (AMB) |

---

## 6. File Inventory

```
migration/
├── __init__.py
├── snowflake_ddl.sql           # CREATE TABLE + staging DDL
├── sas_reader.py               # SAS7BDAT → DataFrame → CSV
├── snowflake_loader.py         # COPY INTO statement generator
└── validation_queries.py       # Reconciliation SQL + local checks
```

---

## 7. Prerequisites

- Python 3.10+
- `pyreadstat` (SAS7BDAT reader backed by ReadStat C library)
- `pandas`
- Snowflake account with `ACCOUNTADMIN` or equivalent for DDL execution
- SnowSQL or Snowflake web UI for running generated SQL

Install Python dependencies:

```bash
pip install pyreadstat pandas
```
