"""Load immutable SAS datasets, execute JOB03, and measure Snowflake parity."""

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import snowflake.connector
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "snowflake_sql"))
from attribution import sf_attribution as sf

WORKLOAD = "mig_sas"
EP = sf.ENDPOINTS["cs_mig_sas_monthly_balance"]
WAREHOUSE = f"{sf.warehouse_name(WORKLOAD)}_MONTHLY_BALANCE_{sf.RUN_TOKEN}"
MONITOR = sf.resource_monitor_name(WORKLOAD)
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


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def pushed_head():
    if git("status", "--porcelain"):
        raise RuntimeError("Refusing connection: HEAD must be clean")
    commit = git("rev-parse", "HEAD")
    if not git("branch", "-r", "--contains", commit):
        raise RuntimeError("Refusing connection: HEAD must be pushed to a remote-tracking branch")
    return commit


def connection_arguments(args, commit, run_id):
    params = sf.connection_params(
        EP, phase="dev", account=os.environ["SNOWFLAKE_EXT_ACCOUNT"],
        private_key_file="in-memory", commit=commit, run_id=run_id,
        session_url=os.environ["DEVIN_SESSION_URL"], role="DEVIN_EXT_ROLE",
        schema=args.target_schema,
    )
    params.pop("private_key_file")
    key = serialization.load_pem_private_key(
        os.environ["SNOWFLAKE_EXT_PRIVATE_KEY"].encode(), password=None)
    params.update(
        private_key=key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()),
        user=os.environ["SNOWFLAKE_EXT_USER"], warehouse=None,
        database=args.database, paramstyle="qmark",
    )
    return params


def connect(args):
    commit = pushed_head()
    run_id = sf.make_run_id(WORKLOAD, "dev")
    print(f"run_id={run_id}; application={sf.APPLICATION} (unregistered)")
    return snowflake.connector.connect(**connection_arguments(args, commit, run_id))


class RecordedCursor:
    def __init__(self, cursor, evidence_dir):
        self.cursor = cursor
        self.path = evidence_dir / "queries.json"
        self.queries = json.loads(self.path.read_text()) if self.path.exists() else []
        self.action = ""

    def execute(self, sql, params=None):
        try:
            self.cursor.execute(sql, params) if params is not None else self.cursor.execute(sql)
        finally:
            self.queries.append({"label": f"{self.action}: {sql.splitlines()[0]}",
                                 "query_id": self.cursor.sfqid})
            write_json(self.path, self.queries)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def sfqid(self):
        return self.cursor.sfqid


def show_rows(cur, sql):
    cur.execute(sql)
    return [dict(zip([c[0].lower() for c in cur.cursor.description], row))
            for row in cur.fetchall()]


def monitor_snapshot(cur):
    rows = show_rows(cur, f"SHOW RESOURCE MONITORS LIKE '{MONITOR}'")
    rows = [r for r in rows if r["name"].upper() == MONITOR]
    if len(rows) != 1 or Decimal(str(rows[0]["credit_quota"])) != 10:
        raise RuntimeError("Required resource monitor missing or quota is not 10")
    row = rows[0]
    fields = ("credit_quota", "notify_at", "suspend_at", "suspend_immediately_at", "end_time")
    return {field: row[field] for field in fields}


def verify_warehouse(cur, suspended=False):
    rows = show_rows(cur, f"SHOW WAREHOUSES LIKE '{WAREHOUSE}'")
    rows = [r for r in rows if r["name"].upper() == WAREHOUSE]
    if (len(rows) != 1 or str(rows[0]["resource_monitor"]).upper() != MONITOR
            or (suspended and rows[0]["state"].upper() != "SUSPENDED")):
        raise RuntimeError("Warehouse missing, monitor not attached, or warehouse not suspended")
    return rows[0]


