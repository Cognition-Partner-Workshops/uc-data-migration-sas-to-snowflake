"""
run_local.py — reproducible Snowflake-side run of JOB02 for the golden reconciliation.

What it does
  1. Loads the JOB02 inputs into a PRIVATE Snowflake namespace
     (<DATABASE>.<PREFIX>_RAW_BANK / _STG_BANK / _CURATED), typed exactly the
     way Data/load_seed_data.sas types them in SAS:
       RAW_BANK.TXN_FEED_20240131   <- SOURCE Data/csv/raw_bank/TXN_FEED_20240131.csv
       CURATED.DAILY_TRANSACTIONS   <- SOURCE Data/csv/curated/DAILY_TRANSACTIONS.csv (history)
       STG_BANK.CUST_ACCOUNTS_DAILY <- golden stg_bank__cust_accounts_daily.csv (JOB01 output)
  2. Runs snowpark/JOB02_daily_transaction_processing.py against that namespace.
  3. Exports the three output tables to <scenario>/<LIB>__<TABLE>.csv for
     verify/reconcile.py --sas-golden.

Engine
  --engine snowflake (default): real Snowflake via key-pair auth, env vars
      SNOWFLAKE_DEMO_ACCOUNT, SNOWFLAKE_DEMO_USER, SNOWFLAKE_DEMO_PRIVATE_KEY
      (PEM), optional SNOWFLAKE_DEMO_PRIVATE_KEY_PASSPHRASE; role DEMO_ENGINEER,
      warehouse DEMO_WH, database INSURANCE_DEMO (override with --database).
  --engine local: Snowpark local-testing emulator (no credentials); see the
      docs for which functions the emulator lacks.

Usage
  python3 sample_data/Scenario_daily_transaction_processing/run_local.py \
      --source-repo ~/repos/ts-sas-legacy-analytics --golden ~/golden
"""
import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from snowflake.snowpark import Session
from snowflake.snowpark.types import (
    DateType,
    DoubleType,
    StringType,
    StructField,
    StructType,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "snowpark"))
from JOB02_daily_transaction_processing import daily_transaction_processing

SAS_EPOCH = date(1960, 1, 1)
OUTPUT_TABLES = ["DAILY_TRANSACTIONS", "TXN_ANOMALIES", "RUNNING_BALANCES"]

# %load_txn in Data/load_seed_data.sas: length $12 $12 $4 $8 $4 $60 $3, dates date9., amount numeric.
# Listed in CSV column order; TXN_PDV_ORDER is the SAS variable order the
# `length` statement produces (char variables first), which is the column
# order of every downstream table and of the golden exports.
TXN_SCHEMA = [
    ("TRANSACTION_ID", "char", 12),
    ("ACCOUNT_ID", "char", 12),
    ("TRANSACTION_DATE", "date9", None),
    ("TRANSACTION_TYPE", "char", 4),
    ("TRANSACTION_AMOUNT", "num", None),
    ("CHANNEL", "char", 8),
    ("MERCHANT_CATEGORY", "char", 4),
    ("DESCRIPTION", "char", 60),
    ("POST_DATE", "date9", None),
    ("CURRENCY_CODE", "char", 3),
]
TXN_PDV_ORDER = [
    "TRANSACTION_ID", "ACCOUNT_ID", "TRANSACTION_TYPE", "CHANNEL", "MERCHANT_CATEGORY",
    "DESCRIPTION", "CURRENCY_CODE", "TRANSACTION_DATE", "TRANSACTION_AMOUNT", "POST_DATE",
]

# STG_BANK.CUST_ACCOUNTS_DAILY as JOB01 builds it from ORA_DW.CUST_ACCOUNTS +
# ORA_DW.CUST_DEMOGRAPHICS (lengths from Data/load_seed_data.sas); the golden
# export carries unformatted SAS dates as day integers.
ACCT_SCHEMA = [
    ("ACCOUNT_ID", "char", 12),
    ("CUSTOMER_ID", "char", 10),
    ("ACCOUNT_TYPE", "char", 4),
    ("ACCOUNT_STATUS", "char", 1),
    ("OPEN_DATE", "sasdate", None),
    ("CLOSE_DATE", "sasdate", None),
    ("CURRENT_BALANCE", "num", None),
    ("AVAILABLE_BALANCE", "num", None),
    ("CREDIT_LIMIT", "num", None),
    ("INTEREST_RATE", "num", None),
    ("BRANCH_ID", "char", 6),
    ("OFFICER_ID", "char", 6),
    ("LAST_ACTIVITY_DATE", "sasdate", None),
    ("FIRST_NAME", "char", 30),
    ("LAST_NAME", "char", 30),
    ("SSN_HASH", "char", 32),
    ("DATE_OF_BIRTH", "sasdate", None),
    ("CUSTOMER_SEGMENT", "char", 4),
    ("RISK_RATING", "num", None),
    ("REGION_CODE", "char", 2),
    ("PRIMARY_EMAIL", "char", 80),
    ("PHONE_NUMBER", "char", 16),
    ("ACCT_AGE_MONTHS", "num", None),
    ("DAYS_INACTIVE", "num", None),
    ("UTILIZATION_PCT", "num", None),
    ("DORMANCY_FLAG", "char", 1),
    ("HIGH_BALANCE_FLAG", "char", 1),
    ("SNAPSHOT_DATE", "sasdate", None),
    ("LOAD_TIMESTAMP", "num", None),
]


