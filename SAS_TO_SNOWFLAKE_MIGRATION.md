# SAS→Snowflake Migration Toolkit

## Overview

This toolkit converts the three immutable SAS sample datasets in `sample_data/`
into Snowflake-ready load artifacts:

- `migration/table_specs.py` — single source of truth for schema mappings
- `migration/sas_to_snowflake.py` — SAS decode/normalize/export CLI
- `migration/generate_validation_sql.py` — Snowflake validation SQL generator
- `snowflake_sql/ddl/` — Snowflake bootstrap and table DDL
- `build/snowflake_load/` — generated CSVs, `COPY_INTO.sql`, `VALIDATION.sql`,
  and `source_metrics.json`

## Schema Mapping

### CUST_ACCOUNTS

| SAS column | SAS type | Snowflake column | Snowflake type | Conversion notes |
|---|---|---|---|---|
| customer_id | numeric | CUSTOMER_ID | NUMBER(10,0) | Cast float-like SAS values to nullable integers |
| account_id | character | ACCOUNT_ID | VARCHAR(16) | Decode SAS bytes and strip whitespace |
| account_type | character | ACCOUNT_TYPE | VARCHAR(20) | Decode SAS bytes; domain is CHECKING/CREDIT/SAVINGS |
| is_active | character | IS_ACTIVE | VARCHAR(10) | Decode SAS bytes; domain is ACTIVE/INACTIVE |
| start_date | date | START_DATE | DATE | Write ISO `YYYY-MM-DD` |
| end_date | date | END_DATE | DATE | Blank when null/active |

### DAILY_BALANCE

| SAS column | SAS type | Snowflake column | Snowflake type | Conversion notes |
|---|---|---|---|---|
| customer_id | numeric | CUSTOMER_ID | NUMBER(10,0) | Cast float-like SAS values to nullable integers |
| account_id | character | ACCOUNT_ID | VARCHAR(16) | Decode SAS bytes and strip whitespace |
| date | date | BALANCE_DATE | DATE | Renamed to avoid reserved-word ambiguity |
| end_of_day_balance | numeric | END_OF_DAY_BALANCE | NUMBER(12,2) | Rounded to 2 decimals |
| month | date | MONTH | DATE | Normalized to the first day of the month (`YYYY-MM-01`) |

### MONTHLY_AMB

| SAS column | SAS type | Snowflake column | Snowflake type | Conversion notes |
|---|---|---|---|---|
| customer_id | numeric | CUSTOMER_ID | NUMBER(10,0) | Cast float-like SAS values to nullable integers |
| account_id | character | ACCOUNT_ID | VARCHAR(16) | Decode SAS bytes and strip whitespace |
| reporting_month_yyyymm | numeric | REPORTING_MONTH_YYYYMM | NUMBER(6,0) | Cast to nullable integers |
| average_monthly_balance | numeric | AVERAGE_MONTHLY_BALANCE | NUMBER(12,2) | Rounded to 2 decimals |
| date_computed | date | DATE_COMPUTED | DATE | Write ISO `YYYY-MM-DD` |

## Constraints and Clustering

- `CUST_ACCOUNTS`
  - Primary key: `ACCOUNT_ID`
  - Additional unique candidate: `(CUSTOMER_ID, ACCOUNT_ID)`
  - Cluster by `CUSTOMER_ID`
- `DAILY_BALANCE`
  - Primary key: `(CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE)`
  - Cluster by `(MONTH, CUSTOMER_ID)`
- `MONTHLY_AMB`
  - Primary key: `(CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM)`
  - Cluster by `REPORTING_MONTH_YYYYMM`

Snowflake treats the primary/unique constraints as informational metadata, so
the migration scripts also emit source-side validation metrics.

## End-to-End Loading Strategy

1. Run the DDL in `snowflake_sql/ddl/` to create the database, schemas, file
   format, stage, and tables.
2. Generate the load-ready CSVs and source metrics:

   ```bash
   python3 migration/sas_to_snowflake.py --all
   ```

3. Upload and load the files with the generated commands:

   ```bash
   cat build/snowflake_load/COPY_INTO.sql
   ```

4. Generate the Snowflake validation SQL:

   ```bash
   python3 migration/generate_validation_sql.py
   ```

5. Run the resulting `VALIDATION.sql` in Snowflake and compare the output with
   `build/snowflake_load/source_metrics.json` and `verify/reconcile.py`.

## Exact Commands

```bash
make migrate
python3 migration/sas_to_snowflake.py --all
python3 migration/generate_validation_sql.py
python3 -m ruff check migration/ verify/
```
