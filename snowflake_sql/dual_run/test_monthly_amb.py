"""Focused loader and set-based SQL regression tests; no credentials required."""

import argparse
import json
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from monthly_amb import (
    ROOT,
    TABLES,
    canonical_expression,
    canonical_line,
    canonical_sha256,
    connection_arguments,
    identifier,
    matches_batch,
    multiset_diff,
    pushed_head,
    read_sas,
    sas_value,
)


@pytest.mark.parametrize("value", ["x.y", "x; DROP TABLE t", "x'", "x y", ""])
def test_identifiers_reject_sql_fragments(value):
    with pytest.raises(argparse.ArgumentTypeError):
        identifier(value)


def test_sas_types_are_preserved():
    assert identifier("source_schema") == "SOURCE_SCHEMA"
    assert sas_value(b" account ", "VARCHAR") == " account "
    assert sas_value(1001.0, "NUMBER(38,0)") == 1001
    assert sas_value(1.23456789, "FLOAT") == 1.23456789
    assert sas_value(pd.Timestamp("2025-07-01"), "DATE") == date(2025, 7, 1)
    assert sas_value(pd.NaT, "DATE") is None
    assert sas_value(float("nan"), "FLOAT") is None


@pytest.mark.parametrize("value", [1.5, float("inf"), float(2**54)])
def test_fractional_or_unsafe_identifiers_fail(value):
    with pytest.raises(ValueError):
        sas_value(value, "NUMBER(38,0)")


def test_non_midnight_dates_fail_instead_of_truncating():
    with pytest.raises(ValueError):
        sas_value(pd.Timestamp("2025-07-01 01:00:00"), "DATE")


def test_frozen_input_loading():
    accounts, digest = read_sas("CUST_ACCOUNTS")
    assert len(digest) == 64
    assert len(accounts) == 1980
    assert isinstance(accounts[0][0], int)
    assert isinstance(accounts[0][1], str)
    assert isinstance(accounts[0][4], date)
    assert sum(row[-1] is None for row in accounts) == 1709
    assert sum(row[3] == "INACTIVE" for row in accounts) == 271


def select_sql():
    sql = (ROOT / "snowflake_sql" / "JOB03_CALC_AMB.sql").read_text()
    query = sql.split("CREATE OR REPLACE TABLE IDENTIFIER(:output_table) AS", 1)[1]
    query = query.split("RETURN output_table", 1)[0].strip().removesuffix(";")
    return (query.replace("IDENTIFIER(:accounts_table)", "accounts")
            .replace("IDENTIFIER(:balances_table)", "balances")
            .replace(":REPORTING_MONTH::NUMBER(6,0)", "202507::DECIMAL(6,0)")
            .replace(":RUN_DATE", "DATE '2025-08-01'")
            .replace(":month_start", "DATE '2025-07-01'"))


def test_sql_includes_inactive_accounts_filters_month_rounds_and_joins_both_keys():
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE accounts (customer_id INT, account_id VARCHAR, is_active VARCHAR)")
        connection.execute("INSERT INTO accounts VALUES (1, 'shared', 'INACTIVE'), (2, 'shared', 'ACTIVE')")
        connection.execute("CREATE TABLE balances (customer_id INT, account_id VARCHAR, month DATE, end_of_day_balance DOUBLE)")
        connection.execute("""INSERT INTO balances VALUES
            (1, 'shared', '2025-07-01', 1.01), (1, 'shared', '2025-07-01', 2.02),
            (1, 'shared', '2025-08-01', 999), (2, 'shared', '2025-07-01', 8.0),
            (3, 'shared', '2025-07-01', 100), (1, 'orphan', '2025-07-01', 200)""")
        rows = sorted(connection.execute(select_sql()).fetchall())
    assert len(rows) == 2
    assert [(r[0], r[1], int(r[2]), r[3], r[4]) for r in rows] == [
        (1, "shared", 202507, 1.52, date(2025, 8, 1)),
        (2, "shared", 202507, 8.0, date(2025, 8, 1)),
    ]


