"""Load immutable SAS datasets, execute JOB03, and measure Snowflake parity."""

import argparse
import csv
import hashlib
import json
import os
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import snowflake.connector
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[2]
KEYS = ["CUSTOMER_ID", "ACCOUNT_ID", "REPORTING_MONTH_YYYYMM"]
TABLES = {
    "CUST_ACCOUNTS": [
        ("CUSTOMER_ID", "NUMBER(38,0)"), ("ACCOUNT_ID", "VARCHAR"),
        ("ACCOUNT_TYPE", "VARCHAR"), ("IS_ACTIVE", "VARCHAR"),
        ("START_DATE", "DATE"), ("END_DATE", "DATE"),
    ],
    "DAILY_BALANCE": [
        ("CUSTOMER_ID", "NUMBER(38,0)"), ("ACCOUNT_ID", "VARCHAR"),
        ("DATE", "DATE"), ("END_OF_DAY_BALANCE", "FLOAT"), ("MONTH", "DATE"),
    ],
    "MONTHLY_AMB": [
        ("CUSTOMER_ID", "NUMBER(38,0)"), ("ACCOUNT_ID", "VARCHAR"),
        ("REPORTING_MONTH_YYYYMM", "NUMBER(6,0)"),
        ("AVERAGE_MONTHLY_BALANCE", "FLOAT"), ("DATE_COMPUTED", "DATE"),
    ],
}


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise argparse.ArgumentTypeError(f"Invalid unquoted identifier: {value!r}")
    return value.upper()


def sas_value(value, sql_type: str):
    if pd.isna(value):
        return None
    if sql_type == "DATE":
        stamp = pd.Timestamp(value)
        if not isinstance(stamp, pd.Timestamp):
            raise ValueError("Invalid SAS date")
        if stamp != stamp.normalize():
            raise ValueError("DATE input contains a non-midnight timestamp")
        return stamp.date()
    if sql_type.startswith("NUMBER"):
        number = float(value)
        if not number.is_integer() or abs(number) > 2**53:
            raise ValueError("SAS identifier is not a losslessly representable integer")
        return int(number)
    if sql_type == "FLOAT":
        return float(value)
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def read_sas(table: str):
    path = ROOT / "sample_data" / f"{table}.sas7bdat"
    frame = pd.read_sas(path, format="sas7bdat", encoding=None)
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("Expected an in-memory SAS DataFrame")
    if list(frame.columns) != [column.lower() for column, _ in TABLES[table]]:
        raise ValueError(f"Unexpected SAS columns for {table}")
    rows = [tuple(sas_value(value, sql_type) for value, (_, sql_type) in zip(row, TABLES[table]))
            for row in frame.itertuples(index=False, name=None)]
    return rows, hashlib.sha256(path.read_bytes()).hexdigest()


def connect(args):
    key = serialization.load_pem_private_key(os.environ["SFNFR_PRIVATE_KEY"].encode(), password=None)
    return snowflake.connector.connect(
        account=os.environ["SFNFR_ACCOUNT"], user=os.environ["SFNFR_USER"],
        role=os.environ["SFNFR_ROLE"], authenticator="SNOWFLAKE_JWT",
        private_key=key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()),
        warehouse="WH_SAS_AMB", database=args.database, schema=args.target_schema,
        paramstyle="qmark", session_parameters={"QUERY_TAG": "sas_monthly_amb_dual_run"},
    )


def namespaces(args):
    return tuple(f"{args.database}.{schema}" for schema in
                 (args.source_schema, args.legacy_schema, args.target_schema))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def load(cur, args):
    source, legacy, _ = namespaces(args)
    manifest = {}
    for table, schema in TABLES.items():
        rows, digest = read_sas(table)
        fqn = f"{legacy if table == 'MONTHLY_AMB' else source}.{table}"
        columns = ", ".join(f'"{name}" {sql_type}' for name, sql_type in schema)
        cur.execute(f"CREATE OR REPLACE TABLE {fqn} ({columns})")
        placeholders = ", ".join("?" for _ in schema)
        for offset in range(0, len(rows), 5000):
            cur.executemany(f"INSERT INTO {fqn} VALUES ({placeholders})", rows[offset:offset + 5000])
        count = cur.execute(f"SELECT COUNT(*) FROM {fqn}").fetchone()[0]
        if count != len(rows):
            raise RuntimeError(f"Load row count mismatch: {fqn}")
        manifest[table] = {"table": fqn, "rows": count, "sas_sha256": digest}
    write_json(args.evidence_dir / "load.json", manifest)
    print(json.dumps(manifest, indent=2))


