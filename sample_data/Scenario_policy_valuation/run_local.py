#!/usr/bin/env python3
"""Run snowflake_sql/JOB06_POLICY_VALUATION.sql against the synthetic baseline
inputs and export the two output tables as <LIB>__<TABLE>.csv for the
reconciliation harness (verify/reconcile.py --sas-golden ...).

Engines
  --engine snowflake  (default when SNOWFLAKE_PAT or SNOWFLAKE_PASSWORD is set)
      Needs SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER and SNOWFLAKE_PAT (or
      SNOWFLAKE_PASSWORD). Optional: SNOWFLAKE_WAREHOUSE (COMPUTE_WH),
      SNOWFLAKE_DATABASE (SAS_MIG_JOB06_PV, created if missing). The .sql is
      executed unchanged inside that database (schemas RAW_INS / STG_INS /
      REPORTS = the SAS librefs).
  --engine duckdb
      Local fallback when no Snowflake credentials are available. The .sql is
      executed through the smallest possible dialect shim (see DUCKDB_SHIMS):
      session variables, DATEADD(month, n, d) and the *_IGNORE_NULLS forms.

Examples
  python3 sample_data/Scenario_policy_valuation/run_local.py --val-date 2024-01-31
  python3 sample_data/Scenario_policy_valuation/run_local.py --engine duckdb \
      --val-date 2024-06-30 --out sample_data/Scenario_policy_valuation/logic_check_30JUN2024
"""
import argparse
import csv
import datetime as dt
import json
import math
import os
import re
import sys
import time
from decimal import Decimal

scenarioDir = os.path.dirname(os.path.abspath(__file__))
repoRoot = os.path.dirname(os.path.dirname(scenarioDir))
sqlFile = os.path.join(repoRoot, "snowflake_sql", "JOB06_POLICY_VALUATION.sql")
inputsDir = os.path.join(scenarioDir, "baseline_inputs")

# SAS types every numeric as an 8-byte double and every date as a numeric
# date -> FLOAT / DATE here; character lengths are the SAS lengths assumed in
# BASELINE_ASSUMPTIONS.md (claims_processing.sas: POLICY_TYPE $10).
INPUT_DDL = {
    "RAW_INS.POLICIES": [
        ("POLICY_ID", "VARCHAR(12)"), ("CUSTOMER_ID", "VARCHAR(10)"),
        ("POLICY_TYPE", "VARCHAR(10)"), ("STATUS", "VARCHAR(10)"),
        ("EFFECTIVE_DATE", "DATE"), ("EXPIRATION_DATE", "DATE"),
        ("ANNUAL_PREMIUM", "FLOAT"), ("SUM_INSURED", "FLOAT"),
        ("DEDUCTIBLE", "FLOAT"), ("RISK_CATEGORY", "VARCHAR(5)"),
        ("UNDERWRITING_CLASS", "VARCHAR(2)"), ("AGENT_ID", "VARCHAR(8)"),
        ("BRANCH_CODE", "VARCHAR(8)"),
    ],
    "RAW_INS.CLAIMS": [
        ("CLAIM_ID", "VARCHAR(12)"), ("POLICY_ID", "VARCHAR(12)"),
        ("LOSS_DATE", "DATE"), ("CLAIM_STATUS", "VARCHAR(4)"),
        ("INCURRED_AMOUNT", "FLOAT"), ("PAID_AMOUNT", "FLOAT"),
        ("RESERVED_AMOUNT", "FLOAT"),
    ],
    "RAW_INS.PREMIUMS": [
        ("PAYMENT_ID", "VARCHAR(12)"), ("POLICY_ID", "VARCHAR(12)"),
        ("PAYMENT_DATE", "DATE"), ("PREMIUM_AMOUNT", "FLOAT"),
        ("PAYMENT_STATUS", "VARCHAR(10)"),
    ],
}
OUTPUT_TABLES = {
    "STG_INS.POLICY_VALUATION": "POLICY_ID",
    "REPORTS.LOSS_RATIO_SUMMARY": "POLICY_TYPE",
}
SCHEMAS = ["RAW_INS", "STG_INS", "REPORTS"]

# Snowflake -> DuckDB rewrites, applied in this order (regex, replacement).
DUCKDB_SHIMS = [
    (r"DATEADD\(month,\s*(-?\d+),\s*(\$\w+)\)", r"(\2 + INTERVAL (\1) MONTH)"),
    (r"(?im)^SET\s+(\w+)\s*=", r"SET VARIABLE \1 ="),
    (r"\$(\w+)", r"getvariable('\1')"),
    (r"GREATEST_IGNORE_NULLS\(", "greatest("),   # DuckDB greatest/least already
    (r"LEAST_IGNORE_NULLS\(", "least("),         # ignore NULLs (verified 1.5)
]


def stripComments(sqlText: str) -> str:
    sqlText = re.sub(r"/\*.*?\*/", "", sqlText, flags=re.DOTALL)
    sqlText = re.sub(r"--[^\n]*", "", sqlText)
    return sqlText


