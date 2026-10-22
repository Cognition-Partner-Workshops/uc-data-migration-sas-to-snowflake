#!/usr/bin/env python3
"""Run a converted Snowflake SQL job against a scenario and export its output.

Loads the scenario's input CSVs (sample_data/<Scenario>/) into an isolated
Snowflake schema, executes snowflake_sql/<JOB>.sql with ``STAGING.`` rewritten
to that schema, and writes the output table back to
sample_data/<Scenario>/<TABLE>.csv so verify/reconcile.py can gate it.

Each scenario gets its own schema (SAS_MIG_<SCENARIO>), so concurrent runs
do not collide and the durable source datasets are never touched.

Auth: key-pair via SNOWFLAKE_DEMO_ACCOUNT / SNOWFLAKE_DEMO_USER /
SNOWFLAKE_DEMO_PRIVATE_KEY (PEM). Database defaults to INSURANCE_DEMO
(override with SNOWFLAKE_DATABASE).

Usage:
    python verify/run_snowflake_job.py --job JOB03_CALC_AMB \
        --inputs CUST_ACCOUNTS,DAILY_BALANCE --output MONTHLY_AMB \
        --scenario Scenario1 --run-date 2025-08-01
"""
import argparse
import os
import re
import sys

import pandas as pd
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from snowflake.connector.pandas_tools import write_pandas

repoRoot = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sampleDir = os.path.join(repoRoot, "sample_data")
sqlDir = os.path.join(repoRoot, "snowflake_sql")


def connect():
    pem = os.environ["SNOWFLAKE_DEMO_PRIVATE_KEY"].encode()
    privateKey = serialization.load_pem_private_key(pem, password=None)
    privateKeyDer = privateKey.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_DEMO_ACCOUNT"],
        user=os.environ["SNOWFLAKE_DEMO_USER"],
        private_key=privateKeyDer,
        database=os.environ.get("SNOWFLAKE_DATABASE", "INSURANCE_DEMO"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "DEMO_WH"),
    )


def load_inputs(conn, schema: str, scenario: str, tables: list) -> None:
    for table in tables:
        path = os.path.join(sampleDir, scenario, f"{table}.csv")
        df = pd.read_csv(path)
        df.columns = [c.upper() for c in df.columns]
        for col in df.columns:
            if col.endswith("DATE") or col == "DATE":
                df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
        conn.cursor().execute(f"DROP TABLE IF EXISTS {schema}.{table}")
        _, _, rowCount, _ = write_pandas(
            conn, df, table, schema=schema, auto_create_table=True,
            quote_identifiers=False,
        )
        print(f"  loaded {schema}.{table}: {rowCount} rows")


def run_job(conn, schema: str, job: str, runDate: str) -> None:
    with open(os.path.join(sqlDir, f"{job}.sql")) as fh:
        sql = fh.read().replace("STAGING.", f"{schema}.")
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {schema}.MONTHLY_AMB")
    if runDate:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", runDate):
            raise SystemExit(f"--run-date must be YYYY-MM-DD, got {runDate!r}")
        cur.execute(f"SET run_date = TO_DATE('{runDate}')")
    for statement in conn.execute_string(sql):
        print(f"  {statement.sfqid}: {statement.rowcount} rows")


def export_output(conn, schema: str, scenario: str, table: str) -> str:
    df = conn.cursor().execute(f"SELECT * FROM {schema}.{table} ORDER BY 1, 2, 3").fetch_pandas_all()
    df.columns = [c.lower() for c in df.columns]
    outPath = os.path.join(sampleDir, scenario, f"{table}.csv")
    df.to_csv(outPath, index=False)
    print(f"  exported {len(df)} rows -> {os.path.relpath(outPath, repoRoot)}")
    return outPath


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", required=True, help="SQL file name under snowflake_sql/ (no extension)")
    parser.add_argument("--inputs", required=True, help="Comma-separated input tables to load from the scenario")
    parser.add_argument("--output", required=True, help="Output table to export back to the scenario")
    parser.add_argument("--scenario", required=True, help="Scenario directory (e.g. Scenario1)")
    parser.add_argument("--run-date", default=None, help="Value for $run_date (YYYY-MM-DD); defaults to today")
    parser.add_argument("--keep-schema", action="store_true", help="Leave the scenario schema in place after the run")
    args = parser.parse_args()

    schema = f"SAS_MIG_{args.scenario.upper()}"
    conn = connect()
    try:
        conn.cursor().execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        print(f"Namespace: {conn.database}.{schema}")
        load_inputs(conn, schema, args.scenario, args.inputs.split(","))
        print(f"Running {args.job} (run_date={args.run_date or 'CURRENT_DATE'})")
        run_job(conn, schema, args.job, args.run_date)
        export_output(conn, schema, args.scenario, args.output)
    finally:
        if not args.keep_schema:
            conn.cursor().execute(f"DROP SCHEMA IF EXISTS {schema}")
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
