"""Generate validation queries comparing SAS source data against Snowflake target.

Produces SQL + Python checks for:
  1. Row count parity
  2. Column-level checksums (SUM, COUNT DISTINCT)
  3. NULL distribution comparison
  4. Hash-based row-level reconciliation
"""

import hashlib
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================================
# Snowflake-side validation SQL generators
# ============================================================================


def row_count_query(table: str, schema: str = "SAS_MIGRATION.BANKING") -> str:
    return (
        f"SELECT '{table}' AS TABLE_NAME, COUNT(*) AS ROW_COUNT FROM {schema}.{table};"
    )


def column_sum_query(
    table: str,
    columns: list[str],
    schema: str = "SAS_MIGRATION.BANKING",
) -> str:
    sums = ",\n    ".join(f"SUM({c}) AS SUM_{c}" for c in columns)
    return f"""\
SELECT
    '{table}' AS TABLE_NAME,
    COUNT(*) AS ROW_COUNT,
    {sums}
FROM {schema}.{table};"""


def distinct_count_query(
    table: str,
    columns: list[str],
    schema: str = "SAS_MIGRATION.BANKING",
) -> str:
    counts = ",\n    ".join(f"COUNT(DISTINCT {c}) AS DISTINCT_{c}" for c in columns)
    return f"""\
SELECT
    '{table}' AS TABLE_NAME,
    {counts}
FROM {schema}.{table};"""


def null_distribution_query(
    table: str,
    columns: list[str],
    schema: str = "SAS_MIGRATION.BANKING",
) -> str:
    nulls = ",\n    ".join(
        f"SUM(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END) AS NULL_{c}" for c in columns
    )
    return f"""\
SELECT
    '{table}' AS TABLE_NAME,
    COUNT(*) AS TOTAL_ROWS,
    {nulls}
FROM {schema}.{table};"""


def row_hash_query(
    table: str,
    columns: list[str],
    schema: str = "SAS_MIGRATION.BANKING",
) -> str:
    """Generate MD5-based row hash for deduplication/reconciliation.

    Snowflake's MD5() operates on a string, so we concatenate columns with a
    delimiter to avoid collisions (e.g., '12' || '3' vs '1' || '23').
    """
    concat_expr = " || '|' || ".join(
        f"COALESCE(CAST({c} AS VARCHAR), '')" for c in columns
    )
    return f"""\
SELECT
    MD5({concat_expr}) AS ROW_HASH,
    {", ".join(columns)}
FROM {schema}.{table}
ORDER BY {", ".join(columns[:2])};"""


