#!/usr/bin/env python3
"""Reproducible runner for JOB05_CREDIT_RISK_SCORING.sql.

Loads the program's inputs exactly as the SAS estate types them, executes
snowflake_sql/JOB05_CREDIT_RISK_SCORING.sql and exports every output table
as sample_data/Scenario_credit_risk_scoring/<LIB>__<TABLE>.csv for
verify/reconcile.py --sas-golden.

Inputs
  STG_BANK.CUST_ACCOUNTS_DAILY  <golden>/stg_bank__cust_accounts_daily.csv
                                (upstream JOB01 output; SAS date integers)
  ORA_DW.BUREAU_SCORES          <estate>/Data/csv/oracle_dw/BUREAU_SCORES.csv
  ORA_DW.PAYMENT_HISTORY        <estate>/Data/csv/oracle_dw/PAYMENT_HISTORY.csv
  ORA_DW.COLLATERAL             <estate>/Data/csv/oracle_dw/COLLATERAL.csv
  Column types follow Data/load_seed_data.sas and load_customer_accounts.sas:
  character columns keep their SAS $n lengths, every SAS numeric is an
  8-byte double (FLOAT), date9.-informatted columns are DATE.

Engines
  snowflake (default when SNOWFLAKE_DEMO_ACCOUNT is set): the SQL runs
    unmodified on Snowflake inside private schemas
    INSURANCE_DEMO.<prefix>_{STG_BANK,ORA_DW,CURATED,REPORTS}; the only
    rewrite is that schema prefix (the equivalent of a SAS libname remap).
  duckdb (fallback, --engine duckdb): a documented dialect shim replaces the
    two session variables with literals, CURRENT_TIMESTAMP() with now() and
    FLOAT with DOUBLE (DuckDB FLOAT is 32-bit; Snowflake FLOAT is 64-bit).

Usage
  python3 sample_data/Scenario_credit_risk_scoring/run_local.py \
      --golden ~/golden --estate ~/repos/ts-sas-legacy-analytics
"""
import argparse
import datetime as dt
import os
import re
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
SQL_FILE = os.path.join(REPO_ROOT, "snowflake_sql", "JOB05_CREDIT_RISK_SCORING.sql")

SCORE_DATE = dt.date(2024, 1, 31)  # CURR_DT=31JAN2024 (Config/autoexec_local.sas)
MODEL_ID = "CRM-2023-Q4-v2"
SAS_EPOCH = dt.date(1960, 1, 1)
SCHEMAS = ("STG_BANK", "ORA_DW", "CURATED", "REPORTS")
OUTPUTS = {
    "CURATED.RISK_SCORES": "ACCOUNT_ID",
    "CURATED.RISK_MIGRATION": "ACCOUNT_ID",
    "REPORTS.RISK_SUMMARY": "ACCOUNT_TYPE, NEW_RISK_RATING",
}

# (column, SAS type) per input table. "$n" = character length n,
# "num" = SAS numeric (double), "date" = numeric with a date informat/format.
CUST_ACCOUNTS_DAILY_COLS = [
    ("ACCOUNT_ID", "$12"), ("CUSTOMER_ID", "$10"), ("ACCOUNT_TYPE", "$4"),
    ("ACCOUNT_STATUS", "$1"), ("OPEN_DATE", "date"), ("CLOSE_DATE", "date"),
    ("CURRENT_BALANCE", "num"), ("AVAILABLE_BALANCE", "num"), ("CREDIT_LIMIT", "num"),
    ("INTEREST_RATE", "num"), ("BRANCH_ID", "$6"), ("OFFICER_ID", "$6"),
    ("LAST_ACTIVITY_DATE", "date"), ("FIRST_NAME", "$30"), ("LAST_NAME", "$30"),
    ("SSN_HASH", "$32"), ("DATE_OF_BIRTH", "date"), ("CUSTOMER_SEGMENT", "$4"),
    ("RISK_RATING", "num"), ("REGION_CODE", "$2"), ("PRIMARY_EMAIL", "$80"),
    ("PHONE_NUMBER", "$16"), ("ACCT_AGE_MONTHS", "num"), ("DAYS_INACTIVE", "num"),
    ("UTILIZATION_PCT", "num"), ("DORMANCY_FLAG", "$1"), ("HIGH_BALANCE_FLAG", "$1"),
    ("SNAPSHOT_DATE", "date"), ("LOAD_TIMESTAMP", "num"),
]
BUREAU_SCORES_COLS = [
    ("CUSTOMER_ID", "$10"), ("SCORE_DATE", "date"), ("FICO_SCORE", "num"),
    ("VANTAGE_SCORE", "num"), ("BUREAU_INQS_6MO", "num"), ("BUREAU_TRADES_OPEN", "num"),
    ("BUREAU_DEROGS", "num"), ("BUREAU_UTIL_PCT", "num"), ("BUREAU_OLDEST_TRADE_MO", "num"),
]
PAYMENT_HISTORY_COLS = [
    ("ACCOUNT_ID", "$12"), ("PMT_ONTIME_12MO", "num"), ("PMT_LATE_30_12MO", "num"),
    ("PMT_LATE_60_12MO", "num"), ("PMT_LATE_90_12MO", "num"),
    ("MAX_DAYS_PAST_DUE_EVER", "num"), ("MONTHS_SINCE_LAST_DPD", "num"),
    ("AVG_PMT_RATIO_12MO", "num"),
]
COLLATERAL_COLS = [
    ("ACCOUNT_ID", "$12"), ("COLLATERAL_VALUE", "num"), ("LAST_APPRAISAL_DATE", "date"),
]


