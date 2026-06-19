"""
Pytest suite validating that the Python translations produce the same
results as the original SAS macros for representative sample inputs.
"""

from __future__ import annotations

import os
import tempfile

import pandas as pd
import pytest

from python_migration.sas_transforms import (
    CompareResult,
    compare,
    dedup_mstring,
    dedup_string,
    export_csv,
    export_dbms,
    export_xlsx,
    subset_data,
    transpose,
)

# ======================================================================
# Fixtures – reusable sample data
# ======================================================================

@pytest.fixture
def class_df() -> pd.DataFrame:
    """Mimics sashelp.class — a common SAS demo dataset."""
    return pd.DataFrame({
        "Name": ["Alice", "Bob", "Carol", "Dave", "Eve",
                 "Frank", "Grace", "Hank", "Ivy", "John"],
        "Sex": ["F", "M", "F", "M", "F", "M", "F", "M", "F", "M"],
        "Age": [13, 14, 13, 15, 14, 12, 14, 15, 13, 12],
        "Height": [56.5, 64.8, 62.8, 67.0, 59.8, 57.3, 63.0, 72.0, 62.5, 59.0],
        "Weight": [84.0, 112.5, 102.5, 128.0, 90.0, 83.0, 105.0, 150.0, 98.0, 99.5],
    })


@pytest.fixture
def vitals_df() -> pd.DataFrame:
    """Resembles the transpose usage example with clinical vitals."""
    return pd.DataFrame({
        "StudyID": ["S1", "S1", "S1", "S1"],
        "SubjID": ["001", "001", "001", "001"],
        "Visit": ["V1", "V1", "V2", "V2"],
        "DIABP": [80, 82, 78, 76],
        "SYSBP": [120, 118, 115, 112],
        "Pulse": [72, 70, 68, 65],
    })


@pytest.fixture
def tmp_dir() -> str:
    with tempfile.TemporaryDirectory() as d:
        yield d


# ======================================================================
# transpose tests
# ======================================================================

class TestTranspose:
    """Validate the transpose function against SAS PROC TRANSPOSE."""

    def test_basic_by_var(self, vitals_df: pd.DataFrame):
        """Transpose with BY and VAR — standard clinical data pivot."""
        result = transpose(
            data=vitals_df,
            by=["StudyID", "SubjID", "Visit"],
            var=["DIABP", "SYSBP", "Pulse"],
        )
        assert "_NAME_" in result.columns
        assert set(result["_NAME_"].unique()) == {"DIABP", "SYSBP", "Pulse"}
        assert "COL1" in result.columns

    def test_rename_col(self, vitals_df: pd.DataFrame):
        """COL parameter renames value columns."""
        result = transpose(
            data=vitals_df,
            by=["StudyID", "SubjID", "Visit"],
            var=["DIABP", "SYSBP", "Pulse"],
            col=["measures"],
        )
        assert "measures" in result.columns
        assert "COL1" not in result.columns

    def test_rename_name(self, vitals_df: pd.DataFrame):
        """NAME parameter renames _NAME_ column."""
        result = transpose(
            data=vitals_df,
            by=["StudyID", "SubjID", "Visit"],
            var=["DIABP", "SYSBP", "Pulse"],
            name="variable",
        )
        assert "variable" in result.columns
        assert "_NAME_" not in result.columns

    def test_drop_name_and_label(self, vitals_df: pd.DataFrame):
        """Setting name=None and label=None drops those columns."""
        result = transpose(
            data=vitals_df,
            by=["StudyID", "SubjID", "Visit"],
            var=["DIABP", "SYSBP", "Pulse"],
            name=None,
            label=None,
        )
        assert "_NAME_" not in result.columns
        assert "_LABEL_" not in result.columns

    def test_no_by(self, class_df: pd.DataFrame):
        """Transpose without BY — all observations transposed."""
        result = transpose(
            data=class_df,
            var=["Age", "Height", "Weight"],
            name="stat",
            label=None,
        )
        assert len(result) == 3
        assert "stat" in result.columns
        assert set(result["stat"]) == {"Age", "Height", "Weight"}

    def test_notsorted(self, vitals_df: pd.DataFrame):
        """NOTSORTED preserves original order."""
        result = transpose(
            data=vitals_df,
            by=["StudyID", "SubjID", "Visit"],
            var=["DIABP"],
            notsorted=True,
        )
        visits = result["Visit"].tolist()
        assert visits == ["V1", "V2"]

    def test_where_filter(self, vitals_df: pd.DataFrame):
        """WHERE parameter filters rows before transposing."""
        result = transpose(
            data=vitals_df,
            by=["StudyID", "SubjID", "Visit"],
            var=["DIABP"],
            where='Visit == "V1"',
        )
        assert all(result["Visit"] == "V1")

    def test_col_and_id_mutually_exclusive(self, vitals_df: pd.DataFrame):
        with pytest.raises(ValueError, match="Only one"):
            transpose(data=vitals_df, col=["x"], id_col="Visit")

    def test_prefix(self, vitals_df: pd.DataFrame):
        """Custom prefix replaces COL."""
        result = transpose(
            data=vitals_df,
            by=["StudyID", "SubjID", "Visit"],
            var=["DIABP"],
            prefix="val_",
        )
        assert any(c.startswith("val_") for c in result.columns)