def run_job(cur, args):
    source, _, target = namespaces(args)
    procedure = f"{target}.SP_JOB03_CALC_AMB"
    cur.execute("SET AMB_PROCEDURE = ?", (procedure,))
    cur.execute((ROOT / "snowflake_sql" / "JOB03_CALC_AMB.sql").read_text())
    cur.execute("CALL IDENTIFIER(?)(?, ?, ?, ?)",
                (procedure, source, target, args.reporting_month, args.run_date))
    print(f"Executed JOB03_CALC_AMB: {cur.fetchone()[0]} (query {cur.sfqid})")


def canonical_expression() -> str:
    parts = []
    for column, sql_type in TABLES["MONTHLY_AMB"]:
        if sql_type == "DATE":
            value = f"TO_CHAR({column}, 'YYYY-MM-DD')"
        elif sql_type.startswith("NUMBER") or sql_type == "FLOAT":
            value = f"TO_VARCHAR(ROUND({column}::NUMBER(38,6), 6))"
        else:
            value = f"TRIM({column})"
        parts.append(f"COALESCE({value}, '\\\\N')")
    return " || '|' || ".join(parts)


def profile(cur, table: str):
    result = {}
    for column, sql_type in TABLES["MONTHLY_AMB"]:
        sum_expr = f"SUM({column})" if sql_type.startswith("NUMBER") or sql_type == "FLOAT" else "NULL"
        row = cur.execute(
            f"SELECT COUNT(*), COUNT(*) - COUNT({column}), COUNT(DISTINCT {column}), "
            f"MIN({column}), MAX({column}), {sum_expr}, "
            f"MIN(LENGTH(TO_VARCHAR({column}))), MAX(LENGTH(TO_VARCHAR({column}))) FROM {table}"
        ).fetchone()
        if row[5] is not None:
            row = (*row[:5], round(Decimal(str(row[5])), 6), *row[6:])
        result[column] = dict(zip(
            ["count", "nulls", "distinct", "min", "max", "sum", "min_length", "max_length"], row
        ))
    return result


def matches_batch(column_profile, reporting_month: int, run_date: date) -> bool:
    for column, expected in [("REPORTING_MONTH_YYYYMM", reporting_month), ("DATE_COMPUTED", run_date)]:
        stats = column_profile[column]
        if stats["nulls"] or (stats["count"] and (stats["min"] != expected or stats["max"] != expected)):
            return False
    return True