def splitStatements(sqlText: str) -> list:
    return [s.strip() for s in stripComments(sqlText).split(";") if s.strip()]


def readInput(table: str) -> list:
    path = os.path.join(inputsDir, table.replace(".", "__") + ".csv")
    columns = INPUT_DDL[table]
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == [c for c, _ in columns], f"{path}: unexpected header"
        for rec in reader:
            row = []
            for colName, colType in columns:
                raw = rec[colName]
                if raw == "":
                    row.append(None)          # SAS missing (. or blank)
                elif colType == "DATE":
                    row.append(dt.date.fromisoformat(raw))
                elif colType == "FLOAT":
                    row.append(float(raw))
                else:
                    row.append(raw)
            rows.append(row)
    return rows


def loadInputs(cur, placeholder: str) -> dict:
    counts = {}
    for schema in SCHEMAS:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for table, columns in INPUT_DDL.items():
        colDdl = ", ".join(f"{c} {t}" for c, t in columns)
        cur.execute(f"CREATE OR REPLACE TABLE {table} ({colDdl})")
        rows = readInput(table)
        marks = ", ".join([placeholder] * len(columns))
        cur.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
        counts[table] = len(rows)
    return counts


def toCsvValue(value):
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, Decimal):
        value = int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value)


def exportTable(cur, table: str, orderBy: str, outDir: str) -> int:
    cur.execute(f"SELECT * FROM {table} ORDER BY {orderBy}")
    header = [d[0].upper() for d in cur.description]
    rows = cur.fetchall()
    outPath = os.path.join(outDir, table.replace(".", "__") + ".csv")
    with open(outPath, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for row in rows:
            writer.writerow([toCsvValue(v) for v in row])
    return len(rows)


def sessionVariableStatements(valDate: str, lob: str) -> list:
    dt.date.fromisoformat(valDate)                      # validate
    if lob not in ("ALL", "AUTO", "HOME", "WL", "TL", "UL", "HLTH"):
        raise SystemExit(f"lob={lob}: %parmv(lob, _val=ALL AUTO HOME WL TL UL HLTH)")
    return [f"SET val_date = '{valDate}'::DATE", f"SET lob = '{lob}'"]


def connectSnowflake():
    import snowflake.connector
    snowflake.connector.paramstyle = "qmark"
    secret = os.environ.get("SNOWFLAKE_PAT") or os.environ.get("SNOWFLAKE_PASSWORD")
    missing = [v for v in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER") if not os.environ.get(v)]
    if missing or not secret:
        raise SystemExit(
            "Snowflake engine needs SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER and "
            "SNOWFLAKE_PAT/SNOWFLAKE_PASSWORD (or use --engine duckdb)"
        )
    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=secret,
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
    )
    database = os.environ.get("SNOWFLAKE_DATABASE", "SAS_MIG_JOB06_PV")
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    cur.execute(f"USE DATABASE {database}")
    return conn, cur, "?", {"database": database, "warehouse": conn.warehouse}


def connectDuckdb():
    import duckdb
    conn = duckdb.connect(database=":memory:")
    return conn, conn.cursor(), "?", {"duckdb": duckdb.__version__}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=["snowflake", "duckdb"],
                        default="snowflake" if (os.environ.get("SNOWFLAKE_PAT") or os.environ.get("SNOWFLAKE_PASSWORD")) else "duckdb")
    parser.add_argument("--val-date", default="2024-01-31", help="SAS CURR_DT as YYYY-MM-DD (default 2024-01-31)")
    parser.add_argument("--lob", default="ALL", help="%%policy_valuation(lob=) parameter (default ALL)")
    parser.add_argument("--out", default=scenarioDir, help="output directory for <LIB>__<TABLE>.csv")
    args = parser.parse_args()

    with open(sqlFile, encoding="utf-8") as fh:
        sqlText = fh.read()
    statements = sessionVariableStatements(args.val_date, args.lob) + splitStatements(sqlText)
    if args.engine == "duckdb":
        shimmed = "\n;\n".join(statements)
        for pattern, replacement in DUCKDB_SHIMS:
            shimmed = re.sub(pattern, replacement, shimmed)
        statements = [s.strip() for s in shimmed.split("\n;\n") if s.strip()]
        conn, cur, placeholder, engineInfo = connectDuckdb()
    else:
        conn, cur, placeholder, engineInfo = connectSnowflake()

    inputCounts = loadInputs(cur, placeholder)
    for stmt in statements:
        cur.execute(stmt)

    os.makedirs(args.out, exist_ok=True)
    outputCounts = {t: exportTable(cur, t, k, args.out) for t, k in OUTPUT_TABLES.items()}
    conn.close()

    manifest = {
        "engine": args.engine, **engineInfo,
        "sql": os.path.relpath(sqlFile, repoRoot),
        "val_date": args.val_date, "lob": args.lob,
        "run_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input_rows": inputCounts, "output_rows": outputCounts,
    }
    with open(os.path.join(args.out, "run_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(json.dumps(manifest, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
