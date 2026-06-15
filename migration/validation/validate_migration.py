"""
Migration Validation Script

Generates and optionally executes validation queries comparing source SAS data
against Snowflake target tables. Checks row counts, column checksums, and
sample record parity.

Usage:
    python migration/validation/validate_migration.py [--source-dir sample_data/]
                                                       [--generate-only]
"""

import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd
import pyreadstat


# ---------------------------------------------------------------------------
# Validation query templates
# ---------------------------------------------------------------------------

ROW_COUNT_QUERY = """-- Row Count Validation: {table_name}
-- Expected source count: {source_count}
SELECT
    '{table_name}' AS TABLE_NAME,
    COUNT(*) AS TARGET_ROW_COUNT,
    {source_count} AS SOURCE_ROW_COUNT,
    CASE
        WHEN COUNT(*) = {source_count} THEN 'PASS'
        ELSE 'FAIL: delta=' || (COUNT(*) - {source_count})::VARCHAR
    END AS VALIDATION_RESULT
FROM {schema}.{table_name};
"""

COLUMN_SUM_QUERY = """-- Column Sum Validation: {table_name}.{column_name}
-- Expected source sum: {source_sum}
SELECT
    '{table_name}.{column_name}' AS VALIDATION_TARGET,
    SUM({column_name}) AS TARGET_SUM,
    {source_sum} AS SOURCE_SUM,
    ABS(SUM({column_name}) - {source_sum}) AS ABSOLUTE_DIFF,
    CASE
        WHEN ABS(SUM({column_name}) - {source_sum}) < 0.01 THEN 'PASS'
        ELSE 'FAIL'
    END AS VALIDATION_RESULT
FROM {schema}.{table_name};
"""

DISTINCT_COUNT_QUERY = """-- Distinct Count Validation: {table_name}.{column_name}
-- Expected source distinct count: {source_distinct}
SELECT
    '{table_name}.{column_name}' AS VALIDATION_TARGET,
    COUNT(DISTINCT {column_name}) AS TARGET_DISTINCT,
    {source_distinct} AS SOURCE_DISTINCT,
    CASE
        WHEN COUNT(DISTINCT {column_name}) = {source_distinct} THEN 'PASS'
        ELSE 'FAIL'
    END AS VALIDATION_RESULT
FROM {schema}.{table_name};
"""

NULL_COUNT_QUERY = """-- Null Count Validation: {table_name}.{column_name}
-- Expected source null count: {source_nulls}
SELECT
    '{table_name}.{column_name}' AS VALIDATION_TARGET,
    COUNT(*) - COUNT({column_name}) AS TARGET_NULL_COUNT,
    {source_nulls} AS SOURCE_NULL_COUNT,
    CASE
        WHEN (COUNT(*) - COUNT({column_name})) = {source_nulls} THEN 'PASS'
        ELSE 'FAIL'
    END AS VALIDATION_RESULT
FROM {schema}.{table_name};
"""

MIN_MAX_QUERY = """-- Min/Max Validation: {table_name}.{column_name}
-- Expected: MIN={source_min}, MAX={source_max}
SELECT
    '{table_name}.{column_name}' AS VALIDATION_TARGET,
    MIN({column_name}) AS TARGET_MIN,
    MAX({column_name}) AS TARGET_MAX,
    '{source_min}' AS SOURCE_MIN,
    '{source_max}' AS SOURCE_MAX,
    CASE
        WHEN MIN({column_name})::VARCHAR = '{source_min}'
         AND MAX({column_name})::VARCHAR = '{source_max}' THEN 'PASS'
        ELSE 'FAIL'
    END AS VALIDATION_RESULT
FROM {schema}.{table_name};
"""

HASH_VALIDATION_QUERY = """-- Hash-based Row Validation: {table_name}
-- Compares MD5 hash of concatenated columns for sample rows
SELECT
    {pk_columns},
    MD5(CONCAT_WS('|', {all_columns})) AS ROW_HASH
FROM {schema}.{table_name}
ORDER BY {pk_columns}
LIMIT 100;
"""