def full_validation_script(schema: str = "SAS_MIGRATION.BANKING") -> str:
    """Generate a comprehensive validation SQL script for all three tables."""
    sections: list[str] = [
        "-- ==================================================================",
        "-- SAS-to-Snowflake Migration Validation Queries",
        "-- ==================================================================",
        "",
        "-- 1. Row count parity",
        row_count_query("CUST_ACCOUNTS", schema),
        row_count_query("DAILY_BALANCE", schema),
        row_count_query("MONTHLY_AMB", schema),
        "",
        "-- 2. Numeric column sums",
        column_sum_query("DAILY_BALANCE", ["END_OF_DAY_BALANCE"], schema),
        column_sum_query("MONTHLY_AMB", ["AVERAGE_MONTHLY_BALANCE"], schema),
        "",
        "-- 3. Distinct counts on key columns",
        distinct_count_query("CUST_ACCOUNTS", ["CUSTOMER_ID", "ACCOUNT_ID"], schema),
        distinct_count_query("DAILY_BALANCE", ["CUSTOMER_ID", "ACCOUNT_ID"], schema),
        distinct_count_query("MONTHLY_AMB", ["CUSTOMER_ID", "ACCOUNT_ID"], schema),
        "",
        "-- 4. NULL distribution",
        null_distribution_query(
            "CUST_ACCOUNTS",
            [
                "CUSTOMER_ID",
                "ACCOUNT_ID",
                "START_DATE",
                "END_DATE",
            ],
            schema,
        ),
        null_distribution_query(
            "DAILY_BALANCE",
            [
                "CUSTOMER_ID",
                "ACCOUNT_ID",
                "BALANCE_DATE",
                "END_OF_DAY_BALANCE",
            ],
            schema,
        ),
        null_distribution_query(
            "MONTHLY_AMB",
            [
                "CUSTOMER_ID",
                "ACCOUNT_ID",
                "REPORTING_MONTH_YYYYMM",
                "AVERAGE_MONTHLY_BALANCE",
                "DATE_COMPUTED",
            ],
            schema,
        ),
        "",
        "-- 5. Row hash samples (first 100 rows per table for spot-check)",
        _limit_query(
            row_hash_query(
                "CUST_ACCOUNTS",
                [
                    "CUSTOMER_ID",
                    "ACCOUNT_ID",
                    "ACCOUNT_TYPE",
                    "IS_ACTIVE",
                    "START_DATE",
                    "END_DATE",
                ],
                schema,
            ),
            100,
        ),
        _limit_query(
            row_hash_query(
                "DAILY_BALANCE",
                [
                    "CUSTOMER_ID",
                    "ACCOUNT_ID",
                    "BALANCE_DATE",
                    "END_OF_DAY_BALANCE",
                    "BALANCE_MONTH",
                ],
                schema,
            ),
            100,
        ),
        _limit_query(
            row_hash_query(
                "MONTHLY_AMB",
                [
                    "CUSTOMER_ID",
                    "ACCOUNT_ID",
                    "REPORTING_MONTH_YYYYMM",
                    "AVERAGE_MONTHLY_BALANCE",
                    "DATE_COMPUTED",
                ],
                schema,
            ),
            100,
        ),
    ]
    return "\n".join(sections)


def _limit_query(sql: str, limit: int) -> str:
    """Append a LIMIT clause to a query (replace trailing semicolon)."""
    return sql.rstrip().rstrip(";") + f"\nLIMIT {limit};"


# ============================================================================
# Python-side source validation (runs against the SAS-extracted DataFrames)
# ============================================================================


def compute_dataframe_hash(df: pd.DataFrame) -> str:
    """Compute a deterministic MD5 over an entire DataFrame for reconciliation."""
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    return hashlib.md5(csv_bytes).hexdigest()


def compute_row_hashes(df: pd.DataFrame) -> pd.Series:
    """Compute per-row MD5 hashes using pipe-delimited column concatenation."""

    def _hash_row(row: pd.Series) -> str:
        parts = "|".join(str(v) if pd.notna(v) else "" for v in row)
        return hashlib.md5(parts.encode("utf-8")).hexdigest()

    return df.apply(_hash_row, axis=1)


def validate_source_target(
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
    table_name: str,
    numeric_columns: list[str] | None = None,
    key_columns: list[str] | None = None,
) -> dict:
    """Run a comprehensive source-vs-target validation suite.

    Parameters
    ----------
    source_df : DataFrame
        SAS-extracted source data.
    target_df : DataFrame
        Data loaded into Snowflake (or a CSV snapshot of it).
    table_name : str
        Table name for reporting.
    numeric_columns : list[str], optional
        Columns to validate with SUM checks.
    key_columns : list[str], optional
        Columns to validate with DISTINCT COUNT checks.

    Returns
    -------
    dict with pass/fail results for each validation check.
    """
    results: dict = {"table": table_name, "checks": []}

    # Row count
    src_count = len(source_df)
    tgt_count = len(target_df)
    results["checks"].append(
        {
            "check": "row_count",
            "source": src_count,
            "target": tgt_count,
            "passed": src_count == tgt_count,
        }
    )

    # Column count
    results["checks"].append(
        {
            "check": "column_count",
            "source": len(source_df.columns),
            "target": len(target_df.columns),
            "passed": len(source_df.columns) == len(target_df.columns),
        }
    )

    # Numeric sums
    for col in numeric_columns or []:
        if col in source_df.columns and col in target_df.columns:
            src_sum = round(float(source_df[col].sum()), 2)
            tgt_sum = round(float(target_df[col].sum()), 2)
            results["checks"].append(
                {
                    "check": f"sum_{col}",
                    "source": src_sum,
                    "target": tgt_sum,
                    "passed": abs(src_sum - tgt_sum) < 0.01,
                }
            )

    # Distinct counts
    for col in key_columns or []:
        if col in source_df.columns and col in target_df.columns:
            src_dc = source_df[col].nunique()
            tgt_dc = target_df[col].nunique()
            results["checks"].append(
                {
                    "check": f"distinct_count_{col}",
                    "source": src_dc,
                    "target": tgt_dc,
                    "passed": src_dc == tgt_dc,
                }
            )

    # NULL distribution
    for col in source_df.columns:
        if col in target_df.columns:
            src_nulls = int(source_df[col].isna().sum())
            tgt_nulls = int(target_df[col].isna().sum())
            results["checks"].append(
                {
                    "check": f"null_count_{col}",
                    "source": src_nulls,
                    "target": tgt_nulls,
                    "passed": src_nulls == tgt_nulls,
                }
            )

    # Full-table hash
    src_hash = compute_dataframe_hash(source_df)
    tgt_hash = compute_dataframe_hash(target_df)
    results["checks"].append(
        {
            "check": "full_table_hash",
            "source": src_hash,
            "target": tgt_hash,
            "passed": src_hash == tgt_hash,
        }
    )

    passed = sum(1 for c in results["checks"] if c["passed"])
    total = len(results["checks"])
    results["summary"] = f"{passed}/{total} checks passed"
    results["all_passed"] = passed == total

    return results


