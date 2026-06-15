"""Generate Snowflake COPY INTO statements for bulk-loading CSV files.

Produces ready-to-execute SQL for both internal-stage and external-stage
loading patterns, including file format definitions and staging table
population.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Maps (target table → staging table → CSV column order)
TABLE_DEFINITIONS: dict[str, dict] = {
    "CUST_ACCOUNTS": {
        "stage_table": "STG_CUST_ACCOUNTS",
        "target_table": "CUST_ACCOUNTS",
        "csv_columns": [
            "CUSTOMER_ID",
            "ACCOUNT_ID",
            "ACCOUNT_TYPE",
            "IS_ACTIVE",
            "START_DATE",
            "END_DATE",
        ],
    },
    "DAILY_BALANCE": {
        "stage_table": "STG_DAILY_BALANCE",
        "target_table": "DAILY_BALANCE",
        "csv_columns": [
            "CUSTOMER_ID",
            "ACCOUNT_ID",
            "BALANCE_DATE",
            "END_OF_DAY_BALANCE",
            "BALANCE_MONTH",
        ],
    },
    "MONTHLY_AMB": {
        "stage_table": "STG_MONTHLY_AMB",
        "target_table": "MONTHLY_AMB",
        "csv_columns": [
            "CUSTOMER_ID",
            "ACCOUNT_ID",
            "REPORTING_MONTH_YYYYMM",
            "AVERAGE_MONTHLY_BALANCE",
            "DATE_COMPUTED",
        ],
    },
}


@dataclass
class LoadConfig:
    """Configuration for a Snowflake COPY INTO operation."""

    database: str = "SAS_MIGRATION"
    schema: str = "BANKING"
    stage_name: str = "SAS_CSV_STAGE"
    file_format_name: str = "SAS_CSV_FORMAT"
    warehouse: str = "COMPUTE_WH"


def generate_file_format_sql(config: LoadConfig) -> str:
    """Return CREATE FILE FORMAT statement for SAS-exported CSVs."""
    return f"""\
CREATE OR REPLACE FILE FORMAT {config.database}.{config.schema}.{config.file_format_name}
    TYPE = 'CSV'
    FIELD_DELIMITER = ','
    SKIP_HEADER = 1
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    NULL_IF = ('', 'NA', 'NaN', '.')
    EMPTY_FIELD_AS_NULL = TRUE
    TRIM_SPACE = TRUE
    ERROR_ON_COLUMN_COUNT_MISMATCH = TRUE
    ENCODING = 'UTF8';"""


def generate_stage_sql(config: LoadConfig) -> str:
    """Return CREATE STAGE statement for an internal named stage."""
    return f"""\
CREATE OR REPLACE STAGE {config.database}.{config.schema}.{config.stage_name}
    FILE_FORMAT = {config.database}.{config.schema}.{config.file_format_name}
    COMMENT = 'Internal stage for SAS-to-Snowflake CSV migration files';"""


def generate_put_command(csv_path: str, config: LoadConfig) -> str:
    """Return a PUT command to upload a local CSV into the named stage."""
    return f"PUT file://{csv_path} @{config.database}.{config.schema}.{config.stage_name} AUTO_COMPRESS=TRUE OVERWRITE=TRUE;"


def generate_copy_into_sql(
    table_name: str,
    config: LoadConfig,
    csv_filename: str | None = None,
) -> str:
    """Generate COPY INTO statement for a specific table.

    Parameters
    ----------
    table_name : str
        One of CUST_ACCOUNTS, DAILY_BALANCE, MONTHLY_AMB.
    config : LoadConfig
        Snowflake connection/stage configuration.
    csv_filename : str, optional
        Specific CSV filename pattern on the stage. Defaults to TABLE_NAME.csv.

    Returns
    -------
    str
        Ready-to-execute COPY INTO SQL.
    """
    defn = TABLE_DEFINITIONS[table_name]
    stage_table = defn["stage_table"]
    csv_columns = defn["csv_columns"]
    pattern = csv_filename or f"{table_name}.csv"

    col_list = ",\n        ".join(f"${i + 1}" for i in range(len(csv_columns)))
    target_cols = ",\n        ".join(csv_columns)

    return f"""\
-- Truncate staging table before load
TRUNCATE TABLE IF EXISTS {config.database}.{config.schema}.{stage_table};

-- COPY INTO staging table from internal stage
COPY INTO {config.database}.{config.schema}.{stage_table} (
        {target_cols}
    )
    FROM (
        SELECT
            {col_list}
        FROM @{config.database}.{config.schema}.{config.stage_name}/{pattern}
    )
    FILE_FORMAT = (FORMAT_NAME = '{config.database}.{config.schema}.{config.file_format_name}')
    ON_ERROR = 'CONTINUE'
    PURGE = FALSE;"""


def generate_staging_to_final_sql(table_name: str, config: LoadConfig) -> str:
    """Generate INSERT ... SELECT from staging table to final table with type casts."""
    fqn = f"{config.database}.{config.schema}"

    if table_name == "CUST_ACCOUNTS":
        return f"""\