# ======================================================================
# subset_data tests
# ======================================================================

class TestSubsetData:
    """Validate the subset_data function against SAS %subset_data."""

    def test_where(self, class_df: pd.DataFrame):
        result = subset_data(class_df, where='Sex == "F"')
        assert all(result["Sex"] == "F")
        assert len(result) == 5

    def test_obs_range(self, class_df: pd.DataFrame):
        """OBS= "1-5 or 8-10" keeps rows 1-5 and 8-10 (1-based)."""
        result = subset_data(class_df, obs="1-5 or 8-10")
        assert len(result) == 8

    def test_single_obs(self, class_df: pd.DataFrame):
        result = subset_data(class_df, obs="3")
        assert len(result) == 1

    def test_firstobs_lastobs(self, class_df: pd.DataFrame):
        result = subset_data(class_df, firstobs=3, lastobs=7)
        assert len(result) == 5

    def test_keep(self, class_df: pd.DataFrame):
        result = subset_data(class_df, keep=["Name", "Age"])
        assert list(result.columns) == ["Name", "Age"]

    def test_drop(self, class_df: pd.DataFrame):
        result = subset_data(class_df, drop=["Weight", "Height"])
        assert "Weight" not in result.columns
        assert "Height" not in result.columns

    def test_rename(self, class_df: pd.DataFrame):
        """Rename is applied before where (SAS semantics)."""
        result = subset_data(
            class_df,
            rename={"Sex": "Gender"},
            where='Gender == "F"',
            keep=["Name", "Age", "Gender"],
        )
        assert "Gender" in result.columns
        assert "Sex" not in result.columns
        assert len(result) == 5

    def test_if_func(self, class_df: pd.DataFrame):
        result = subset_data(
            class_df,
            if_func=lambda df: df["Age"] > 13,
        )
        assert all(result["Age"] > 13)

    def test_combined(self, class_df: pd.DataFrame):
        """Multiple parameters at once."""
        result = subset_data(
            class_df,
            firstobs=1,
            lastobs=8,
            rename={"Sex": "Gender"},
            where='Gender == "M"',
            keep=["Name", "Gender", "Age"],
        )
        assert "Gender" in result.columns
        assert all(result["Gender"] == "M")
        assert len(result) <= 4


# ======================================================================
# compare tests
# ======================================================================

