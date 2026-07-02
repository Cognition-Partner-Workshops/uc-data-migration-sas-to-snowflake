"""Single-source table specifications for the SAS-to-Snowflake toolkit.

The definitions here drive CSV conversion, COPY INTO generation, and
validation SQL emission so the DDL and migration logic stay aligned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

Converter = Callable[[pd.Series], pd.Series]


# ── value normalization helpers ──────────────────────────────────────────

def decode_value(value: object) -> object:
    """Decode SAS byte strings and preserve scalar values."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="ignore").strip()
    if isinstance(value, (list, tuple)) or (
        hasattr(value, "__iter__") and not isinstance(value, str)
    ):
        try:
            return bytes(value).decode("utf-8", errors="ignore").strip()
        except Exception:
            return value
    return value


def decode_text(series: pd.Series) -> pd.Series:
    """Decode SAS byte strings in a Series."""
    return series.map(decode_value)


def to_nullable_int(series: pd.Series) -> pd.Series:
    """Convert numeric values to nullable integer Series."""
    return pd.to_numeric(series, errors="coerce").round().astype("Int64")


def to_decimal(series: pd.Series) -> pd.Series:
    """Convert numeric values to two-decimal floating point Series."""
    return pd.to_numeric(series, errors="coerce").round(2)


def to_date_string(series: pd.Series) -> pd.Series:
    """Convert date-like values to ISO YYYY-MM-DD strings."""
    dt = pd.to_datetime(series, errors="coerce")
    return dt.dt.strftime("%Y-%m-%d")


def to_month_start_string(series: pd.Series) -> pd.Series:
    """Normalize dates to the first of the month and format as ISO text."""
    dt = pd.to_datetime(series, errors="coerce")
    normalized = dt.dt.to_period("M").dt.to_timestamp()
    return normalized.dt.strftime("%Y-%m-%d")


@dataclass(frozen=True)
class ColumnSpec:
    """Mapping from a SAS source column to a Snowflake target column."""

    source_col: str
    target_col: str
    snowflake_type: str
    converter: Converter


@dataclass(frozen=True)
class TableSpec:
    """Canonical specification for a migration table."""

    table_name: str
    sas_filename: str
    columns: tuple[ColumnSpec, ...]
    numeric_columns: tuple[str, ...]
    distinct_count_columns: tuple[str, ...]
    natural_key_columns: tuple[str, ...]
    target_schema: str = "RAW"

    @property
    def source_columns(self) -> tuple[str, ...]:
        return tuple(column.source_col for column in self.columns)

    @property
    def target_columns(self) -> tuple[str, ...]:
        return tuple(column.target_col for column in self.columns)

    @property
    def target_column_specs(self) -> tuple[tuple[str, str], ...]:
        return tuple((column.target_col, column.snowflake_type) for column in self.columns)


TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        table_name="CUST_ACCOUNTS",
        sas_filename="CUST_ACCOUNTS.sas7bdat",
        columns=(
            ColumnSpec("customer_id", "CUSTOMER_ID", "NUMBER(10,0)", to_nullable_int),
            ColumnSpec("account_id", "ACCOUNT_ID", "VARCHAR(16)", decode_text),
            ColumnSpec("account_type", "ACCOUNT_TYPE", "VARCHAR(20)", decode_text),
            ColumnSpec("is_active", "IS_ACTIVE", "VARCHAR(10)", decode_text),
            ColumnSpec("start_date", "START_DATE", "DATE", to_date_string),
            ColumnSpec("end_date", "END_DATE", "DATE", to_date_string),
        ),
        numeric_columns=(),
        distinct_count_columns=("account_id", "customer_id"),
        natural_key_columns=("account_id",),
    ),
    TableSpec(
        table_name="DAILY_BALANCE",
        sas_filename="DAILY_BALANCE.sas7bdat",
        columns=(
            ColumnSpec("customer_id", "CUSTOMER_ID", "NUMBER(10,0)", to_nullable_int),
            ColumnSpec("account_id", "ACCOUNT_ID", "VARCHAR(16)", decode_text),
            ColumnSpec("date", "BALANCE_DATE", "DATE", to_date_string),
            ColumnSpec(
                "end_of_day_balance",
                "END_OF_DAY_BALANCE",
                "NUMBER(12,2)",
                to_decimal,
            ),
            ColumnSpec("month", "MONTH", "DATE", to_month_start_string),
        ),
        numeric_columns=("end_of_day_balance",),
        distinct_count_columns=("customer_id", "account_id", "date"),
        natural_key_columns=("customer_id", "account_id", "date"),
    ),
    TableSpec(
        table_name="MONTHLY_AMB",
        sas_filename="MONTHLY_AMB.sas7bdat",
        columns=(
            ColumnSpec("customer_id", "CUSTOMER_ID", "NUMBER(10,0)", to_nullable_int),
            ColumnSpec("account_id", "ACCOUNT_ID", "VARCHAR(16)", decode_text),
            ColumnSpec(
                "reporting_month_yyyymm",
                "REPORTING_MONTH_YYYYMM",
                "NUMBER(6,0)",
                to_nullable_int,
            ),
            ColumnSpec(
                "average_monthly_balance",
                "AVERAGE_MONTHLY_BALANCE",
                "NUMBER(12,2)",
                to_decimal,
            ),
            ColumnSpec("date_computed", "DATE_COMPUTED", "DATE", to_date_string),
        ),
        numeric_columns=("average_monthly_balance",),
        distinct_count_columns=("customer_id", "account_id", "reporting_month_yyyymm"),
        natural_key_columns=("customer_id", "account_id", "reporting_month_yyyymm"),
    ),
)

TABLE_BY_NAME: dict[str, TableSpec] = {spec.table_name: spec for spec in TABLE_SPECS}