def test_entire_frozen_window_matches_without_legacy_output_in_computation():
    frames = {}
    for name in ["CUST_ACCOUNTS", "DAILY_BALANCE"]:
        rows, _ = read_sas(name)
        frames[name] = pd.DataFrame(rows, columns=pd.Index([c.lower() for c, _ in TABLES[name]]))
    with duckdb.connect() as connection:
        connection.register("accounts", frames["CUST_ACCOUNTS"])
        connection.register("balances", frames["DAILY_BALANCE"])
        actual = sorted(connection.execute(select_sql()).fetchall())
    expected, _ = read_sas("MONTHLY_AMB")
    assert actual == sorted(expected)


def test_canonical_form_uses_all_columns_in_legacy_order():
    expression = canonical_expression()
    positions = [expression.index(column) for column, _ in TABLES["MONTHLY_AMB"]]
    assert positions == sorted(positions)
    assert expression.count(" || '|' || ") == 4
    assert expression.count("NUMBER(38,6)") == 6
    assert "TRIM" not in expression
    assert "TO_CHAR(DATE_COMPUTED, 'YYYY-MM-DD')" in expression
    assert expression.count("IS NULL, 'N', 'V'") == 5


def test_canonical_null_empty_literal_and_whitespace_are_distinct():
    assert len({canonical_line([v]) for v in (None, "", "\\N", " a ", "a")}) == 5
    assert canonical_line([None]) == "[null]"
    assert canonical_line([7]) == "[7]"
    assert canonical_sha256([]) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_multiset_duplicates_and_order():
    assert multiset_diff([(1,), (1,), (2,)], [(2,), (1,)]) == [
        [{"row": [1], "multiplicity": 1}], []]
    assert canonical_sha256([(1,), (2,)]) == canonical_sha256([(2,), (1,)])
    assert canonical_sha256([(1,), (1,)]) != canonical_sha256([(1,)])
    assert multiset_diff([], []) == [[], []]


def test_float_roundtrip_is_strict_and_rounding_is_separate():
    a, b = 1.0000000000000002, 1.0
    assert canonical_line([a]) == "[" + repr(a) + "]"
    assert json.loads(canonical_line([a])) == [a]
    assert multiset_diff([(a,)], [(b,)]) != [[], []]
    assert multiset_diff([(a,)], [(b,)], rounded=True) == [[], []]


def test_connection_has_in_memory_key_and_ordered_attribution(monkeypatch):
    import monthly_amb as amb
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    session = "https://example.com/sessions/" + "a" * 32
    for name, value in {"SNOWFLAKE_EXT_ACCOUNT": "dummy", "SNOWFLAKE_EXT_USER": "dummy_user",
                        "SNOWFLAKE_EXT_PRIVATE_KEY": pem, "DEVIN_SESSION_URL": session}.items():
        monkeypatch.setenv(name, value)
    args = argparse.Namespace(database="DB", target_schema="TARGET")
    params = connection_arguments(args, "a" * 40, amb.sf.make_run_id("mig_sas", "dev"))
    assert "private_key_file" not in params
    assert params["user"] == "dummy_user"
    assert isinstance(params["private_key"], bytes)
    serialization.load_der_private_key(params["private_key"], password=None)
    assert params["application"] == "Devin_Demo"
    assert params["warehouse"] is None
    tag = json.loads(params["session_parameters"]["QUERY_TAG"])
    assert list(tag) == ["csid", "workload", "phase", "commit", "run_id", "session_url"]
    assert tag["session_url"] == session
    monkeypatch.delenv("DEVIN_SESSION_URL")
    with pytest.raises(KeyError):
        connection_arguments(args, "a" * 40, amb.sf.make_run_id("mig_sas", "dev"))


@pytest.mark.parametrize("dirty,remote", [(" M file", "origin/x"), ("", "")])
def test_connection_refuses_dirty_or_unpushed_head(monkeypatch, dirty, remote):
    import monthly_amb as amb
    monkeypatch.setattr(amb, "git", lambda *args: dirty if args[0] == "status" else
                        ("a" * 40 if args[0] == "rev-parse" else remote))
    with pytest.raises(RuntimeError):
        pushed_head()