def sqlType(sasType: str) -> str:
    if sasType.startswith("$"):
        return f"VARCHAR({sasType[1:]})"
    return "DATE" if sasType == "date" else "FLOAT"


def sasDateInt(value):
    """Golden exports unformatted SAS dates as days since 1960-01-01."""
    if pd.isna(value):
        return None
    return SAS_EPOCH + dt.timedelta(days=int(value))


def date9(value):
    """Seed CSVs carry date9. text (e.g. 31OCT2023)."""
    if pd.isna(value) or str(value).strip() == "":
        return None
    return dt.datetime.strptime(str(value).strip(), "%d%b%Y").date()


def typedFrame(path: str, cols: list, dateParser) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.upper() for c in df.columns]
    out = pd.DataFrame()
    for name, sasType in cols:
        raw = df[name].replace("", None)
        if sasType.startswith("$"):
            length = int(sasType[1:])
            out[name] = raw.map(lambda v, n=length: None if v is None else v[:n])
        elif sasType == "date":
            out[name] = raw.map(dateParser)
        else:
            out[name] = raw.map(lambda v: None if v is None else float(v))
    return out


def loadInputs(goldenDir: str, estateDir: str) -> dict:
    oracle = os.path.join(estateDir, "Data", "csv", "oracle_dw")
    return {
        "STG_BANK.CUST_ACCOUNTS_DAILY": (
            CUST_ACCOUNTS_DAILY_COLS,
            typedFrame(os.path.join(goldenDir, "stg_bank__cust_accounts_daily.csv"),
                       CUST_ACCOUNTS_DAILY_COLS, sasDateInt),
        ),
        "ORA_DW.BUREAU_SCORES": (
            BUREAU_SCORES_COLS,
            typedFrame(os.path.join(oracle, "BUREAU_SCORES.csv"), BUREAU_SCORES_COLS, date9),
        ),
        "ORA_DW.PAYMENT_HISTORY": (
            PAYMENT_HISTORY_COLS,
            typedFrame(os.path.join(oracle, "PAYMENT_HISTORY.csv"), PAYMENT_HISTORY_COLS, date9),
        ),
        "ORA_DW.COLLATERAL": (
            COLLATERAL_COLS,
            typedFrame(os.path.join(oracle, "COLLATERAL.csv"), COLLATERAL_COLS, date9),
        ),
    }


def splitStatements(sqlText: str) -> list:
    noComments = re.sub(r"/\*.*?\*/", "", sqlText, flags=re.DOTALL)
    noComments = re.sub(r"--[^\n]*", "", noComments)
    return [s.strip() for s in noComments.split(";") if s.strip()]


class SnowflakeEngine:
    name = "snowflake"

    def __init__(self, prefix: str):
        import snowflake.connector
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_private_key(
            os.environ["SNOWFLAKE_DEMO_PRIVATE_KEY"].encode(), password=None
        )
        self.conn = snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_DEMO_ACCOUNT"],
            user=os.environ["SNOWFLAKE_DEMO_USER"],
            private_key=key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            database=os.environ.get("SNOWFLAKE_DEMO_DATABASE", "INSURANCE_DEMO"),
            warehouse=os.environ.get("SNOWFLAKE_DEMO_WAREHOUSE", "DEMO_WH"),
        )
        self.prefix = prefix
        self.cur = self.conn.cursor()
        self.paramStyle = "%s"

    def schemaName(self, lib: str) -> str:
        return f"{self.prefix}_{lib}"

    def rewrite(self, sql: str) -> str:
        return re.sub(r"\b(" + "|".join(SCHEMAS) + r")\.", lambda m: self.schemaName(m.group(1)) + ".", sql)

    def execute(self, sql: str, params=None):
        self.cur.execute(self.rewrite(sql), params)
        return self.cur

    def executeMany(self, sql: str, rows: list):
        self.cur.executemany(self.rewrite(sql), rows)

    def setVariables(self):
        self.cur.execute("SET score_date = TO_DATE(%s)", (SCORE_DATE.isoformat(),))
        self.cur.execute("SET model_id = %s", (MODEL_ID,))

    def fetchFrame(self, sql: str) -> pd.DataFrame:
        cur = self.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

    def close(self):
        self.conn.close()