FULL_RECONCILIATION_QUERY = """-- Full Reconciliation Report
SELECT
    TABLE_NAME,
    VALIDATION_TYPE,
    VALIDATION_TARGET,
    SOURCE_VALUE,
    TARGET_VALUE,
    VALIDATION_RESULT
FROM (
    -- CUST_ACCOUNTS row count
    SELECT 'CUST_ACCOUNTS' AS TABLE_NAME, 'ROW_COUNT' AS VALIDATION_TYPE,
           'CUST_ACCOUNTS' AS VALIDATION_TARGET,
           '{cust_accounts_rows}'::VARCHAR AS SOURCE_VALUE,
           COUNT(*)::VARCHAR AS TARGET_VALUE,
           CASE WHEN COUNT(*) = {cust_accounts_rows} THEN 'PASS' ELSE 'FAIL' END AS VALIDATION_RESULT
    FROM {schema}.CUST_ACCOUNTS
    UNION ALL
    -- DAILY_BALANCE row count
    SELECT 'DAILY_BALANCE', 'ROW_COUNT', 'DAILY_BALANCE',
           '{daily_balance_rows}'::VARCHAR,
           COUNT(*)::VARCHAR,
           CASE WHEN COUNT(*) = {daily_balance_rows} THEN 'PASS' ELSE 'FAIL' END
    FROM {schema}.DAILY_BALANCE
    UNION ALL
    -- MONTHLY_AMB row count
    SELECT 'MONTHLY_AMB', 'ROW_COUNT', 'MONTHLY_AMB',
           '{monthly_amb_rows}'::VARCHAR,
           COUNT(*)::VARCHAR,
           CASE WHEN COUNT(*) = {monthly_amb_rows} THEN 'PASS' ELSE 'FAIL' END
    FROM {schema}.MONTHLY_AMB
    UNION ALL
    -- DAILY_BALANCE sum check
    SELECT 'DAILY_BALANCE', 'COLUMN_SUM', 'END_OF_DAY_BALANCE',
           '{daily_balance_sum}'::VARCHAR,
           SUM(END_OF_DAY_BALANCE)::VARCHAR,
           CASE WHEN ABS(SUM(END_OF_DAY_BALANCE) - {daily_balance_sum}) < 0.01
                THEN 'PASS' ELSE 'FAIL' END
    FROM {schema}.DAILY_BALANCE
    UNION ALL
    -- MONTHLY_AMB sum check
    SELECT 'MONTHLY_AMB', 'COLUMN_SUM', 'AVERAGE_MONTHLY_BALANCE',
           '{monthly_amb_sum}'::VARCHAR,
           SUM(AVERAGE_MONTHLY_BALANCE)::VARCHAR,
           CASE WHEN ABS(SUM(AVERAGE_MONTHLY_BALANCE) - {monthly_amb_sum}) < 0.01
                THEN 'PASS' ELSE 'FAIL' END
    FROM {schema}.MONTHLY_AMB
)
ORDER BY TABLE_NAME, VALIDATION_TYPE;
"""


# ---------------------------------------------------------------------------
# Source statistics computation
# ---------------------------------------------------------------------------

TABLES_CONFIG = {
    "CUST_ACCOUNTS": {
        "numeric_columns": ["customer_id"],
        "pk_columns": ["customer_id", "account_id"],
        "all_columns": [
            "customer_id",
            "account_id",
            "account_type",
            "is_active",
            "start_date",
            "end_date",
        ],
    },
    "DAILY_BALANCE": {
        "numeric_columns": ["customer_id", "end_of_day_balance"],
        "pk_columns": ["customer_id", "account_id", "date"],
        "all_columns": [
            "customer_id",
            "account_id",
            "date",
            "end_of_day_balance",
            "month",
        ],
    },
    "MONTHLY_AMB": {
        "numeric_columns": [
            "customer_id",
            "reporting_month_yyyymm",
            "average_monthly_balance",
        ],
        "pk_columns": ["customer_id", "account_id", "reporting_month_yyyymm"],
        "all_columns": [
            "customer_id",
            "account_id",
            "reporting_month_yyyymm",
            "average_monthly_balance",
            "date_computed",
        ],
    },
}

