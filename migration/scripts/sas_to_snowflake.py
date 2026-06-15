"""
SAS-to-Snowflake Migration Script

Reads SAS7BDAT files from sample_data/, applies data type conversions,
exports cleaned CSVs for Snowflake bulk loading, and generates COPY INTO
statements.

Usage:
    python migration/scripts/sas_to_snowflake.py [--source-dir sample_data/]
                                                  [--output-dir migration/output/]
                                                  [--scenario baseline]
"""

import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd
import pyreadstat


# ---------------------------------------------------------------------------
# SAS-to-Snowflake type mapping
# ---------------------------------------------------------------------------

SAS_TYPE_MAP = {
    "BEST12": "NUMBER",
    "YYMMDD10": "DATE",
    "DATE7": "DATE",
    "$8": "VARCHAR(8)",
    "$16": "VARCHAR(16)",
}

# Column-level overrides for Snowflake target types
COLUMN_TYPE_OVERRIDES = {
    "customer_id": "INTEGER",
    "account_id": "VARCHAR(8)",
    "account_type": "VARCHAR(16)",
    "is_active": "VARCHAR(8)",
    "start_date": "DATE",
    "end_date": "DATE",
    "date": "DATE",
    "end_of_day_balance": "NUMBER(12,2)",
    "month": "VARCHAR(7)",
    "reporting_month_yyyymm": "INTEGER",
    "average_monthly_balance": "NUMBER(12,2)",
    "date_computed": "DATE",
}


# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------


def read_sas_file(filepath: Path) -> tuple[pd.DataFrame, dict]:
    """Read a SAS7BDAT file and return DataFrame with metadata."""
    df, meta = pyreadstat.read_sas7bdat(str(filepath))
    metadata = {
        "columns": list(meta.column_names),
        "original_types": meta.original_variable_types,
        "storage_widths": meta.variable_storage_width,
        "row_count": meta.number_rows,
        "file_label": meta.file_label,
        "encoding": meta.file_encoding,
    }
    return df, metadata


# ---------------------------------------------------------------------------
# Data type conversions
# ---------------------------------------------------------------------------


def convert_customer_id(series: pd.Series) -> pd.Series:
    """Convert SAS numeric customer_id to integer."""
    return series.astype("Int64")


def _detect_dayfirst(series: pd.Series) -> bool:
    """Auto-detect whether string dates use DD-MM-YYYY (dayfirst) format.

    Distinguishes between YYYY-MM-DD (first > 31 → year) and DD-MM-YYYY
    (first > 12 but <= 31 → day, with third > 31 → year).
    """
    sample = series.dropna().head(50)
    for val in sample:
        parts = str(val).split("-")
        if len(parts) == 3:
            try:
                first_part = int(parts[0])
                third_part = int(parts[2])
                if first_part > 31:
                    return False  # YYYY-MM-DD format (first is a year)
                if first_part > 12:
                    return True  # DD-MM-YYYY (day > 12 is unambiguous)
                if third_part > 31:
                    # XX-XX-YYYY: third is a year, so format is DD-MM-YYYY
                    return True
            except ValueError:
                continue
    return False


def convert_sas_date(series: pd.Series, format_hint: str = "YYMMDD10") -> pd.Series:
    """Convert SAS date values to ISO date strings (YYYY-MM-DD).

    SAS stores dates as days since 1960-01-01. pyreadstat may already convert
    them to datetime or return them as strings depending on the file.
    Auto-detects DD-MM-YYYY format when dates are already strings.
    """
    if pd.api.types.is_string_dtype(series) or series.dtype == "object":
        # Auto-detect date format from string content
        dayfirst = _detect_dayfirst(series)
        parsed = pd.to_datetime(series, errors="coerce", dayfirst=dayfirst)
        return parsed.dt.strftime("%Y-%m-%d").where(parsed.notna(), None)
    elif pd.api.types.is_float_dtype(series):
        # Raw SAS date numeric (days since 1960-01-01)
        sas_epoch = pd.Timestamp("1960-01-01")
        converted = series.apply(
            lambda x: (
                (sas_epoch + pd.Timedelta(days=x)).strftime("%Y-%m-%d")
                if pd.notna(x)
                else None
            )
        )
        return converted
    elif pd.api.types.is_datetime64_any_dtype(series):
        return series.dt.strftime("%Y-%m-%d").where(series.notna(), None)
    return series


def convert_month_field(series: pd.Series) -> pd.Series:
    """Convert month field to YYYY-MM string format."""
    if pd.api.types.is_string_dtype(series) or series.dtype == "object":
        dayfirst = _detect_dayfirst(series)
        parsed = pd.to_datetime(series, errors="coerce", dayfirst=dayfirst)
        return parsed.dt.strftime("%Y-%m").where(parsed.notna(), None)
    elif pd.api.types.is_datetime64_any_dtype(series):
        return series.dt.strftime("%Y-%m").where(series.notna(), None)
    return series.astype(str)