def _sas_char(value, length):
    """SAS character variable: blank -> missing, longer values truncated (truncover)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value)
    return text[:length] if text.strip() != "" else None


def _sas_num(value):
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    return float(value)


def _sas_date9(value):
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    return pd.to_datetime(str(value), format="%d%b%Y").date()


def _sas_date_int(value):
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    return SAS_EPOCH + timedelta(days=int(float(value)))


CONVERTERS = {"char": _sas_char, "num": _sas_num, "date9": _sas_date9, "sasdate": _sas_date_int}
SNOWPARK_TYPES = {
    "char": lambda n: StringType(n),
    "num": lambda n: DoubleType(),
    "date9": lambda n: DateType(),
    "sasdate": lambda n: DateType(),
}


def load_csv_as_sas(session: Session, csvPath: Path, schema, targetTable: str, pdvOrder=None) -> int:
    """Read a CSV with every field as text and type it the way the SAS loader does."""
    frame = pd.read_csv(csvPath, dtype=str, keep_default_na=False)
    if list(frame.columns) != [name for name, _, _ in schema]:
        raise ValueError(f"{csvPath}: columns differ from the SAS loader's input statement")
    if pdvOrder:
        position = {name: i for i, (name, _, _) in enumerate(schema)}
        schema = [schema[position[name]] for name in pdvOrder]
        frame = frame[pdvOrder]
    rows = []
    for record in frame.itertuples(index=False):
        row = []
        for (name, kind, length), raw in zip(schema, record):
            row.append(CONVERTERS[kind](raw, length) if kind == "char" else CONVERTERS[kind](raw))
        rows.append(row)
    structType = StructType(
        [StructField(name, SNOWPARK_TYPES[kind](length), nullable=True) for name, kind, length in schema]
    )
    session.create_dataframe(rows, schema=structType).write.mode("overwrite").save_as_table(targetTable)
    return len(rows)


def install_local_shims() -> None:
    """Snowpark local-testing emulator lacks a few scalar functions the
    conversion uses; patch them with pandas equivalents (Snowflake semantics).
    Only active with --engine local; the conversion file itself is untouched.
    """
    from snowflake.snowpark.mock import ColumnEmulator, ColumnType, patch
    from snowflake.snowpark.types import StringType as MockStringType

    @patch("trim")
    def mock_trim(column: ColumnEmulator, trimChars: ColumnEmulator = None) -> ColumnEmulator:
        result = column.apply(lambda v: None if v is None else str(v).strip())
        return ColumnEmulator(data=result, sf_type=ColumnType(MockStringType(), column.sf_type.nullable))

    from snowflake.snowpark.mock import _functions as mockFunctions
    from snowflake.snowpark.types import _NumericType

    originalToChar = mockFunctions.mock_to_char

    @patch("to_char")
    def mock_to_char_numeric(column: ColumnEmulator, fmt: ColumnEmulator = None, **kwargs) -> ColumnEmulator:
        # the emulator rejects TO_CHAR(<number>, '<format>'); only the one
        # format the conversion uses ($ + thousands separators + 2 decimals,
        # i.e. SAS dollar18.2) is emulated here.  Everything else is delegated.
        if fmt is not None and isinstance(column.sf_type.datatype, _NumericType):
            result = column.apply(
                lambda v: None if v is None or pd.isna(v) else ("-$" if v < 0 else "$") + f"{abs(float(v)):,.2f}"
            )
            return ColumnEmulator(data=result, sf_type=ColumnType(MockStringType(), column.sf_type.nullable))
        return originalToChar(column, fmt, **kwargs) if fmt is not None else originalToChar(column, **kwargs)

    @patch("abs")
    def mock_abs_null_safe(column: ColumnEmulator) -> ColumnEmulator:
        # the emulator's abs() raises on NULL; Snowflake (and SAS) return NULL/missing
        result = column.apply(lambda v: None if v is None or pd.isna(v) else abs(v))
        return ColumnEmulator(data=result, sf_type=column.sf_type)


def build_session(engine: str, database: str) -> Session:
    if engine == "local":
        install_local_shims()
        return Session.builder.config("local_testing", True).create()
    from cryptography.hazmat.primitives import serialization

    passphrase = os.environ.get("SNOWFLAKE_DEMO_PRIVATE_KEY_PASSPHRASE") or None
    privateKey = serialization.load_pem_private_key(
        os.environ["SNOWFLAKE_DEMO_PRIVATE_KEY"].encode(),
        password=passphrase.encode() if passphrase else None,
    )
    der = privateKey.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return Session.builder.configs(
        {
            "account": os.environ["SNOWFLAKE_DEMO_ACCOUNT"],
            "user": os.environ["SNOWFLAKE_DEMO_USER"],
            "private_key": der,
            "role": os.environ.get("SNOWFLAKE_ROLE", "DEMO_ENGINEER"),
            "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE", "DEMO_WH"),
            "database": database,
        }
    ).create()


def export_table(session: Session, table: str, outPath: Path) -> int:
    """Dump a table the way docker/sas/export_golden.sas (PROC EXPORT) does:
    upper-case headers and unformatted SAS dates as day integers since
    1960-01-01, so the golden and Snowflake CSVs are directly comparable."""
    frame = session.table(table).to_pandas()
    frame.columns = [c.upper() for c in frame.columns]
    for column in frame.columns:
        values = frame[column].dropna()
        if len(values) and all(isinstance(v, date) for v in values):
            frame[column] = frame[column].map(lambda v: None if v is None or pd.isna(v) else (v - SAS_EPOCH).days)
            frame[column] = frame[column].astype("Int64")
    frame.to_csv(outPath, index=False)
    return len(frame)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--txn-date", default="2024-01-31", help="CURR_DT as YYYY-MM-DD")
    parser.add_argument("--source-repo", default=os.path.expanduser("~/repos/ts-sas-legacy-analytics"))
    parser.add_argument("--golden", default=os.path.expanduser("~/golden"), help="untarred sas_golden dir")
    parser.add_argument("--engine", choices=["snowflake", "local"], default="snowflake")
    parser.add_argument("--database", default=os.environ.get("SNOWFLAKE_DATABASE", "INSURANCE_DEMO"))
    parser.add_argument("--schema-prefix", default="DEV_JOB02", help="private namespace prefix")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent))
    args = parser.parse_args()

    txnDt = date.fromisoformat(args.txn_date)
    feedName = f"TXN_FEED_{txnDt.strftime('%Y%m%d')}"
    sourceRepo = Path(args.source_repo)
    golden = Path(args.golden)
    outDir = Path(args.out)
    outDir.mkdir(parents=True, exist_ok=True)

    session = build_session(args.engine, args.database)
    qualify = (lambda lib: f"{args.database}.{args.schema_prefix}_{lib}") if args.engine == "snowflake" else (
        lambda lib: f"{args.schema_prefix}_{lib}"
    )
    rawBank, stgBank, curated = qualify("RAW_BANK"), qualify("STG_BANK"), qualify("CURATED")

    if args.engine == "snowflake":
        for schema in (rawBank, stgBank, curated):
            session.sql(f"CREATE SCHEMA IF NOT EXISTS {schema}").collect()
        # fresh run every time: SAS starts from the seeded history and no anomalies table
        session.sql(f"DROP TABLE IF EXISTS {curated}.TXN_ANOMALIES").collect()
        session.sql(f"DROP TABLE IF EXISTS {curated}.RUNNING_BALANCES").collect()

    nFeed = load_csv_as_sas(
        session, sourceRepo / "Data/csv/raw_bank" / f"{feedName}.csv", TXN_SCHEMA, f"{rawBank}.{feedName}", TXN_PDV_ORDER
    )
    nHist = load_csv_as_sas(
        session, sourceRepo / "Data/csv/curated/DAILY_TRANSACTIONS.csv", TXN_SCHEMA, f"{curated}.DAILY_TRANSACTIONS", TXN_PDV_ORDER
    )
    nAcct = load_csv_as_sas(
        session, golden / "stg_bank__cust_accounts_daily.csv", ACCT_SCHEMA, f"{stgBank}.CUST_ACCOUNTS_DAILY"
    )
    print(f"Loaded inputs: feed={nFeed} history={nHist} accounts={nAcct} into {args.database}.{args.schema_prefix}_*")

    result = daily_transaction_processing(session, args.txn_date, rawBank=rawBank, stgBank=stgBank, curated=curated)
    print(result)
    if result.startswith("ERROR"):
        return 1

    for table in OUTPUT_TABLES:
        n = export_table(session, f"{curated}.{table}", outDir / f"CURATED__{table}.csv")
        print(f"Exported CURATED.{table}: {n} rows -> {outDir / f'CURATED__{table}.csv'}")
    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
