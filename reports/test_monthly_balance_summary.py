from datetime import date
from typing import Any

import pandas as pd
import pytest
from monthly_balance_summary import (
    ROOT,
    load_legacy,
    load_snowflake,
    money,
    prepare,
    render,
    summarise,
)


def _tables(
    customer_ids: list[float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    amb = pd.DataFrame(
        {
            "customer_id": customer_ids or [1001.0],
            "account_id": [b"A1"],
            "reporting_month_yyyymm": [202507.0],
            "average_monthly_balance": [12.5],
            "date_computed": pd.to_datetime(["2025-07-31"]),
        }
    )
    accounts = pd.DataFrame(
        {
            "customer_id": [1001.0],
            "account_id": [b"A1"],
            "account_type": [b"CHECKING"],
            "is_active": [None],
        }
    )
    return amb, accounts


def test_prepare_normalizes_values_and_rejects_invalid_keys() -> None:
    amb, accounts = _tables()
    result = prepare(amb, accounts)

    assert result.loc[0, "account_type"] == "CHECKING"
    assert result.loc[0, "customer_id"] == 1001
    assert result.loc[0, "reporting_month_yyyymm"] == 202507
    assert result.loc[0, "date_computed"] == date(2025, 7, 31)
    assert result.loc[0, "is_active"] == "Unknown"

    invalid_amb = amb.copy()
    invalid_amb.loc[0, "customer_id"] = 1001.5
    with pytest.raises(ValueError, match="Expected an integer identifier"):
        prepare(invalid_amb, accounts)

    duplicate_accounts = pd.concat([accounts, accounts], ignore_index=True)
    with pytest.raises(ValueError):
        prepare(amb, duplicate_accounts)


def test_prepare_normalizes_sas_and_snowflake_shapes_equally() -> None:
    sas_amb = pd.DataFrame(
        {
            "customer_id": [1002.0, 1001.0],
            "account_id": [b"B2", b"A1"],
            "reporting_month_yyyymm": [202507.0, 202506.0],
            "average_monthly_balance": [20.0, 12.5],
            "date_computed": pd.to_datetime(["2025-07-31", "2025-06-30"]),
        }
    )
    sas_accounts = pd.DataFrame(
        {
            "customer_id": [1002.0, 1001.0],
            "account_id": [b"B2", b"A1"],
            "account_type": [b"SAVINGS", b"CHECKING"],
            "is_active": [b"Y", b"N"],
        }
    )
    snowflake_amb = pd.DataFrame(
        {
            "customer_id": [1001, 1002],
            "account_id": ["A1", "B2"],
            "reporting_month_yyyymm": [202506, 202507],
            "average_monthly_balance": [12.5, 20.0],
            "date_computed": [date(2025, 6, 30), date(2025, 7, 31)],
        }
    )
    snowflake_accounts = pd.DataFrame(
        {
            "customer_id": [1001, 1002],
            "account_id": ["A1", "B2"],
            "account_type": ["CHECKING", "SAVINGS"],
            "is_active": ["N", "Y"],
        }
    )

    pd.testing.assert_frame_equal(
        prepare(sas_amb, sas_accounts),
        prepare(snowflake_amb, snowflake_accounts),
    )


def test_large_customer_identifiers_remain_distinct():
    from monthly_balance_summary import _integer

    ids = [2**53, 2**53 + 1]
    amb, accounts = _tables()
    amb = pd.concat([amb, amb], ignore_index=True).assign(customer_id=ids)
    accounts = pd.concat([accounts, accounts], ignore_index=True).assign(customer_id=ids)
    frame = prepare(amb, accounts)
    assert list(frame.customer_id) == ids
    assert summarise(frame)["totals"]["customers"] == 2
    assert _integer(str(ids[1])) == ids[1]
    for value in ("1.5", "NaN", "Infinity", "invalid", None):
        with pytest.raises(ValueError, match="Expected an integer identifier"):
            _integer(value)


def _summary_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            [1001, "A1", 202507, date(2025, 7, 31), 100.0, "CHECKING", "Y"],
            [1001, "A2", 202507, date(2025, 7, 31), 50.0, "SAVINGS", "N"],
            [1003, "A3", 202507, date(2025, 7, 31), 75.0, "CHECKING", "Y"],
            [1003, "A4", 202507, date(2025, 7, 31), 75.0, "SAVINGS", "Y"],
            [
                1002,
                "A5",
                202507,
                date(2025, 7, 31),
                10.0,
                "</script><script>alert(1)</script>",
                "N",
            ],
        ],
        columns=[
            "customer_id",
            "account_id",
            "reporting_month_yyyymm",
            "date_computed",
            "average_monthly_balance",
            "account_type",
            "is_active",
        ],
    )