def provision(cur, args):
    before = monitor_snapshot(cur)
    existing = show_rows(cur, f"SHOW WAREHOUSES LIKE '{WAREHOUSE}'")
    existed = any(row["name"].upper() == WAREHOUSE for row in existing)
    comment = sf.sql_string(sf.external_comment(workload=WORKLOAD, csid=EP.csid, job="JOB03_CALC_AMB"))
    cur.execute(f"CREATE WAREHOUSE IF NOT EXISTS {WAREHOUSE} WAREHOUSE_SIZE='XSMALL' "
                f"AUTO_SUSPEND=60 AUTO_RESUME=TRUE INITIALLY_SUSPENDED=TRUE COMMENT={comment}")
    rows = show_rows(cur, f"SHOW WAREHOUSES LIKE '{WAREHOUSE}'")
    rows = [row for row in rows if row["name"].upper() == WAREHOUSE]
    if before != monitor_snapshot(cur):
        raise RuntimeError("Resource monitor changed during provisioning")
    if not existed or len(rows) != 1 or str(rows[0]["resource_monitor"]).upper() != MONITOR:
        raise RuntimeError(
            "Warehouse requires resource monitor assignment by the control connection. "
            f"Ask the control connection to run: ALTER WAREHOUSE {WAREHOUSE} "
            f"SET RESOURCE_MONITOR = {MONITOR}; No compute was run.")
    warehouse = rows[0]
    if str(warehouse["size"]).upper() != "X-SMALL" or warehouse["auto_suspend"] != 60:
        raise RuntimeError("Existing warehouse must have size X-Small and auto_suspend 60; no compute was run")
    tag = sf.sql_string(sf.devin_csid_value(sf.workload_csids(WORKLOAD)))
    cur.execute(f"ALTER WAREHOUSE {WAREHOUSE} SET TAG {sf.TAG_FQN} = {tag}")
    cur.execute(f"CREATE DATABASE IF NOT EXISTS {args.database} COMMENT={comment}")
    for namespace in namespaces(args):
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {namespace} COMMENT={comment}")
    after = monitor_snapshot(cur)
    if before != after:
        raise RuntimeError("Resource monitor changed during provisioning")
    warehouse = verify_warehouse(cur)
    write_json(args.evidence_dir / "provision.json",
               {"monitor_before": before, "monitor_after": after, "warehouse": warehouse})


def namespaces(args):
    return tuple(f"{args.database}.{schema}" for schema in
                 (args.source_schema, args.legacy_schema, args.target_schema))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def load(cur, args):
    source, legacy, _ = namespaces(args)
    manifest = input_manifest(args)
    path = args.evidence_dir / "manifest.json"
    if path.exists():
        raise RuntimeError("Frozen manifest already exists; choose a new evidence directory")
    for table, schema in TABLES.items():
        rows, digest = read_sas(table)
        if digest != manifest["files"][table]["sha256"]:
            raise RuntimeError(f"Input changed during load: {table}")
        fqn = f"{legacy if table == 'MONTHLY_AMB' else source}.{table}"
        columns = ", ".join(f'"{name}" {sql_type}' for name, sql_type in schema)
        cur.execute(f"CREATE OR REPLACE TABLE {fqn} ({columns})")
        placeholders = ", ".join("?" for _ in schema)
        for offset in range(0, len(rows), 5000):
            batch = rows[offset:offset + 5000]
            values = ", ".join(f"({placeholders})" for _ in batch)
            cur.execute(f"INSERT INTO {fqn} VALUES {values}",
                        tuple(value for row in batch for value in row))
        count = cur.execute(f"SELECT COUNT(*) FROM {fqn}").fetchone()[0]
        if count != len(rows):
            raise RuntimeError(f"Load row count mismatch: {fqn}")
        snapshot = table_snapshot(cur, fqn)
        if snapshot["sha256"] != canonical_sha256(rows):
            raise RuntimeError(f"Loaded table differs from source: {fqn}")
        manifest["files"][table].update(loaded_rows=count, loaded_table=snapshot)
    write_json(path, manifest)
    print(json.dumps(manifest, indent=2, default=str))


def canonical_line(row, rounded=False):
    values = []
    for value in row:
        if isinstance(value, float):
            values.append(float(repr(round(value, 6) if rounded else value)))
        elif isinstance(value, date):
            values.append(value.isoformat())
        elif isinstance(value, Decimal):
            values.append(int(value) if value == value.to_integral_value() else str(value))
        else:
            values.append(value)
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def canonical_sha256(rows):
    return hashlib.sha256("\n".join(sorted(canonical_line(row) for row in rows)).encode()).hexdigest()


def multiset_diff(left, right, rounded=False):
    a = Counter(canonical_line(row, rounded) for row in left)
    b = Counter(canonical_line(row, rounded) for row in right)
    return [[{"row": json.loads(row), "multiplicity": count}
             for row, count in sorted(diff.items())] for diff in (a - b, b - a)]


def table_snapshot(cur, fqn):
    database, schema, table = fqn.split(".")
    columns = cur.execute(
        f"SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE, "
        f"CHARACTER_MAXIMUM_LENGTH FROM {database}.INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
        (schema, table)).fetchall()
    if not columns:
        raise RuntimeError(f"No columns found: {fqn}")
    names = ", ".join(f'"{column[0]}"' for column in columns)
    rows = cur.execute(f"SELECT {names} FROM {fqn}").fetchall()
    rows.sort(key=canonical_line)
    return {"columns": [list(c) for c in columns], "rows": [list(row) for row in rows],
            "row_count": len(rows), "sha256": canonical_sha256(rows)}