class TestCompare:
    """Validate the compare function against SAS PROC COMPARE."""

    def test_identical(self, class_df: pd.DataFrame):
        result = compare(class_df, class_df.copy(), by=["Name"])
        assert result.equal is True
        assert len(result.value_diffs) == 0
        assert len(result.base_only_rows) == 0
        assert len(result.comp_only_rows) == 0

    def test_value_diff(self, class_df: pd.DataFrame):
        comp = class_df.copy()
        comp.loc[comp["Name"] == "John", "Age"] = 99
        result = compare(class_df, comp, by=["Name"])
        assert result.equal is False
        assert len(result.value_diffs) == 1
        diff = result.value_diffs.iloc[0]
        assert diff["_column_"] == "Age"
        assert diff["_base_"] == 12
        assert diff["_comp_"] == 99

    def test_missing_column(self, class_df: pd.DataFrame):
        comp = class_df.drop(columns=["Sex"])
        result = compare(class_df, comp, by=["Name"])
        assert result.equal is False
        assert "Sex" in result.base_only_columns
        assert len(result.comp_only_columns) == 0

    def test_extra_rows(self, class_df: pd.DataFrame):
        base = class_df.iloc[:8].copy()
        comp = class_df.copy()
        result = compare(base, comp, by=["Name"])
        assert len(result.comp_only_rows) == 2
        assert len(result.base_only_rows) == 0

    def test_no_by(self, class_df: pd.DataFrame):
        comp = class_df.copy()
        comp.loc[0, "Age"] = 99
        result = compare(class_df, comp)
        assert result.equal is False
        assert len(result.value_diffs) >= 1

    def test_absolute_method(self, class_df: pd.DataFrame):
        comp = class_df.copy()
        comp["Height"] = comp["Height"] + 0.0001
        result_exact = compare(class_df, comp, by=["Name"], method="exact")
        result_abs = compare(class_df, comp, by=["Name"],
                             method="absolute", criterion=0.001)
        assert result_exact.equal is False
        assert result_abs.equal is True

    def test_repr(self, class_df: pd.DataFrame):
        result = compare(class_df, class_df.copy())
        assert "CompareResult" in repr(result)


# ======================================================================
# dedup_string tests
# ======================================================================

class TestDedupString:
    """Validate dedup_string against SAS %dedup_string."""

    def test_space_delimited(self):
        assert dedup_string("C A B B A G E 3 2 1 1 2 3") == "C A B G E 3 2 1"

    def test_pipe_delimited(self):
        result = dedup_string("C|A|B|B|A|G|E|3|2|1|1|2|3", dlm="|")
        assert result == "C|A|B|G|E|3|2|1"

    def test_case_insensitive(self):
        assert dedup_string("a A b B") == "a b"

    def test_empty_string(self):
        assert dedup_string("") == ""

    def test_single_token(self):
        assert dedup_string("hello") == "hello"

    def test_all_duplicates(self):
        assert dedup_string("X X X X") == "X"


# ======================================================================
# dedup_mstring tests
# ======================================================================

class TestDedupMstring:
    """Validate dedup_mstring against SAS %dedup_mstring."""

    def test_space_delimited(self):
        assert dedup_mstring("C A B B A G E 3 2 1 1 2 3") == "C A B G E 3 2 1"

    def test_comma_delimited(self):
        result = dedup_mstring("C, A, B, B, A, G, E", indlm=",")
        assert result == "C,A,B,G,E"

    def test_multi_delimiter(self):
        result = dedup_mstring("C^A^B^B^A#G#E#3|2|1*1*2*3", indlm="^#|*")
        assert result == "C A B G E 3 2 1"

    def test_output_delimiter(self):
        result = dedup_mstring("C^A^B^B", indlm="^#|*", dlm=",")
        assert result == "C,A,B"

    def test_single_char_indlm_default_dlm(self):
        """When indlm is a single char and dlm is not set, dlm = indlm."""
        result = dedup_mstring("A,B,B,C", indlm=",")
        assert result == "A,B,C"

    def test_complex_quoted_string(self):
        result = dedup_mstring(
            "'PERSON', \"ORGANISATION\", 'PERSON', 'ORGANISATION'",
            indlm=",",
        )
        assert "'PERSON'" in result
        assert result.count("'PERSON'") == 1