def test_summarise_totals_groups_top_ranks_and_details() -> None:
    summary = summarise(_summary_frame())

    assert summary["totals"]["accounts"] == 5
    assert summary["totals"]["customers"] == 3
    assert summary["totals"]["total"] == 310.0
    assert summary["totals"]["average_account"] == 62.0
    assert summary["totals"]["average_customer"] == pytest.approx(310 / 3)
    assert summary["by_type"] == [
        {
            "label": "</script><script>alert(1)</script>",
            "accounts": 1,
            "total": 10.0,
            "average": 10.0,
        },
        {"label": "CHECKING", "accounts": 2, "total": 175.0, "average": 87.5},
        {"label": "SAVINGS", "accounts": 2, "total": 125.0, "average": 62.5},
    ]
    assert summary["by_status"] == [
        {"label": "N", "accounts": 2, "total": 60.0, "average": 30.0},
        {"label": "Y", "accounts": 3, "total": 250.0, "average": pytest.approx(250 / 3)},
    ]
    assert summary["top"] == [
        {"rank": 1, "customer_id": 1001, "accounts": 2, "total": 150.0},
        {"rank": 2, "customer_id": 1003, "accounts": 2, "total": 150.0},
        {"rank": 3, "customer_id": 1002, "accounts": 1, "total": 10.0},
    ]
    assert summary["ranks"] == {
        "1001": {"rank": 1, "total": 150.0},
        "1002": {"rank": 3, "total": 10.0},
        "1003": {"rank": 2, "total": 150.0},
    }
    assert summary["details"]["1001"] == [
        {
            "account_id": "A1",
            "account_type": "CHECKING",
            "is_active": "Y",
            "balance": 100.0,
        },
        {
            "account_id": "A2",
            "account_type": "SAVINGS",
            "is_active": "N",
            "balance": 50.0,
        },
    ]
    assert summary["details"]["1002"] == [
        {
            "account_id": "A5",
            "account_type": "</script><script>alert(1)</script>",
            "is_active": "N",
            "balance": 10.0,
        }
    ]
    assert summary["details"]["1003"] == [
        {
            "account_id": "A3",
            "account_type": "CHECKING",
            "is_active": "Y",
            "balance": 75.0,
        },
        {
            "account_id": "A4",
            "account_type": "SAVINGS",
            "is_active": "Y",
            "balance": 75.0,
        },
    ]


def test_render_escapes_data_and_includes_month_and_source_labels() -> None:
    summary = summarise(_summary_frame())
    legacy = render(summary, "legacy", "MONTHLY_AMB.sas7bdat")
    snowflake = render(summary, "snowflake", "DB.SCHEMA.MONTHLY_AMB")

    assert (
        "<title>Monthly Customer Balance Summary - July 2025 - EMULATED Legacy SAS baseline</title>"
        in legacy
    )
    assert "Monthly Customer Balance Summary &mdash; July 2025" in legacy
    assert "Legacy SAS baseline" in legacy
    assert "Source: <b>Snowflake</b>" in snowflake
    assert "Snowflake" in snowflake
    assert "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in legacy
    json_payload = legacy.split(
        '<script type="application/json" id="details">', maxsplit=1
    )[1].split("</script>", maxsplit=1)[0]
    assert r"\u003c/script\u003e" in json_payload
    assert "</script>" not in json_payload


def test_json_script_escapes_mixed_case_closing_tags():
    import json

    from monthly_balance_summary import _json_script

    value = {"account_type": '</ScRiPt><script>alert("x")</script>&'}
    script = _json_script("details", value)
    payload = script.split(">", 1)[1].rsplit("</script>", 1)[0]
    assert "<" not in payload
    assert ">" not in payload
    assert "&" not in payload
    assert json.loads(payload) == value


class FakeCursor:
    def __init__(self, results: list[list[tuple[Any, ...]]]) -> None:
        self.results = iter(results)
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[str, ...]) -> "FakeCursor":
        self.calls.append((sql, params))
        self.rows = next(self.results)
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