def test_provision_only_ddl_and_monitor_validation(monkeypatch, tmp_path):
    import monthly_amb as amb
    statements = []
    cur = argparse.Namespace(execute=statements.append)
    args = argparse.Namespace(database="DB", source_schema="SRC", legacy_schema="LEGACY",
                              target_schema="TARGET", evidence_dir=tmp_path)
    calls = []
    monkeypatch.setattr(amb, "monitor_snapshot", lambda cur: calls.append("monitor") or {"quota": 10})
    monkeypatch.setattr(amb, "verify_warehouse",
                        lambda cur, suspended=False: calls.append(suspended) or {"state": "SUSPENDED"})
    amb.provision(cur, args)
    assert calls == ["monitor", "monitor", True]
    assert all(sql.startswith(("CREATE ", "ALTER ")) for sql in statements)
    assert len(statements) == 7
    assert "INITIALLY_SUSPENDED=TRUE" in statements[0]
    assert "RESOURCE_MONITOR" in statements[1]
    assert "SET TAG" in statements[2]
    assert not any(sql.startswith("ALTER DATABASE") for sql in statements)
    snapshots = iter([{"quota": 10}, {"quota": 11}])
    monkeypatch.setattr(amb, "monitor_snapshot", lambda cur: next(snapshots))
    with pytest.raises(RuntimeError, match="changed"):
        amb.provision(cur, args)


@pytest.mark.parametrize("rows", [[], [{"name": "OTHER", "credit_quota": 10}],
                                   [{"name": "RM", "credit_quota": 11}]])
def test_missing_or_wrong_monitor_fails(monkeypatch, rows):
    import monthly_amb as amb
    monkeypatch.setattr(amb, "MONITOR", "RM")
    monkeypatch.setattr(amb, "show_rows", lambda cur, sql: rows)
    with pytest.raises(RuntimeError):
        amb.monitor_snapshot(None)


def test_frozen_manifest_verifies_bytes_metadata_and_loaded_hash(monkeypatch, tmp_path):
    import monthly_amb as amb
    args = argparse.Namespace(database="DB", source_schema="SRC", legacy_schema="LEGACY",
                              target_schema="TARGET", evidence_dir=tmp_path,
                              reporting_month=202507, run_date=date(2025, 8, 1))
    manifest = amb.input_manifest(args)
    assert len(manifest["source_commit"]) == 40
    assert manifest["files"]["MONTHLY_AMB"]["provenance"].startswith("retained legacy SAS output")
    snapshot = {"columns": [["A", "FLOAT"]], "rows": [[1.0]], "sha256": "frozen", "row_count": 1}
    for info in manifest["files"].values():
        assert info["byte_size"] > 0 and len(info["sha256"]) == 64
        assert info["git_index"].startswith("100644 ")
        info.update(loaded_table=snapshot, loaded_rows=1)
    amb.write_json(tmp_path / "manifest.json", manifest)
    monkeypatch.setattr(amb, "table_snapshot", lambda cur, fqn: snapshot)
    amb.verify_manifest(None, args)
    monkeypatch.setattr(amb, "table_snapshot", lambda cur, fqn: {**snapshot, "sha256": "changed"})
    with pytest.raises(RuntimeError, match="loaded table changed"):
        amb.verify_manifest(None, args)
    manifest["files"]["DAILY_BALANCE"]["sha256"] = "changed"
    amb.write_json(tmp_path / "manifest.json", manifest)
    monkeypatch.setattr(amb, "table_snapshot", lambda cur, fqn: snapshot)
    with pytest.raises(RuntimeError, match="input changed"):
        amb.verify_manifest(None, args)