# ======================================================================
# export_csv tests
# ======================================================================

class TestExportCsv:
    """Validate export_csv against SAS %export_csv."""

    def test_basic(self, class_df: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "class.csv")
        result_path = export_csv(class_df, path)
        assert os.path.exists(result_path)
        loaded = pd.read_csv(result_path)
        assert len(loaded) == len(class_df)
        assert list(loaded.columns) == list(class_df.columns)

    def test_no_header(self, class_df: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "noheader.csv")
        export_csv(class_df, path, header=False)
        with open(path) as f:
            first_line = f.readline().strip()
        assert "Name" not in first_line

    def test_label_header(self, class_df: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "labelled.csv")
        labels = {"Name": "Student Name", "Age": "Age In Years"}
        export_csv(class_df, path, label=True, labels=labels)
        loaded = pd.read_csv(path)
        assert "Student Name" in loaded.columns
        assert "Age In Years" in loaded.columns

    def test_replace_false(self, class_df: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "exists.csv")
        export_csv(class_df, path)
        with pytest.raises(FileExistsError):
            export_csv(class_df, path, replace=False)

    def test_replace_true(self, class_df: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "replaceable.csv")
        export_csv(class_df, path)
        export_csv(class_df, path, replace=True)
        assert os.path.exists(path)

    def test_directory_path(self, class_df: pd.DataFrame, tmp_dir: str):
        result_path = export_csv(class_df, tmp_dir)
        assert result_path.endswith(".csv")
        assert os.path.exists(result_path)


# ======================================================================
# export_xlsx tests
# ======================================================================

class TestExportXlsx:
    """Validate export_xlsx against SAS %export_xlsx."""

    def test_basic(self, class_df: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "class.xlsx")
        result_path = export_xlsx(class_df, path)
        assert os.path.exists(result_path)
        loaded = pd.read_excel(result_path, engine="openpyxl")
        assert len(loaded) == len(class_df)

    def test_label(self, class_df: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "labelled.xlsx")
        labels = {"Name": "Student Name"}
        export_xlsx(class_df, path, label=True, labels=labels)
        loaded = pd.read_excel(path, engine="openpyxl")
        assert "Student Name" in loaded.columns

    def test_replace(self, class_df: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "replace.xlsx")
        export_xlsx(class_df, path)
        export_xlsx(class_df, path, replace=True)
        assert os.path.exists(path)


# ======================================================================
# export_dbms tests
# ======================================================================

class TestExportDbms:
    """Validate export_dbms against SAS %export_dbms."""

    def test_xlsx_default(self, class_df: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "default.xlsx")
        result_path = export_dbms(class_df, path)
        assert result_path.endswith(".xlsx")
        loaded = pd.read_excel(result_path, engine="openpyxl")
        assert len(loaded) == len(class_df)

    def test_csv(self, class_df: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "output.csv")
        result_path = export_dbms(class_df, path, dbms="csv")
        loaded = pd.read_csv(result_path)
        assert len(loaded) == len(class_df)

    def test_unsupported_dbms(self, class_df: pd.DataFrame, tmp_dir: str):
        with pytest.raises(ValueError, match="Unsupported"):
            export_dbms(class_df, tmp_dir, dbms="parquet")

    def test_bak_cleanup(self, class_df: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "cleanup.xlsx")
        bak = path + ".bak"
        with open(bak, "w") as f:
            f.write("dummy")
        export_dbms(class_df, path, dbms="xlsx")
        assert not os.path.exists(bak)

    def test_directory_path(self, class_df: pd.DataFrame, tmp_dir: str):
        result_path = export_dbms(class_df, tmp_dir, dbms="csv")
        assert os.path.exists(result_path)
        assert result_path.endswith(".csv")


