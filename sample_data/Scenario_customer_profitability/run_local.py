#!/usr/bin/env python3
"""Local runner for snowflake_sql/JOB07_CUSTOMER_PROFITABILITY.sql.

Snowflake credentials exist in the environment, but the demo account is
suspended (``000666 (57014): Your account is suspended due to lack of
payment method``), so the conversion is executed in DuckDB instead and the
Snowflake-side CSVs for the reconciliation are produced from there.

The .sql file is executed verbatim except for the dialect shims listed in
``SHIMS`` below. Inputs are CSVs in the estate's golden layout
(``<lib>__<table>.csv``; SAS dates as integers since 1960-01-01) and are typed
exactly as SAS types them: character -> VARCHAR (blank = NULL, i.e. SAS
missing), numeric -> DOUBLE (SAS numerics are IEEE doubles), dates -> DATE.

    python3 run_local.py                       # synthetic baseline_inputs/
    python3 run_local.py --inputs ~/golden     # estate data: fails like SAS

Outputs: <out>/REPORTS__CUSTOMER_PNL.csv, REPORTS__SEGMENT_PROFITABILITY.csv,
REPORTS__BRANCH_PROFITABILITY.csv.
"""
import argparse
import os
import re
import sys

import duckdb
import pandas as pd

scenarioDir = os.path.dirname(os.path.abspath(__file__))
repoRoot = os.path.dirname(os.path.dirname(scenarioDir))
sqlPath = os.path.join(repoRoot, "snowflake_sql", "JOB07_CUSTOMER_PROFITABILITY.sql")

# SAS libref -> Snowflake schema (same mapping the other JOBnn conversions use)
inputTables = {
    "stg_bank__cust_accounts_daily": ("STAGING", "CUST_ACCOUNTS_DAILY"),
    "curated__daily_transactions": ("CURATED", "DAILY_TRANSACTIONS"),
    "curated__risk_scores": ("CURATED", "RISK_SCORES"),
}
outputTables = ["CUSTOMER_PNL", "SEGMENT_PROFITABILITY", "BRANCH_PROFITABILITY"]

# SAS attribute classes, by column name (covers every column of the three
# estate tables as PROC EXPORTed by docker/sas/export_golden.sas).
charColumns = {
    "ACCOUNT_ID", "CUSTOMER_ID", "ACCOUNT_TYPE", "ACCOUNT_STATUS", "BRANCH_ID",
    "OFFICER_ID", "FIRST_NAME", "LAST_NAME", "SSN_HASH", "CUSTOMER_SEGMENT",
    "REGION_CODE", "PRIMARY_EMAIL", "PHONE_NUMBER", "DORMANCY_FLAG",
    "HIGH_BALANCE_FLAG", "TRANSACTION_ID", "TRANSACTION_TYPE", "CHANNEL",
    "MERCHANT_CATEGORY", "DESCRIPTION", "CURRENCY_CODE", "MODEL_ID",
}
dateColumns = {
    "OPEN_DATE", "CLOSE_DATE", "LAST_ACTIVITY_DATE", "DATE_OF_BIRTH",
    "SNAPSHOT_DATE", "TRANSACTION_DATE", "POST_DATE", "LAST_APPRAISAL_DATE",
    "SCORE_DATE",
}
datetimeColumns = {"LOAD_TIMESTAMP", "SCORE_TIMESTAMP"}

# Snowflake -> DuckDB dialect shims (the only edits applied to the .sql text)
SHIMS = [
    ("$report_month session variable", r"\$report_month", "getvariable('report_month')"),
]
SHIM_MACROS = [
    # Snowflake TO_DATE(expr, 'YYYYMMDD') -> DuckDB strptime; only the format the job uses
    ("CREATE MACRO TO_DATE(x, fmt) AS CASE WHEN fmt = 'YYYYMMDD' "
     "THEN strptime(x, '%Y%m%d')::DATE END"),
    # Snowflake EQUAL_NULL(a, b) -> NULL-safe equality
    "CREATE MACRO EQUAL_NULL(a, b) AS (a IS NOT DISTINCT FROM b)",
]