def convert_decimal(series: pd.Series, precision: int = 2) -> pd.Series:
    """Round numeric values to specified decimal precision."""
    return series.round(precision)


def convert_reporting_month(series: pd.Series) -> pd.Series:
    """Convert reporting_month_yyyymm to integer."""
    return series.astype("Int64")


# ---------------------------------------------------------------------------
# Table-specific transformation pipelines
# ---------------------------------------------------------------------------


def transform_cust_accounts(df: pd.DataFrame) -> pd.DataFrame:
    """Apply transformations for CUST_ACCOUNTS."""
    result = df.copy()
    result["customer_id"] = convert_customer_id(result["customer_id"])
    result["account_id"] = result["account_id"].str.strip()
    result["account_type"] = result["account_type"].str.strip().str.upper()
    result["is_active"] = result["is_active"].str.strip().str.upper()
    result["start_date"] = convert_sas_date(result["start_date"])
    result["end_date"] = convert_sas_date(result["end_date"])
    return result


def transform_daily_balance(df: pd.DataFrame) -> pd.DataFrame:
    """Apply transformations for DAILY_BALANCE."""
    result = df.copy()
    result["customer_id"] = convert_customer_id(result["customer_id"])
    result["account_id"] = result["account_id"].str.strip()
    result["date"] = convert_sas_date(result["date"])
    result["end_of_day_balance"] = convert_decimal(result["end_of_day_balance"])
    result["month"] = convert_month_field(result["month"])
    return result


def transform_monthly_amb(df: pd.DataFrame) -> pd.DataFrame:
    """Apply transformations for MONTHLY_AMB."""
    result = df.copy()
    result["customer_id"] = convert_customer_id(result["customer_id"])
    result["account_id"] = result["account_id"].str.strip()
    result["reporting_month_yyyymm"] = convert_reporting_month(
        result["reporting_month_yyyymm"]
    )
    result["average_monthly_balance"] = convert_decimal(
        result["average_monthly_balance"]
    )
    result["date_computed"] = convert_sas_date(result["date_computed"])
    return result


TRANSFORM_MAP = {
    "CUST_ACCOUNTS": transform_cust_accounts,
    "DAILY_BALANCE": transform_daily_balance,
    "MONTHLY_AMB": transform_monthly_amb,
}

# Snowflake column name mapping (SAS source -> Snowflake target)
COLUMN_RENAME_MAP = {
    "DAILY_BALANCE": {"date": "balance_date", "month": "balance_month"},
}


# ---------------------------------------------------------------------------
# CSV export for Snowflake COPY INTO
# ---------------------------------------------------------------------------


def export_csv_for_snowflake(
    df: pd.DataFrame, table_name: str, output_dir: Path
) -> Path:
    """Export DataFrame as CSV suitable for Snowflake COPY INTO."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{table_name}.csv"

    # Apply column renames if defined
    renamed_df = df.rename(columns=COLUMN_RENAME_MAP.get(table_name, {}))

    renamed_df.to_csv(
        output_path,
        index=False,
        na_rep="",
        date_format="%Y-%m-%d",
    )
    return output_path


# ---------------------------------------------------------------------------
# COPY INTO statement generation
# ---------------------------------------------------------------------------


def generate_copy_into(
    table_name: str, stage_name: str = "@SAS_MIGRATION_STAGE"
) -> str:
    """Generate Snowflake COPY INTO statement for a table."""
    stg_table = f"STG_{table_name}"
    target_table = table_name

    copy_stmt = f"""-- Load CSV into staging table
COPY INTO {stg_table}
FROM {stage_name}/{table_name}.csv
FILE_FORMAT = (FORMAT_NAME = 'SAS_MIGRATION_CSV_FORMAT')
ON_ERROR = 'CONTINUE'
PURGE = FALSE;
"""

    if table_name == "CUST_ACCOUNTS":
        insert_stmt = f"""
-- Insert from staging into target with type casting
INSERT INTO {target_table} (
    CUSTOMER_ID, ACCOUNT_ID, ACCOUNT_TYPE, IS_ACTIVE, START_DATE, END_DATE
)
SELECT
    TRY_CAST(CUSTOMER_ID AS INTEGER),
    ACCOUNT_ID,
    ACCOUNT_TYPE,
    IS_ACTIVE,
    TRY_CAST(START_DATE AS DATE),
    TRY_CAST(END_DATE AS DATE)
FROM {stg_table}
WHERE TRY_CAST(CUSTOMER_ID AS INTEGER) IS NOT NULL;
"""
    elif table_name == "DAILY_BALANCE":
        insert_stmt = f"""
-- Insert from staging into target with type casting
INSERT INTO {target_table} (
    CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE, END_OF_DAY_BALANCE, BALANCE_MONTH
)
SELECT
    TRY_CAST(CUSTOMER_ID AS INTEGER),
    ACCOUNT_ID,
    TRY_CAST(BALANCE_DATE AS DATE),
    TRY_CAST(END_OF_DAY_BALANCE AS NUMBER(12,2)),
    BALANCE_MONTH
