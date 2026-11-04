"""Focused loader and set-based SQL regression tests; no credentials required."""

import argparse
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from monthly_amb import (
    ROOT,
    TABLES,
    canonical_expression,
    identifier,
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
        frames[name] = pd.DataFrame(rows, columns=[c.lower() for c, _ in TABLES[name]])
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
    assert expression.count("NUMBER(38,6)") == 3
    assert "TRIM(ACCOUNT_ID)" in expression
    assert "TO_CHAR(DATE_COMPUTED, 'YYYY-MM-DD')" in expression
    assert expression.count("'\\\\N'") == 5


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