def test_load_snowflake_binds_table_identifiers_and_rejects_invalid_names() -> None:
    amb_table = "DB.SCHEMA.MONTHLY_AMB"
    accounts_table = "DB.SCHEMA.CUST_ACCOUNTS"
    amb_sql = (
        "SELECT CUSTOMER_ID, ACCOUNT_ID, REPORTING_MONTH_YYYYMM, "
        "AVERAGE_MONTHLY_BALANCE, DATE_COMPUTED FROM IDENTIFIER(?)"
    )
    accounts_sql = (
        "SELECT CUSTOMER_ID, ACCOUNT_ID, ACCOUNT_TYPE, IS_ACTIVE "
        "FROM IDENTIFIER(?)"
    )
    cursor = FakeCursor(
        [
            [(1001, "A1", 202507, 12.5, date(2025, 7, 31))],
            [(1001, "A1", "CHECKING", "Y")],
        ]
    )

    result = load_snowflake(cursor, amb_table, accounts_table)

    assert cursor.calls == [
        (amb_sql, (amb_table,)),
        (accounts_sql, (accounts_table,)),
    ]
    assert result.loc[0, "customer_id"] == 1001
    assert result.loc[0, "account_type"] == "CHECKING"

    invalid_cursor = FakeCursor([])
    with pytest.raises(ValueError, match="Expected DATABASE.SCHEMA.TABLE"):
        load_snowflake(invalid_cursor, "MONTHLY_AMB; DROP", accounts_table)
    assert invalid_cursor.calls == []


def test_legacy_sample_data_integration() -> None:
    frame = load_legacy(ROOT / "sample_data")
    summary = summarise(frame)

    assert len(frame) == 1980
    assert summary["totals"]["customers"] == 1000
    assert money(summary["totals"]["total"]) == "5,702,328.99"
    assert summary["top"][0]["customer_id"] == 1360
    assert money(summary["top"][0]["total"]) == "16,335.94"


def test_frozen_digest_and_business_validation():
    from monthly_balance_summary import (
        INPUT_DIGEST,
        frozen_input_digest,
        validate_business,
    )

    assert frozen_input_digest(202507, date(2025, 8, 1)) == INPUT_DIGEST
    with pytest.raises(RuntimeError, match="digest mismatch"):
        frozen_input_digest(202506, date(2025, 8, 1))
    frame = load_legacy(ROOT / "sample_data")
    validate_business(frame, 202507, date(2025, 8, 1))
    for bad in (frame.iloc[:0], frame.assign(reporting_month_yyyymm=202506),
                frame.assign(date_computed=date(2025, 7, 31))):
        with pytest.raises(RuntimeError, match="month/run date mismatch"):
            validate_business(bad, 202507, date(2025, 8, 1))


def test_output_and_disclosures(tmp_path):
    from monthly_balance_summary import outside_repo

    assert outside_repo(tmp_path / "report.html") == (tmp_path / "report.html").resolve()
    with pytest.raises(ValueError, match="outside"):
        outside_repo(ROOT / "report.html")
    report = render(summarise(_summary_frame()), "legacy", "sample_data/MONTHLY_AMB.sas7bdat",
                    commit="abc")
    for text in ("EMULATED", "no native SAS engine rerun", "equivalence is unverified",
                 "original SAS source missing", "embedded snippet differs", "Source commit: abc"):
        assert text in report


def test_warehouse_and_task_guards(monkeypatch):
    import monthly_balance_summary as report

    row = {"name": report.harness.WAREHOUSE, "size": "X-Small", "auto_suspend": 60,
           "resource_monitor": report.harness.MONITOR, "state": "SUSPENDED"}
    monkeypatch.setattr(report.harness, "show_rows", lambda *a: [row])
    assert report.warehouse_snapshot(None) == row
    for field, value in (("size", "Small"), ("auto_suspend", 600),
                         ("resource_monitor", "WRONG")):
        good = row.copy()
        row[field] = value
        with pytest.raises(RuntimeError, match="observed="):
            report.warehouse_snapshot(None)
        row = good
    monkeypatch.setattr(report.harness, "show_rows", lambda *a: [])
    with pytest.raises(RuntimeError, match="missing.*observed="):
        report.warehouse_snapshot(None)
    for tasks in ([], [{"state": "suspended"}]):
        monkeypatch.setattr(report.harness, "show_rows", lambda *a, tasks=tasks: tasks)
        assert report.tasks_snapshot(None, "DB") == tasks
    monkeypatch.setattr(report.harness, "show_rows", lambda *a: [{"state": "started"}])
    with pytest.raises(RuntimeError, match="remain suspended"):
        report.tasks_snapshot(None, "DB")