def compare(cur, args):
    source, legacy, target = namespaces(args)
    expression, keys, queries = canonical_expression(), ", ".join(KEYS), []

    def query(sql):
        queries.append(sql + ";")
        return cur.execute(sql)

    for side, namespace in [("LEGACY", legacy), ("NEW", target)]:
        query(f"CREATE OR REPLACE TEMP VIEW {target}.AMB_{side}_CANONICAL AS "
              f"SELECT {keys}, {expression} AS CANONICAL_ROW, MD5({expression}) AS ROW_HASH "
              f"FROM {namespace}.MONTHLY_AMB")
    left, right = (f"{target}.AMB_{side}_CANONICAL" for side in ("LEGACY", "NEW"))
    checksums = [query(f"SELECT COUNT(*), HASH_AGG(CANONICAL_ROW) FROM {view}").fetchone()
                 for view in (left, right)]
    join = " AND ".join(f"l.{key} IS NOT DISTINCT FROM n.{key}" for key in KEYS)
    diff_sql = (f"SELECT l.CANONICAL_ROW AS LEGACY_ROW, n.CANONICAL_ROW AS NEW_ROW "
                f"FROM {left} l FULL OUTER JOIN {right} n ON {join} "
                "WHERE l.ROW_HASH IS DISTINCT FROM n.ROW_HASH")
    differences = query(diff_sql).fetchall()
    # Grouped multiplicities implement EXCEPT ALL (plain EXCEPT loses duplicates).
    multiset = []
    for a, b in [(left, right), (right, left)]:
        multiset.append(query(
            "SELECT COALESCE(SUM(GREATEST(a.N - COALESCE(b.N, 0), 0)), 0) FROM "
            f"(SELECT CANONICAL_ROW, COUNT(*) N FROM {a} GROUP BY CANONICAL_ROW) a LEFT JOIN "
            f"(SELECT CANONICAL_ROW, COUNT(*) N FROM {b} GROUP BY CANONICAL_ROW) b "
            "ON a.CANONICAL_ROW = b.CANONICAL_ROW"
        ).fetchone()[0])
    duplicates = [query(f"SELECT COUNT(*) FROM (SELECT {keys} FROM {view} "
                        f"GROUP BY {keys} HAVING COUNT(*) > 1)").fetchone()[0] for view in (left, right)]
    profiles = [profile(cur, f"{namespace}.MONTHLY_AMB") for namespace in (legacy, target)]
    batch_matches = all(matches_batch(item, args.reporting_month, args.run_date) for item in profiles)
    rows_in = sum(query(f"SELECT COUNT(*) FROM {source}.{table}").fetchone()[0]
                  for table in ("CUST_ACCOUNTS", "DAILY_BALANCE"))
    result = {
        "table": "MONTHLY_AMB", "legacy_fqn": f"{legacy}.MONTHLY_AMB",
        "new_fqn": f"{target}.MONTHLY_AMB", "key_columns": [key.lower() for key in KEYS],
        "rows_in": rows_in, "legacy_rows": checksums[0][0], "new_rows": checksums[1][0],
        "mismatches": len(differences), "legacy_checksum": str(checksums[0][1]),
        "new_checksum": str(checksums[1][1]), "multiset_legacy_only": multiset[0],
        "multiset_new_only": multiset[1], "duplicate_key_groups": duplicates,
        "profiles_match": profiles[0] == profiles[1],
        "batch_parameters_match": batch_matches,
        "legacy_profile": profiles[0], "new_profile": profiles[1],
        "mismatch_samples": differences[:20], "reporting_month": args.reporting_month,
        "run_date": args.run_date.isoformat(),
    }
    passed = (checksums[0] == checksums[1] and not differences and not any(multiset)
              and not any(duplicates) and profiles[0] == profiles[1] and batch_matches)
    result["status"] = "PASS" if passed else "FAIL"
    write_json(args.evidence_dir / "parity.json", result)
    (args.evidence_dir / "canonical_diff.sql").write_text("\n\n".join(queries) + "\n")
    args.scenario_dir.mkdir(parents=True, exist_ok=True)
    columns = ", ".join(column for column, _ in TABLES["MONTHLY_AMB"])
    rows = cur.execute(f"SELECT {columns} FROM {target}.MONTHLY_AMB ORDER BY {keys}").fetchall()
    with (args.scenario_dir / "MONTHLY_AMB.csv").open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(column.lower() for column, _ in TABLES["MONTHLY_AMB"])
        writer.writerows(rows)
    print(json.dumps({key: value for key, value in result.items() if "profile" not in key},
                     indent=2, default=str))
    if not passed:
        raise SystemExit("Snowflake row-level parity FAILED; inspect parity.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["load", "run", "compare", "all"])
    parser.add_argument("--database", type=identifier, default="SFNFR_20261104")
    parser.add_argument("--source-schema", type=identifier, default="SAS_AMB_SRC_SFNFR_20261104")
    parser.add_argument("--legacy-schema", type=identifier, default="SAS_AMB_LEGACY_SFNFR_20261104")
    parser.add_argument("--target-schema", type=identifier, default="SAS_AMB_SF_SFNFR_20261104")
    parser.add_argument("--reporting-month", type=int, default=202507)
    parser.add_argument("--run-date", type=date.fromisoformat, default=date(2025, 8, 1))
    parser.add_argument("--scenario-dir", type=Path,
                        default=ROOT / "sample_data" / "Scenario_SFNFR_20261104")
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / "evidence" / "MONTHLY_AMB")
    args = parser.parse_args()
    date.fromisoformat(f"{args.reporting_month // 100:04d}-{args.reporting_month % 100:02d}-01")
    if len(set(namespaces(args))) != 3:
        parser.error("SRC, LEGACY and SF must be distinct namespaces")
    with connect(args) as connection, connection.cursor() as cur:
        if args.action in ("load", "all"):
            load(cur, args)
        if args.action in ("run", "all"):
            run_job(cur, args)
        if args.action in ("compare", "all"):
            compare(cur, args)


if __name__ == "__main__":
    main()