def format_validation_report(results: dict) -> str:
    """Format validation results as a human-readable text report."""
    lines: list[str] = [
        f"Validation Report: {results['table']}",
        "=" * 60,
    ]
    for check in results["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        lines.append(
            f"  [{status}] {check['check']}: "
            f"source={check['source']}, target={check['target']}"
        )
    lines.append("-" * 60)
    lines.append(f"  {results['summary']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Generate Snowflake validation SQL or run local validation"
    )
    subparsers = parser.add_subparsers(dest="command")

    sql_parser = subparsers.add_parser("sql", help="Generate validation SQL script")
    sql_parser.add_argument("-o", "--output", default="migration/output/validation.sql")
    sql_parser.add_argument("--schema", default="SAS_MIGRATION.BANKING")

    local_parser = subparsers.add_parser(
        "local", help="Run local source-vs-target validation"
    )
    local_parser.add_argument(
        "source_dir", help="Directory with SAS-extracted CSVs (source)"
    )
    local_parser.add_argument(
        "target_dir", help="Directory with Snowflake-loaded CSVs (target)"
    )

    args = parser.parse_args()

    if args.command == "sql":
        sql = full_validation_script(args.schema)
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(sql, encoding="utf-8")
        print(f"Validation SQL written to {out}")

    elif args.command == "local":
        datasets = {
            "CUST_ACCOUNTS": {
                "numeric": [],
                "keys": ["CUSTOMER_ID", "ACCOUNT_ID"],
            },
            "DAILY_BALANCE": {
                "numeric": ["END_OF_DAY_BALANCE"],
                "keys": ["CUSTOMER_ID", "ACCOUNT_ID"],
            },
            "MONTHLY_AMB": {
                "numeric": ["AVERAGE_MONTHLY_BALANCE"],
                "keys": ["CUSTOMER_ID", "ACCOUNT_ID"],
            },
        }
        from migration.sas_reader import apply_column_mapping

        all_passed = True
        for name, cfg in datasets.items():
            src_path = Path(args.source_dir) / f"{name}.csv"
            tgt_path = Path(args.target_dir) / f"{name}.csv"
            if not src_path.exists() or not tgt_path.exists():
                print(f"  SKIP {name}: file not found")
                continue
            src_df = apply_column_mapping(pd.read_csv(src_path), name)
            tgt_df = pd.read_csv(tgt_path)
            results = validate_source_target(
                src_df,
                tgt_df,
                name,
                numeric_columns=cfg["numeric"],
                key_columns=cfg["keys"],
            )
            print(format_validation_report(results))
            print()
            if not results["all_passed"]:
                all_passed = False

        sys.exit(0 if all_passed else 1)

    else:
        parser.print_help()