def input_manifest(args):
    source, legacy, _ = namespaces(args)
    files = {}
    for table, columns in TABLES.items():
        path = ROOT / "sample_data" / f"{table}.sas7bdat"
        files[table] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "byte_size": path.stat().st_size,
            "git_index": git("ls-files", "-s", str(path.relative_to(ROOT))),
            "target_fqn": f"{legacy if table == 'MONTHLY_AMB' else source}.{table}",
            "columns": [list(c) for c in columns],
        }
        if table == "MONTHLY_AMB":
            files[table]["provenance"] = "retained legacy SAS output; SAS engine unavailable, not re-executed"
    return {"source_commit": git("rev-parse", "HEAD"), "files": files,
            "reporting_month": args.reporting_month, "run_date": args.run_date.isoformat()}


def verify_manifest(cur, args):
    saved = json.loads((args.evidence_dir / "manifest.json").read_text())
    current = input_manifest(args)
    for key in ("source_commit", "reporting_month", "run_date"):
        if saved[key] != current[key]:
            raise RuntimeError(f"Frozen manifest changed: {key}")
    for table, info in current["files"].items():
        original = saved["files"][table]
        if any(original[key] != value for key, value in info.items()):
            raise RuntimeError(f"Frozen input changed: {table}")
        snapshot = table_snapshot(cur, info["target_fqn"])
        # JSON serialization makes persisted dates comparable to fetched dates.
        if json.dumps(snapshot, default=str, sort_keys=True) != json.dumps(
                original["loaded_table"], default=str, sort_keys=True):
            raise RuntimeError(f"Frozen loaded table changed: {table}")


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
            value = column
        parts.append(f"IFF({column} IS NULL, 'N', 'V' || LENGTH({value}) || ':' || {value})")
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
    snapshots = [table_snapshot(cur, f"{namespace}.MONTHLY_AMB") for namespace in (legacy, target)]
    legacy_only, new_only = multiset_diff(snapshots[0]["rows"], snapshots[1]["rows"])
    rounded_diff = multiset_diff(snapshots[0]["rows"], snapshots[1]["rows"], rounded=True)
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
        "strict_legacy_only": legacy_only, "strict_new_only": new_only,
        "canonical_sha256": [s["sha256"] for s in snapshots],
        "ordered_columns_types": [s["columns"] for s in snapshots],
        "float_6dp_policy": "Python round(value, 6) on FLOAT only; diagnostic, never strict",
        "float_6dp_difference_counts": [sum(r["multiplicity"] for r in side) for side in rounded_diff],
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
    passed = (not legacy_only and not new_only and snapshots[0]["columns"] == snapshots[1]["columns"]
              and checksums[0] == checksums[1] and not differences and not any(multiset)
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
    parser.add_argument("action", choices=["provision", "manifest", "load", "run", "compare", "all"])
    parser.add_argument("--database", type=identifier, default=sf.database_name(WORKLOAD))
    parser.add_argument("--source-schema", type=identifier, default=f"MONTHLY_AMB_SRC_{sf.RUN_TOKEN}")
    parser.add_argument("--legacy-schema", type=identifier, default=f"MONTHLY_AMB_LEGACY_{sf.RUN_TOKEN}")
    parser.add_argument("--target-schema", type=identifier, default=f"MONTHLY_AMB_SF_{sf.RUN_TOKEN}")
    parser.add_argument("--reporting-month", type=int, default=202507)
    parser.add_argument("--run-date", type=date.fromisoformat, default=date(2025, 8, 1))
    parser.add_argument("--scenario-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    date.fromisoformat(f"{args.reporting_month // 100:04d}-{args.reporting_month % 100:02d}-01")
    if len(set(namespaces(args))) != 3:
        parser.error("SRC, LEGACY and SF must be distinct namespaces")
    for path in (args.evidence_dir, args.scenario_dir):
        if path.resolve().is_relative_to(ROOT):
            parser.error("Output directories must be outside the repository")
    with connect(args) as connection, connection.cursor() as raw:
        cur = RecordedCursor(raw, args.evidence_dir)
        cur.action = args.action
        if args.action == "provision":
            provision(cur, args)
            return
        verify_warehouse(cur)
        cur.execute(f"USE WAREHOUSE {WAREHOUSE}")
        if args.action in ("load", "all"):
            cur.action = "load"
            load(cur, args)
        if args.action in ("manifest", "run", "compare", "all"):
            cur.action = "manifest"
            verify_manifest(cur, args)
        if args.action in ("run", "all"):
            cur.action = "run"
            run_job(cur, args)
        if args.action in ("compare", "all"):
            cur.action = "manifest"
            verify_manifest(cur, args)
            cur.action = "compare"
            compare(cur, args)


if __name__ == "__main__":
    main()