FROM {stg_table}
WHERE TRY_CAST(CUSTOMER_ID AS INTEGER) IS NOT NULL
  AND TRY_CAST(BALANCE_DATE AS DATE) IS NOT NULL;
"""
    elif table_name == "MONTHLY_AMB":
        insert_stmt = f"""
-- Insert from staging into target with type casting
INSERT INTO {target_table} (
    CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM,
    AVERAGE_MONTHLY_BALANCE, DATE_COMPUTED
)
SELECT
    TRY_CAST(CUSTOMER_ID AS INTEGER),
    ACCOUNT_ID,
    TRY_CAST(REPORTING_MONTH_YYYYMM AS INTEGER),
    TRY_CAST(AVERAGE_MONTHLY_BALANCE AS NUMBER(12,2)),
    TRY_CAST(DATE_COMPUTED AS DATE)
FROM {stg_table}
WHERE TRY_CAST(CUSTOMER_ID AS INTEGER) IS NOT NULL;
"""
    else:
        insert_stmt = ""

    truncate_stg = f"""
-- Clean up staging
TRUNCATE TABLE {stg_table};
"""

    return copy_stmt + insert_stmt + truncate_stg


# ---------------------------------------------------------------------------
# Checksum computation
# ---------------------------------------------------------------------------


def compute_row_checksum(df: pd.DataFrame, table_name: str = "") -> str:
    """Compute an MD5 checksum matching the exported CSV representation."""
    # Apply same column renames as export_csv_for_snowflake
    renamed_df = df.rename(columns=COLUMN_RENAME_MAP.get(table_name, {}))
    content = renamed_df.to_csv(index=False, na_rep="", date_format="%Y-%m-%d")
    return hashlib.md5(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Main migration pipeline
# ---------------------------------------------------------------------------

TABLES = ["CUST_ACCOUNTS", "DAILY_BALANCE", "MONTHLY_AMB"]


def run_migration(source_dir: Path, output_dir: Path, scenario: str) -> dict:
    """Execute the full migration pipeline.

    Returns a summary dict with row counts and checksums per table.
    Supports both SAS7BDAT and CSV source files (CSV fallback when SAS not present).
    """
    summary = {}

    for table_name in TABLES:
        sas_path = source_dir / f"{table_name}.sas7bdat"
        csv_source_path = source_dir / f"{table_name}.csv"

        if sas_path.exists():
            print(f"[INFO] Processing {table_name} (SAS7BDAT)...")
            df, metadata = read_sas_file(sas_path)
            print(f"  SAS types: {metadata['original_types']}")
        elif csv_source_path.exists():
            print(f"[INFO] Processing {table_name} (CSV fallback)...")
            df = pd.read_csv(csv_source_path)
        else:
            print(f"[WARN] No SAS or CSV file found for {table_name} in {source_dir}, skipping.")
            continue

        source_rows = len(df)
        print(f"  Source rows: {source_rows}")

        # 2. Transform
        transform_fn = TRANSFORM_MAP[table_name]
        transformed_df = transform_fn(df)

        # 3. Export CSV
        csv_path = export_csv_for_snowflake(transformed_df, table_name, output_dir)
        print(f"  Exported to: {csv_path}")

        # 4. Generate COPY INTO
        copy_sql = generate_copy_into(table_name)
        sql_path = output_dir / f"load_{table_name}.sql"
        sql_path.write_text(copy_sql)
        print(f"  Load SQL: {sql_path}")

        # 5. Compute checksum (matches exported CSV exactly)
        checksum = compute_row_checksum(transformed_df, table_name)

        summary[table_name] = {
            "source_rows": source_rows,
            "exported_rows": len(transformed_df),
            "checksum_md5": checksum,
            "csv_path": str(csv_path),
            "scenario": scenario,
        }

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="SAS-to-Snowflake migration: read SAS7BDAT, transform, export CSV"
    )
    parser.add_argument(
        "--source-dir",
        type=str,
        default="sample_data",
        help="Directory containing SAS7BDAT source files",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="migration/output",
        help="Directory for exported CSVs and SQL scripts",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default="baseline",
        choices=["baseline", "scenario1", "scenario2"],
        help="Migration scenario to process",
    )
    args = parser.parse_args()

    # Resolve paths relative to repo root
    repo_root = Path(__file__).resolve().parent.parent.parent
    source_dir = repo_root / args.source_dir
    output_dir = repo_root / args.output_dir / args.scenario

    if not source_dir.exists():
        print(f"[ERROR] Source directory not found: {source_dir}")
        sys.exit(1)

    print("=== SAS-to-Snowflake Migration ===")
    print(f"Source: {source_dir}")
    print(f"Output: {output_dir}")
    print(f"Scenario: {args.scenario}")
    print()

    summary = run_migration(source_dir, output_dir, args.scenario)

    print()
    print("=== Migration Summary ===")
    for table, info in summary.items():
        print(f"  {table}: {info['source_rows']} rows, MD5={info['checksum_md5']}")

    return summary


if __name__ == "__main__":
    main()