TRUNCATE TABLE IF EXISTS {fqn}.CUST_ACCOUNTS;

INSERT INTO {fqn}.CUST_ACCOUNTS (
    CUSTOMER_ID, ACCOUNT_ID, ACCOUNT_TYPE, IS_ACTIVE, START_DATE, END_DATE
)
SELECT
    TRY_CAST(CUSTOMER_ID AS INTEGER),
    TRIM(ACCOUNT_ID),
    TRIM(ACCOUNT_TYPE),
    TRIM(IS_ACTIVE),
    TRY_TO_DATE(START_DATE, 'YYYY-MM-DD'),
    TRY_TO_DATE(END_DATE, 'YYYY-MM-DD')
FROM {fqn}.STG_CUST_ACCOUNTS
WHERE TRY_CAST(CUSTOMER_ID AS INTEGER) IS NOT NULL;"""

    if table_name == "DAILY_BALANCE":
        return f"""\
TRUNCATE TABLE IF EXISTS {fqn}.DAILY_BALANCE;

INSERT INTO {fqn}.DAILY_BALANCE (
    CUSTOMER_ID, ACCOUNT_ID, BALANCE_DATE, END_OF_DAY_BALANCE, BALANCE_MONTH
)
SELECT
    TRY_CAST(CUSTOMER_ID AS INTEGER),
    TRIM(ACCOUNT_ID),
    TRY_TO_DATE(BALANCE_DATE, 'YYYY-MM-DD'),
    TRY_CAST(END_OF_DAY_BALANCE AS NUMBER(12,2)),
    TRIM(BALANCE_MONTH)
FROM {fqn}.STG_DAILY_BALANCE
WHERE TRY_CAST(CUSTOMER_ID AS INTEGER) IS NOT NULL;"""

    if table_name == "MONTHLY_AMB":
        return f"""\
TRUNCATE TABLE IF EXISTS {fqn}.MONTHLY_AMB;

INSERT INTO {fqn}.MONTHLY_AMB (
    CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM,
    AVERAGE_MONTHLY_BALANCE, DATE_COMPUTED
)
SELECT
    TRY_CAST(CUSTOMER_ID AS INTEGER),
    TRIM(ACCOUNT_ID),
    TRY_CAST(REPORTING_MONTH_YYYYMM AS INTEGER),
    TRY_CAST(AVERAGE_MONTHLY_BALANCE AS NUMBER(12,2)),
    TRY_TO_DATE(DATE_COMPUTED, 'YYYY-MM-DD')
FROM {fqn}.STG_MONTHLY_AMB
WHERE TRY_CAST(CUSTOMER_ID AS INTEGER) IS NOT NULL;"""

    raise ValueError(f"Unknown table: {table_name}")


def generate_full_load_script(
    csv_dir: str | Path,
    config: LoadConfig | None = None,
) -> str:
    """Generate a complete Snowflake loading script for all three datasets.

    Returns a single SQL string that can be executed end-to-end in a
    Snowflake worksheet.
    """
    config = config or LoadConfig()
    csv_dir = Path(csv_dir)

    sections: list[str] = [
        "-- ==========================================================",
        "-- SAS-to-Snowflake Bulk Load Script",
        f"-- Generated for: {config.database}.{config.schema}",
        "-- ==========================================================",
        "",
        f"USE WAREHOUSE {config.warehouse};",
        f"USE SCHEMA {config.database}.{config.schema};",
        "",
        "-- 1. File format",
        generate_file_format_sql(config),
        "",
        "-- 2. Internal stage",
        generate_stage_sql(config),
        "",
        "-- 3. PUT files into stage",
    ]

    for table_name in TABLE_DEFINITIONS:
        csv_path = csv_dir / f"{table_name}.csv"
        sections.append(generate_put_command(str(csv_path), config))

    sections.append("")
    sections.append("-- 4. COPY INTO staging tables")
    for table_name in TABLE_DEFINITIONS:
        sections.append(generate_copy_into_sql(table_name, config))
        sections.append("")

    sections.append("-- 5. Type-cast staging → final tables")
    for table_name in TABLE_DEFINITIONS:
        sections.append(generate_staging_to_final_sql(table_name, config))
        sections.append("")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Snowflake COPY INTO SQL for SAS migration CSVs"
    )
    parser.add_argument(
        "csv_dir",
        help="Directory containing exported CSV files",
    )
    parser.add_argument("--database", default="SAS_MIGRATION")
    parser.add_argument("--schema", default="BANKING")
    parser.add_argument("--warehouse", default="COMPUTE_WH")
    parser.add_argument(
        "-o",
        "--output",
        default="migration/output/snowflake_load.sql",
        help="Output SQL file path",
    )
    args = parser.parse_args()

    config = LoadConfig(
        database=args.database,
        schema=args.schema,
        warehouse=args.warehouse,
    )
    sql = generate_full_load_script(args.csv_dir, config)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(sql, encoding="utf-8")
    print(f"Generated load script: {out_path}")