# ======================================================================
# Integration tests with sample data
# ======================================================================

SAMPLE_DATA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "sample_data"
)


@pytest.fixture
def cust_accounts() -> pd.DataFrame:
    path = os.path.join(SAMPLE_DATA_DIR, "CUST_ACCOUNTS.csv")
    if not os.path.exists(path):
        pytest.skip("Sample data not available")
    return pd.read_csv(path)


@pytest.fixture
def daily_balance() -> pd.DataFrame:
    path = os.path.join(SAMPLE_DATA_DIR, "DAILY_BALANCE.csv")
    if not os.path.exists(path):
        pytest.skip("Sample data not available")
    return pd.read_csv(path)


@pytest.fixture
def monthly_amb() -> pd.DataFrame:
    path = os.path.join(SAMPLE_DATA_DIR, "MONTHLY_AMB.csv")
    if not os.path.exists(path):
        pytest.skip("Sample data not available")
    return pd.read_csv(path)


class TestIntegrationSampleData:
    """Run transforms against the actual migration sample datasets."""

    def test_subset_active_accounts(self, cust_accounts: pd.DataFrame):
        result = subset_data(
            cust_accounts,
            where='is_active == "ACTIVE"',
            keep=["customer_id", "account_id", "account_type", "start_date"],
        )
        assert "end_date" not in result.columns
        assert len(result) > 0
        assert all(result["customer_id"].notna())

    def test_subset_checking_accounts(self, cust_accounts: pd.DataFrame):
        result = subset_data(
            cust_accounts,
            where='account_type == "CHECKING"',
        )
        assert all(result["account_type"] == "CHECKING")

    def test_transpose_daily_balance(self, daily_balance: pd.DataFrame):
        """Transpose a small slice of daily balances by account."""
        sample = daily_balance.head(62)
        result = transpose(
            data=sample,
            by=["customer_id", "account_id", "month"],
            var=["end_of_day_balance"],
            name="measure",
        )
        assert len(result) > 0
        assert "measure" in result.columns

    def test_compare_cust_accounts_identity(self, cust_accounts: pd.DataFrame):
        result = compare(
            cust_accounts,
            cust_accounts.copy(),
            by=["customer_id", "account_id"],
        )
        assert result.equal is True

    def test_compare_cust_accounts_modified(self, cust_accounts: pd.DataFrame):
        modified = cust_accounts.copy()
        modified.loc[0, "account_type"] = "MODIFIED"
        result = compare(
            cust_accounts,
            modified,
            by=["customer_id", "account_id"],
        )
        assert result.equal is False
        assert len(result.value_diffs) >= 1

    def test_export_csv_monthly_amb(self, monthly_amb: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "monthly_amb_export.csv")
        export_csv(monthly_amb, path)
        loaded = pd.read_csv(path)
        assert len(loaded) == len(monthly_amb)
        pd.testing.assert_frame_equal(
            loaded.sort_values(["customer_id", "account_id"]).reset_index(drop=True),
            monthly_amb.sort_values(["customer_id", "account_id"]).reset_index(drop=True),
            check_dtype=False,
        )

    def test_export_xlsx_cust_accounts(self, cust_accounts: pd.DataFrame, tmp_dir: str):
        path = os.path.join(tmp_dir, "cust_accounts.xlsx")
        export_xlsx(cust_accounts, path)
        loaded = pd.read_excel(path, engine="openpyxl")
        assert len(loaded) == len(cust_accounts)

    def test_dedup_account_types(self, cust_accounts: pd.DataFrame):
        """Dedup a space-joined list of account types per customer."""
        grouped = (
            cust_accounts.groupby("customer_id")["account_type"]
            .apply(lambda x: " ".join(x))
            .reset_index()
        )
        grouped["unique_types"] = grouped["account_type"].apply(dedup_string)
        for _, row in grouped.iterrows():
            types = row["unique_types"].split()
            assert len(types) == len(set(t.upper() for t in types))