def test_suspend_polling_and_timeout_preserve_raw_rows(monkeypatch):
    import monthly_balance_summary as report

    row = {"name": report.harness.WAREHOUSE, "size": "X-Small", "auto_suspend": 60,
           "resource_monitor": report.harness.MONITOR, "state": "SUSPENDING"}
    reads = iter([[row], [{**row, "state": "SUSPENDED"}]])
    monkeypatch.setattr(report.harness, "show_rows", lambda *a: next(reads))
    clock = iter([0, 0, 0])
    monkeypatch.setattr(report.time, "monotonic", lambda: next(clock))
    sleeps = []
    monkeypatch.setattr(report.time, "sleep", sleeps.append)
    evidence = {}
    assert report.warehouse_snapshot(None, True, evidence=evidence)["state"] == "SUSPENDED"
    assert sleeps == [2]
    assert len(evidence["warehouse_readbacks"]) == 2
    clock = iter([0, 60])
    monkeypatch.setattr(report.harness, "show_rows", lambda *a: [row])
    with pytest.raises(RuntimeError, match="state.*observed='SUSPENDING'.*timeout=60s"):
        report.warehouse_snapshot(None, True, evidence=evidence)
    assert evidence["warehouse"] == [row]


@pytest.mark.parametrize("failure", ["before", "after", "tasks", "connect", "already_suspended", None])
def test_evidence_always_written(monkeypatch, tmp_path, failure):
    import json
    from argparse import Namespace

    import monthly_balance_summary as report

    class Cursor:
        def __init__(self):
            self.queries = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql):
            self.queries.append({"query_id": str(len(self.queries)), "label": sql})
            if failure == "already_suspended" and sql.startswith("ALTER WAREHOUSE"):
                raise report.harness.snowflake.connector.errors.ProgrammingError(
                    msg="Invalid state. Warehouse cannot be suspended.", errno=90064)

    class Connection(Cursor):
        def cursor(self):
            return Cursor()

    def connect(**params):
        if failure == "connect":
            raise RuntimeError("connection error")
        return Connection()

    def show(cursor, sql):
        cursor.execute(sql)
        if "TASKS" in sql:
            return [{"name": "TASK", "state": "started" if failure == "tasks" else "suspended"}]
        after = any("SUSPEND" in query["label"] and "ALTER" in query["label"]
                    for query in cursor.queries)
        bad = failure == "before" or (failure == "after" and after)
        return [{"name": report.harness.WAREHOUSE, "state": "SUSPENDED", "size": "X-Small",
                 "auto_suspend": 60, "resource_monitor": "WRONG" if bad else report.harness.MONITOR}]

    monkeypatch.setattr(report.harness, "connection_arguments", lambda *a: {})
    monkeypatch.setattr(report.harness.snowflake.connector, "connect", connect)
    monkeypatch.setattr(report.harness, "RecordedCursor", lambda raw, path: raw)
    monkeypatch.setattr(report.harness, "show_rows", show)
    monkeypatch.setattr(report, "load_snowflake", lambda *a: None)
    monkeypatch.setattr(report, "write_report", lambda *a: None)
    args = Namespace(out=tmp_path / "report.html", database="DB", amb_table="DB.S.T",
                     accounts_table="DB.S.A")
    if failure and failure != "already_suspended":
        with pytest.raises(RuntimeError):
            report.run_snowflake(args, "commit", "digest")
    else:
        report.run_snowflake(args, "commit", "digest")
    evidence = json.loads(args.out.with_suffix(".evidence.json").read_text())
    assert evidence["run_id"]
    assert bool(evidence["errors"]) == (failure not in (None, "already_suspended"))
    if failure == "already_suspended":
        assert "cannot be suspended" in evidence["suspend_response"]
    if failure != "connect":
        assert evidence["warehouse_before"]
        assert evidence["warehouse_after"]
        assert evidence["tasks_after"]
        assert evidence["queries"]
        assert all(q["run_id"] == evidence["run_id"] for q in evidence["queries"])
        if failure == "before":
            assert not any("USE WAREHOUSE" in q["label"] for q in evidence["queries"])
        if failure in ("after", "tasks", None):
            assert evidence["tasks_before"]
