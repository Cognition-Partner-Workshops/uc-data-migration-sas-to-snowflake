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
        "<title>Monthly Customer Balance Summary - July 2025 - Legacy SAS baseline</title>"
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
    assert r"<\/script>" in json_payload
    assert "</script>" not in json_payload


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