class DuckDbEngine:
    """Local fallback. Dialect shim (documented): session variables become
    literals; CURRENT_TIMESTAMP() becomes now(); FLOAT becomes DOUBLE because
    DuckDB FLOAT is 4-byte REAL whereas Snowflake FLOAT (like SAS numerics)
    is an 8-byte double. Nothing else is rewritten."""

    name = "duckdb"

    def __init__(self, prefix: str):
        import duckdb

        self.conn = duckdb.connect()
        self.paramStyle = "?"

    def schemaName(self, lib: str) -> str:
        return lib

    def rewrite(self, sql: str) -> str:
        sql = sql.replace("$score_date", f"DATE '{SCORE_DATE.isoformat()}'")
        sql = sql.replace("$model_id", f"'{MODEL_ID}'")
        sql = sql.replace("CURRENT_TIMESTAMP()", "now()")
        return re.sub(r"\bFLOAT\b", "DOUBLE", sql)

    def execute(self, sql: str, params=None):
        return self.conn.execute(self.rewrite(sql), params or [])

    def executeMany(self, sql: str, rows: list):
        self.conn.executemany(self.rewrite(sql), rows)

    def setVariables(self):
        return None

    def fetchFrame(self, sql: str) -> pd.DataFrame:
        return self.execute(sql).df()

    def close(self):
        self.conn.close()


def stageInputs(engine, inputs: dict) -> None:
    for lib in SCHEMAS:
        engine.execute(f"CREATE SCHEMA IF NOT EXISTS {engine.schemaName(lib)}")
    for table, (cols, df) in inputs.items():
        ddl = ", ".join(f"{name} {sqlType(sasType)}" for name, sasType in cols)
        engine.execute(f"CREATE OR REPLACE TABLE {table} ({ddl})")
        placeholders = ", ".join([engine.paramStyle] * len(cols))
        rows = [tuple(None if pd.isna(v) else v for v in rec) for rec in df.itertuples(index=False, name=None)]
        engine.executeMany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        n = engine.fetchFrame(f"SELECT COUNT(*) AS N FROM {table}").iloc[0, 0]
        print(f"  staged {table}: {n} rows")
    # The golden baseline is a first run: PROC APPEND created the base tables.
    for table in ("CURATED.RISK_SCORES", "CURATED.RISK_MIGRATION", "REPORTS.RISK_SUMMARY"):
        engine.execute(f"DROP TABLE IF EXISTS {table}")


def runSql(engine) -> None:
    with open(SQL_FILE, encoding="utf-8") as fh:
        statements = splitStatements(fh.read())
    engine.setVariables()
    for stmt in statements:
        engine.execute(stmt)
    print(f"  executed {len(statements)} statements from {os.path.relpath(SQL_FILE, REPO_ROOT)}")


def exportOutputs(engine, outDir: str) -> None:
    for table, orderBy in OUTPUTS.items():
        df = engine.fetchFrame(f"SELECT * FROM {table} ORDER BY {orderBy}")
        df.columns = [c.upper() for c in df.columns]
        path = os.path.join(outDir, table.replace(".", "__") + ".csv")
        df.to_csv(path, index=False, date_format="%Y-%m-%d")
        print(f"  exported {table}: {len(df)} rows -> {os.path.relpath(path, REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden", required=True, help="untarred sas_golden_31JAN2024 directory")
    parser.add_argument("--estate", default=os.path.expanduser("~/repos/ts-sas-legacy-analytics"),
                        help="ts-sas-legacy-analytics checkout (read-only seed CSVs)")
    parser.add_argument("--engine", choices=["snowflake", "duckdb"],
                        default="snowflake" if os.environ.get("SNOWFLAKE_DEMO_ACCOUNT") else "duckdb")
    parser.add_argument("--schema-prefix", default="JOB05_CRS",
                        help="private schema prefix inside the Snowflake database")
    parser.add_argument("--out", default=HERE)
    args = parser.parse_args()

    print(f"JOB05 credit_risk_scoring — engine={args.engine} score_date={SCORE_DATE} model_id={MODEL_ID}")
    inputs = loadInputs(args.golden, args.estate)
    engine = SnowflakeEngine(args.schema_prefix) if args.engine == "snowflake" else DuckDbEngine(args.schema_prefix)
    try:
        stageInputs(engine, inputs)
        runSql(engine)
        exportOutputs(engine, args.out)
    finally:
        engine.close()
    print("done")


if __name__ == "__main__":
    sys.exit(main())