def sasTypedExpr(column: str) -> str:
    if column in charColumns:
        return f'NULLIF("{column}", \'\') AS "{column}"'
    if column in dateColumns:
        return f'DATE \'1960-01-01\' + CAST(NULLIF("{column}", \'\') AS INTEGER) AS "{column}"'
    if column in datetimeColumns:
        return (f'TIMESTAMP \'1960-01-01 00:00:00\' + to_seconds(CAST(NULLIF("{column}", \'\') '
                f'AS DOUBLE)) AS "{column}"')
    return f'CAST(NULLIF("{column}", \'\') AS DOUBLE) AS "{column}"'


def loadInputs(con: duckdb.DuckDBPyConnection, inputsDir: str) -> None:
    for schema in ("STAGING", "CURATED", "REPORTS"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for fileStem, (schema, table) in inputTables.items():
        path = os.path.join(inputsDir, f"{fileStem}.csv")
        rawDf = pd.read_csv(path, dtype=str, keep_default_na=False)
        rawDf.columns = [c.upper() for c in rawDf.columns]
        con.register("raw_df", rawDf)
        selectList = ",\n       ".join(sasTypedExpr(c) for c in rawDf.columns)
        con.execute(f"CREATE OR REPLACE TABLE {schema}.{table} AS SELECT {selectList} FROM raw_df")
        con.unregister("raw_df")
        nRows = con.execute(f"SELECT COUNT(*) FROM {schema}.{table}").fetchone()[0]
        print(f"  loaded {schema}.{table}: {nRows} rows from {path}")


def shimmedStatements(reportMonth: str) -> list:
    with open(sqlPath, encoding="utf-8") as fh:
        sqlText = fh.read()
    sqlText = re.sub(r"/\*.*?\*/", "", sqlText, flags=re.DOTALL)
    sqlText = "\n".join(line for line in sqlText.splitlines() if not line.strip().startswith("--"))
    for _, pattern, replacement in SHIMS:
        sqlText = re.sub(pattern, replacement, sqlText)
    statements = [s.strip() for s in sqlText.split(";") if s.strip()]
    return [f"SET VARIABLE report_month = '{reportMonth}'", *SHIM_MACROS, *statements]


def exportOutputs(con: duckdb.DuckDBPyConnection, outDir: str) -> None:
    os.makedirs(outDir, exist_ok=True)
    for table in outputTables:
        df = con.execute(f"SELECT * FROM REPORTS.{table}").df()
        df.columns = [c.upper() for c in df.columns]
        path = os.path.join(outDir, f"REPORTS__{table}.csv")
        df.to_csv(path, index=False, na_rep="")
        print(f"  wrote {path}: {len(df)} rows")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inputs", default=os.path.join(scenarioDir, "baseline_inputs"),
                        help="directory of <lib>__<table>.csv inputs (default: synthetic baseline_inputs/)")
    parser.add_argument("--out", default=scenarioDir, help="output directory (default: scenario dir)")
    parser.add_argument("--report-month", default="202401", help="SAS &report_month (default 202401 = &PREV_YM)")
    args = parser.parse_args()

    con = duckdb.connect()
    print(f"Inputs: {args.inputs}")
    loadInputs(con, args.inputs)
    print(f"Executing {sqlPath} (report_month={args.report_month}) in DuckDB")
    for statement in shimmedStatements(args.report_month):
        try:
            con.execute(statement)
        except duckdb.BinderException as exc:
            if "CUSTOMER_ID" in str(exc):
                print("\nLEGACY DEFECT REPRODUCED (SAS: 'ERROR: PROC SQL: the following columns were "
                      "not found in the contributing tables: CUSTOMER_ID'):")
                print(f"  {exc}")
                print("  Step 2 of customer_profitability.sas selects t.CUSTOMER_ID from "
                      "CURATED.DAILY_TRANSACTIONS, which has no such column. Not fixed by design.")
                return 2
            raise
    exportOutputs(con, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