@pytest.mark.parametrize("rows", [[], [(date(2025, 8, 1),)]])
def test_load_freezes_manifest_and_refuses_overwrite(monkeypatch, tmp_path, rows):
    import monthly_amb as amb
    args = argparse.Namespace(database="DB", source_schema="SRC", legacy_schema="LEGACY",
                              target_schema="TARGET", evidence_dir=tmp_path)
    monkeypatch.setattr(amb, "TABLES", {"MONTHLY_AMB": [("DATE_COMPUTED", "DATE")]})
    monkeypatch.setattr(amb, "input_manifest", lambda args: {"files": {"MONTHLY_AMB": {"sha256": "abc"}}})
    monkeypatch.setattr(amb, "read_sas", lambda table: (rows, "abc"))
    monkeypatch.setattr(amb, "table_snapshot", lambda cur, table: {
        "sha256": amb.canonical_sha256(rows), "rows": rows})
    statements = []

    class Cursor:
        def execute(self, sql, params=None):
            statements.append((sql, params))
            return self

        def fetchone(self):
            return (len(rows),)

    amb.load(Cursor(), args)
    saved = json.loads((tmp_path / "manifest.json").read_text())
    assert saved["files"]["MONTHLY_AMB"]["loaded_rows"] == len(rows)
    assert len([s for s, _ in statements if s.startswith("INSERT")]) == bool(rows)
    with pytest.raises(RuntimeError, match="already exists"):
        amb.load(Cursor(), args)


def test_query_ids_append_across_invocations_even_on_error(tmp_path):
    import monthly_amb as amb

    class Cursor:
        sfqid = None

        def execute(self, sql, params=None):
            self.sfqid = "id-" + sql
            if sql == "bad":
                raise ValueError("bad")

    first = amb.RecordedCursor(Cursor(), tmp_path)
    first.action = "load"
    first.execute("ok")
    second = amb.RecordedCursor(Cursor(), tmp_path)
    second.action = "run"
    with pytest.raises(ValueError):
        second.execute("bad")
    assert json.loads((tmp_path / "queries.json").read_text()) == [
        {"label": "load: ok", "query_id": "id-ok"}, {"label": "run: bad", "query_id": "id-bad"}]


def test_scenario_export_is_not_used_by_sql():
    sql = Path(ROOT / "snowflake_sql" / "JOB03_CALC_AMB.sql").read_text()
    assert "LEGACY" not in sql
    assert "CURRENT_DATE" not in sql


def test_banking_task_requires_explicit_graph_config_not_replay_defaults():
    sql = (ROOT / "snowflake_sql" / "orchestration" / "tasks.sql").read_text()
    node = sql.split("-- JOB03: Calculate Monthly Average Balance", 1)[1].split("-- JOB04:", 1)[0]
    assert "AFTER FINANCE_DB.PUBLIC.TASK_JOB02_DAILY_TRANSACTIONS" in node
    assert "CALL IDENTIFIER(:procedure_name)" in node
    assert "RAISE missing_config" in node
    for key in ["source_namespace", "target_namespace", "reporting_month", "run_date"]:
        assert f"SYSTEM$GET_TASK_GRAPH_CONFIG('amb.{key}')" in node
    assert "SFNFR_" not in node
    assert "202507" not in node


@pytest.mark.parametrize("column,change,expected", [
    ("REPORTING_MONTH_YYYYMM", {}, True),
    ("REPORTING_MONTH_YYYYMM", {"min": 202508, "max": 202508}, False),
    ("REPORTING_MONTH_YYYYMM", {"max": 202508}, False),
    ("DATE_COMPUTED", {"min": date(2025, 9, 1), "max": date(2025, 9, 1)}, False),
    ("DATE_COMPUTED", {"max": date(2025, 9, 1)}, False),
    ("DATE_COMPUTED", {"nulls": 1}, False),
])
def test_comparison_checks_actual_output_batch(column, change, expected):
    column_profile = {
        "REPORTING_MONTH_YYYYMM": {"count": 1980, "nulls": 0, "min": 202507, "max": 202507},
        "DATE_COMPUTED": {"count": 1980, "nulls": 0, "min": date(2025, 8, 1), "max": date(2025, 8, 1)},
    }
    column_profile[column].update(change)
    assert matches_batch(column_profile, 202507, date(2025, 8, 1)) is expected