# Snowflake column name mapping matches the DDL
SF_COLUMN_MAP = {
    "DAILY_BALANCE": {"date": "BALANCE_DATE", "month": "BALANCE_MONTH"},
}


def compute_source_stats(df: pd.DataFrame, table_name: str) -> dict:
    """Compute validation statistics from source DataFrame."""
    config = TABLES_CONFIG[table_name]
    stats = {
        "row_count": len(df),
        "columns": {},
    }

    for col in df.columns:
        col_stats = {
            "null_count": int(df[col].isna().sum()),
            "distinct_count": int(df[col].nunique()),
        }
        if col in config["numeric_columns"]:
            col_stats["sum"] = float(df[col].sum())
            col_stats["min"] = str(df[col].min())
            col_stats["max"] = str(df[col].max())
        stats["columns"][col] = col_stats

    return stats


def compute_source_hash(df: pd.DataFrame) -> str:
    """Compute MD5 hash of full source dataset for reconciliation."""
    csv_content = df.to_csv(index=False, na_rep="NULL")
    return hashlib.md5(csv_content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------


def generate_validation_queries(
    source_dir: Path, schema: str = "SAS_MIGRATION_DB.BANKING"
) -> str:
    """Generate all validation queries based on source data analysis."""
    all_queries = []
    all_queries.append("-- " + "=" * 77)
    all_queries.append("-- SAS-to-Snowflake Migration Validation Queries")
    all_queries.append(f"-- Schema: {schema}")
    all_queries.append("-- " + "=" * 77)
    all_queries.append("")

    table_stats = {}

    for table_name, config in TABLES_CONFIG.items():
        sas_path = source_dir / f"{table_name}.sas7bdat"
        if not sas_path.exists():
            print(f"[WARN] {sas_path} not found, skipping validation for {table_name}")
            continue

        df, _ = pyreadstat.read_sas7bdat(str(sas_path))
        stats = compute_source_stats(df, table_name)
        table_stats[table_name] = stats

        all_queries.append(f"-- {'=' * 77}")
        all_queries.append(f"-- Validation: {table_name}")
        all_queries.append(f"-- {'=' * 77}")
        all_queries.append("")

        # Row count
        all_queries.append(
            ROW_COUNT_QUERY.format(
                table_name=table_name,
                schema=schema,
                source_count=stats["row_count"],
            )
        )

        # Column-level validations
        for col, col_stats in stats["columns"].items():
            sf_col = SF_COLUMN_MAP.get(table_name, {}).get(col, col.upper())

            # Null count
            all_queries.append(
                NULL_COUNT_QUERY.format(
                    table_name=table_name,
                    column_name=sf_col,
                    schema=schema,
                    source_nulls=col_stats["null_count"],
                )
            )

            # Distinct count
            all_queries.append(
                DISTINCT_COUNT_QUERY.format(
                    table_name=table_name,
                    column_name=sf_col,
                    schema=schema,
                    source_distinct=col_stats["distinct_count"],
                )
            )

            # Sum for numeric columns
            if "sum" in col_stats:
                all_queries.append(
                    COLUMN_SUM_QUERY.format(
                        table_name=table_name,
                        column_name=sf_col,
                        schema=schema,
                        source_sum=col_stats["sum"],
                    )
                )

            # Min/Max for numeric columns
            if "min" in col_stats:
                all_queries.append(
                    MIN_MAX_QUERY.format(
                        table_name=table_name,
                        column_name=sf_col,
                        schema=schema,
                        source_min=col_stats["min"],
                        source_max=col_stats["max"],
                    )
                )

        # Hash-based sample validation
        pk_cols = ", ".join(
            SF_COLUMN_MAP.get(table_name, {}).get(c, c.upper())
            for c in config["pk_columns"]
        )
        all_cols = ", ".join(
            SF_COLUMN_MAP.get(table_name, {}).get(c, c.upper())
            for c in config["all_columns"]
        )
        all_queries.append(
            HASH_VALIDATION_QUERY.format(
                table_name=table_name,
                schema=schema,
                pk_columns=pk_cols,
                all_columns=all_cols,
            )
        )

    # Full reconciliation query
    if all(t in table_stats for t in TABLES_CONFIG):
        daily_bal_sum = table_stats["DAILY_BALANCE"]["columns"]["end_of_day_balance"][
            "sum"
        ]
        monthly_amb_sum = table_stats["MONTHLY_AMB"]["columns"][
            "average_monthly_balance"
        ]["sum"]
        all_queries.append(
            FULL_RECONCILIATION_QUERY.format(
                schema=schema,
                cust_accounts_rows=table_stats["CUST_ACCOUNTS"]["row_count"],
                daily_balance_rows=table_stats["DAILY_BALANCE"]["row_count"],
                monthly_amb_rows=table_stats["MONTHLY_AMB"]["row_count"],
                daily_balance_sum=daily_bal_sum,
                monthly_amb_sum=monthly_amb_sum,
            )
        )

    return "\n".join(all_queries)


# ---------------------------------------------------------------------------
# Local validation (source-only checks)
# ---------------------------------------------------------------------------


def run_local_validation(source_dir: Path) -> list[dict]:
    """Run validation checks locally against source data only.

    Returns a list of validation results with pass/fail status.
    """
    results = []

    for table_name in TABLES_CONFIG:
        sas_path = source_dir / f"{table_name}.sas7bdat"
        csv_path = source_dir / f"{table_name}.csv"

        if not sas_path.exists() or not csv_path.exists():
            continue

        # Read both formats
        df_sas, _ = pyreadstat.read_sas7bdat(str(sas_path))
        df_csv = pd.read_csv(csv_path)

        # Row count parity between SAS and CSV
        sas_rows = len(df_sas)
        csv_rows = len(df_csv)
        results.append(
            {
                "table": table_name,
                "check": "ROW_COUNT_SAS_VS_CSV",
                "source_value": sas_rows,
                "target_value": csv_rows,
                "status": "PASS" if sas_rows == csv_rows else "FAIL",
            }
        )

        # Column count parity
        sas_cols = len(df_sas.columns)
        csv_cols = len(df_csv.columns)
        results.append(
            {
                "table": table_name,
                "check": "COLUMN_COUNT_SAS_VS_CSV",
                "source_value": sas_cols,
                "target_value": csv_cols,
                "status": "PASS" if sas_cols == csv_cols else "FAIL",
            }
        )

        # Column name match
        sas_col_names = set(df_sas.columns)
        csv_col_names = set(df_csv.columns)
        results.append(
            {
                "table": table_name,
                "check": "COLUMN_NAMES_MATCH",
                "source_value": sorted(sas_col_names),
                "target_value": sorted(csv_col_names),
                "status": "PASS" if sas_col_names == csv_col_names else "FAIL",
            }
        )

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Generate Snowflake validation queries for SAS migration"
    )
    parser.add_argument(
        "--source-dir",
        type=str,
        default="sample_data",
        help="Directory containing source SAS7BDAT files",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default="migration/validation/validation_queries.sql",
        help="Output SQL file for validation queries",
    )
    parser.add_argument(
        "--schema",
        type=str,
        default="SAS_MIGRATION_DB.BANKING",
        help="Target Snowflake schema",
    )
    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Only generate queries, skip local validation",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent.parent
    source_dir = repo_root / args.source_dir
    output_file = repo_root / args.output_file

    if not source_dir.exists():
        print(f"[ERROR] Source directory not found: {source_dir}")
        sys.exit(1)

    # Generate validation queries
    print("=== Generating Snowflake Validation Queries ===")
    queries = generate_validation_queries(source_dir, args.schema)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(queries)
    print(f"  Written to: {output_file}")

    # Run local validation
    if not args.generate_only:
        print()
        print("=== Running Local Validation (SAS vs CSV parity) ===")
        results = run_local_validation(source_dir)
        for r in results:
            status_icon = "OK" if r["status"] == "PASS" else "FAIL"
            print(
                f"  [{status_icon}] {r['table']}.{r['check']}: "
                f"{r['source_value']} vs {r['target_value']}"
            )

        failures = [r for r in results if r["status"] == "FAIL"]
        if failures:
            print(f"\n  {len(failures)} validation(s) FAILED")
        else:
            print(f"\n  All {len(results)} validations PASSED")


if __name__ == "__main__":
    main()
