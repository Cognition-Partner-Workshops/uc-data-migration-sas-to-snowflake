"""
Python equivalents of SAS data-transformation macros.

Each function mirrors the signature of the original SAS macro found in
ts-sas-legacy-codebase/Macro/.  Input and output are pandas DataFrames
so callers can chain operations in the same style as SAS DATA steps.

Translated macros
-----------------
- transpose        (PROC TRANSPOSE wrapper)
- subset_data      (DATA step subsetting)
- compare          (PROC COMPARE wrapper)
- dedup_string     (DATA step duplicate-token removal)
- dedup_mstring    (Macro-level duplicate-token removal)
- export_csv       (PROC EXPORT → CSV)
- export_xlsx      (PROC EXPORT → XLSX)
- export_dbms      (PROC EXPORT generic)
"""

from __future__ import annotations

import os
import re
from typing import Callable, Sequence

import pandas as pd


# ---------------------------------------------------------------------------
# transpose  –  SAS %transpose macro  (transpose.sas)
# ---------------------------------------------------------------------------

def transpose(
    data: pd.DataFrame,
    by: list[str] | None = None,
    var: list[str] | None = None,
    sort: bool = True,
    notsorted: bool = False,
    id_col: str | None = None,
    id_label: str | None = None,
    let: bool = False,
    copy: list[str] | None = None,
    where: str | None = None,
    prefix: str | None = None,
    name: str | None = "_NAME_",
    label: str | None = "_LABEL_",
    col: list[str] | None = None,
) -> pd.DataFrame:
    """Transpose *data*, replicating SAS ``PROC TRANSPOSE``.

    Parameters
    ----------
    data : DataFrame
        Input dataset.
    by : list[str], optional
        Group-by columns.  When *sort* is ``True`` (default) the frame
        is sorted on these columns first, matching the SAS default.
    var : list[str], optional
        Columns to transpose.  ``None`` → all numeric columns.
    sort : bool
        Sort *data* by *by* before transposing (default ``True``).
    notsorted : bool
        If ``True``, *sort* is forced to ``False`` and group order is
        preserved as-is (mirrors the SAS ``NOTSORTED`` option).
    id_col : str, optional
        Column whose values become the output column names (SAS ``ID``
        statement).  Mutually exclusive with *col*.
    id_label : str, optional
        Column whose values label the ``id_col`` output columns.
        Requires *id_col*.
    let : bool
        When ``True`` and *id_col* is set, keep only the last occurrence
        per group (SAS ``LET`` option).
    copy : list[str], optional
        Columns copied through without transposing (SAS ``COPY``).
    where : str, optional
        Row filter applied to *data* before transposing.  Passed to
        ``DataFrame.query()``.
    prefix : str, optional
        Prefix for generated value columns (default ``COL``).
    name : str or None
        Output column holding the original variable name.  ``None`` →
        drop it.  Default ``"_NAME_"``.
    label : str or None
        Output column holding the original variable label.  Since pandas
        DataFrames do not carry labels, the column is omitted if ``None``
        or filled with empty strings otherwise.
    col : list[str], optional
        Rename the generated value columns ``COL1 … COLn`` to these
        names.  Mutually exclusive with *id_col*.

    Returns
    -------
    DataFrame
    """
    if col is not None and id_col is not None:
        raise ValueError("Only one of 'col' or 'id_col' may be specified.")
    if id_label is not None and id_col is None:
        raise ValueError("'id_label' requires 'id_col'.")

    df = data.copy()

    if where is not None:
        df = df.query(where)

    if notsorted:
        sort = False

    if var is None:
        var = list(df.select_dtypes(include="number").columns)

    if by and sort:
        df = df.sort_values(by).reset_index(drop=True)

    pfx = prefix if prefix else "COL"

    if id_col is not None:
        if let:
            df = df.drop_duplicates(subset=(by or []) + [id_col], keep="last")
        result = df.pivot_table(
            index=by or [],
            columns=id_col,
            values=var,
            aggfunc="first",
        )
        if len(var) == 1:
            result.columns = result.columns.droplevel(0)
        else:
            result.columns = [
                f"{v}_{c}" for v, c in result.columns
            ]
        result = result.reset_index()
        return result

    # Standard (non-ID) transpose
    copy_data: pd.DataFrame | None = None
    if copy:
        if by:
            copy_data = df.groupby(by, sort=False)[copy].first().reset_index()
        else:
            copy_data = df[copy].head(1)

    if by:
        melted = df.melt(id_vars=by, value_vars=var,
                         var_name="_NAME_", value_name="_VALUE_")
        melted["_seq"] = melted.groupby(by + ["_NAME_"]).cumcount()
        pivoted = melted.pivot_table(
            index=by + ["_NAME_"],
            columns="_seq",
            values="_VALUE_",
            aggfunc="first",
        )
        pivoted.columns = [f"{pfx}{int(c) + 1}" for c in pivoted.columns]
        result = pivoted.reset_index()
    else:
        rows: list[dict] = []
        for v in var:
            row: dict = {"_NAME_": v}
            for idx, val in enumerate(df[v]):
                row[f"{pfx}{idx + 1}"] = val
            rows.append(row)
        result = pd.DataFrame(rows)

    # Rename COL# → user-supplied names
    if col is not None:
        col_cols = [c for c in result.columns if c.startswith(pfx)]
        renames = {}
        for i, new_name in enumerate(col):
            old_name = f"{pfx}{i + 1}"
            if old_name in col_cols:
                renames[old_name] = new_name
        result = result.rename(columns=renames)

    # Handle _NAME_ / _LABEL_
    if name is None:
        result = result.drop(columns=["_NAME_"], errors="ignore")
    elif name != "_NAME_":
        result = result.rename(columns={"_NAME_": name})

    if label is not None and label != "":
        label_col_name = label if label != "_LABEL_" else "_LABEL_"
        name_key = name if name else "_NAME_"
        if name_key in result.columns:
            result.insert(
                result.columns.get_loc(name_key) + 1,
                label_col_name,
                "",
            )
    elif label is None:
        result = result.drop(columns=["_LABEL_"], errors="ignore")

    if copy_data is not None:
        merge_keys = by if by else []
        if merge_keys:
            result = result.merge(copy_data, on=merge_keys, how="left")
        else:
            for c in copy_data.columns:
                result[c] = copy_data[c].iloc[0]

    return result


# ---------------------------------------------------------------------------
# subset_data  –  SAS %subset_data macro  (subset_data.sas)
# ---------------------------------------------------------------------------

def subset_data(
    data: pd.DataFrame,
    where: str | None = None,
    if_func: Callable[[pd.DataFrame], pd.Series] | None = None,
    firstobs: int | None = None,
    lastobs: int | None = None,
    obs: str | None = None,
    keep: list[str] | None = None,
    drop: list[str] | None = None,
    rename: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Subset *data* by rows and columns.

    Parameters
    ----------
    data : DataFrame
        Input dataset.
    where : str, optional
        Row filter expression (``DataFrame.query`` syntax).
    if_func : callable, optional
        A function ``f(df) → bool Series`` used as a subsetting-if
        (applied *after* rename, matching SAS semantics).
    firstobs, lastobs : int, optional
        1-based row bounds (inclusive).
    obs : str, optional
        Non-contiguous observation ranges, e.g. ``"1-5 or 11-15"``.
        Parsed the same way as the SAS macro.
    keep : list[str], optional
        Columns to keep.
    drop : list[str], optional
        Columns to drop.
    rename : dict, optional
        ``{old_name: new_name}`` mapping applied *before* where / keep.

    Returns
    -------
    DataFrame
    """
    df = data.copy()

    # Apply firstobs / lastobs (1-based, inclusive)
    if firstobs is not None or lastobs is not None:
        start = (firstobs - 1) if firstobs else 0
        end = lastobs if lastobs else len(df)
        df = df.iloc[start:end].reset_index(drop=True)

    # Rename
    if rename:
        df = df.rename(columns=rename)

    # OBS ranges  e.g. "1-5 or 11-15 or 20"
    if obs is not None:
        mask = pd.Series(False, index=df.index)
        for part in re.split(r"\s+or\s+", obs, flags=re.IGNORECASE):
            part = part.strip()
            m = re.match(r"(\d+)\s*-\s*(\d+)", part)
            if m:
                lo, hi = int(m.group(1)), int(m.group(2))
                # 1-based inclusive → 0-based
                mask |= (df.index >= lo - 1) & (df.index <= hi - 1)
            elif part.isdigit():
                mask |= df.index == int(part) - 1
        df = df.loc[mask].reset_index(drop=True)

    # Where clause
    if where is not None:
        df = df.query(where).reset_index(drop=True)

    # Subsetting if
    if if_func is not None:
        df = df.loc[if_func(df)].reset_index(drop=True)

    # Keep / Drop
    if keep is not None:
        df = df[keep]
    if drop is not None:
        df = df.drop(columns=drop, errors="ignore")

    return df


# ---------------------------------------------------------------------------
# compare  –  SAS %compare macro  (compare.sas)
# ---------------------------------------------------------------------------

class CompareResult:
    """Container for the output of :func:`compare`."""

    def __init__(
        self,
        equal: bool,
        common_columns: list[str],
        base_only_columns: list[str],
        comp_only_columns: list[str],
        base_only_rows: pd.DataFrame,
        comp_only_rows: pd.DataFrame,
        value_diffs: pd.DataFrame,
        base_nobs: int,
        comp_nobs: int,
    ) -> None:
        self.equal = equal
        self.common_columns = common_columns
        self.base_only_columns = base_only_columns
        self.comp_only_columns = comp_only_columns
        self.base_only_rows = base_only_rows
        self.comp_only_rows = comp_only_rows
        self.value_diffs = value_diffs
        self.base_nobs = base_nobs
        self.comp_nobs = comp_nobs

    def __repr__(self) -> str:
        return (
            f"CompareResult(equal={self.equal}, "
            f"value_diffs={len(self.value_diffs)} rows, "
            f"base_only_rows={len(self.base_only_rows)}, "
            f"comp_only_rows={len(self.comp_only_rows)})"
        )


def compare(
    base: pd.DataFrame,
    comp: pd.DataFrame,
    by: list[str] | None = None,
    criterion: float = 1e-6,
    method: str = "exact",
) -> CompareResult:
    """Compare two DataFrames, replicating SAS ``PROC COMPARE``.

    Parameters
    ----------
    base, comp : DataFrame
        The two datasets to compare.
    by : list[str], optional
        Key columns that uniquely identify a row.  Both frames are
        sorted by these columns before comparison.
    criterion : float
        Numeric fuzz factor (ignored when *method* is ``"exact"``).
    method : str
        ``"exact"`` or ``"absolute"``.

    Returns
    -------
    CompareResult
    """
    method = method.lower()
    base_cols = set(base.columns)
    comp_cols = set(comp.columns)
    common = sorted(base_cols & comp_cols)
    base_only_cols = sorted(base_cols - comp_cols)
    comp_only_cols = sorted(comp_cols - base_cols)

    b = base.copy()
    c = comp.copy()

    if by:
        b = b.sort_values(by).reset_index(drop=True)
        c = c.sort_values(by).reset_index(drop=True)

    # Row-level differences (by key)
    if by:
        merged = b[by].merge(c[by], on=by, how="outer", indicator=True)
        base_only_rows = merged.loc[merged["_merge"] == "left_only", by].reset_index(drop=True)
        comp_only_rows = merged.loc[merged["_merge"] == "right_only", by].reset_index(drop=True)
        both = merged.loc[merged["_merge"] == "both", by].reset_index(drop=True)
        b_matched = b.merge(both, on=by, how="inner").reset_index(drop=True)
        c_matched = c.merge(both, on=by, how="inner").reset_index(drop=True)
    else:
        min_len = min(len(b), len(c))
        base_only_rows = b.iloc[min_len:].reset_index(drop=True) if len(b) > min_len else pd.DataFrame()
        comp_only_rows = c.iloc[min_len:].reset_index(drop=True) if len(c) > min_len else pd.DataFrame()
        b_matched = b.iloc[:min_len].reset_index(drop=True)
        c_matched = c.iloc[:min_len].reset_index(drop=True)

    # Value-level differences on common columns
    compare_cols = [col for col in common if col not in (by or [])]
    diff_rows: list[dict] = []
    for col in compare_cols:
        bv = b_matched[col] if col in b_matched.columns else pd.Series(dtype=object)
        cv = c_matched[col] if col in c_matched.columns else pd.Series(dtype=object)
        for i in range(len(bv)):
            base_val = bv.iloc[i]
            comp_val = cv.iloc[i]
            is_diff = False
            if pd.isna(base_val) and pd.isna(comp_val):
                continue
            if pd.isna(base_val) or pd.isna(comp_val):
                is_diff = True
            elif isinstance(base_val, (int, float)) and isinstance(comp_val, (int, float)):
                if method == "exact":
                    is_diff = base_val != comp_val
                else:
                    is_diff = abs(base_val - comp_val) > criterion
            else:
                is_diff = base_val != comp_val
            if is_diff:
                row_info: dict = {"_row_": i, "_column_": col,
                                  "_base_": base_val, "_comp_": comp_val}
                if by:
                    for k in by:
                        row_info[k] = b_matched[k].iloc[i]
                diff_rows.append(row_info)

    value_diffs = pd.DataFrame(diff_rows)
    equal = (
        len(base_only_cols) == 0
        and len(comp_only_cols) == 0
        and len(base_only_rows) == 0
        and len(comp_only_rows) == 0
        and len(value_diffs) == 0
    )

    return CompareResult(
        equal=equal,
        common_columns=common,
        base_only_columns=base_only_cols,
        comp_only_columns=comp_only_cols,
        base_only_rows=base_only_rows,
        comp_only_rows=comp_only_rows,
        value_diffs=value_diffs,
        base_nobs=len(base),
        comp_nobs=len(comp),
    )


# ---------------------------------------------------------------------------
# dedup_string  –  SAS %dedup_string macro  (dedup_string.sas)
# ---------------------------------------------------------------------------

def dedup_string(input_str: str, dlm: str = " ") -> str:
    """Remove duplicate tokens from *input_str*.

    Operates at the DATA-step level: takes a single string value and
    returns it with duplicate tokens removed (first occurrence kept).
    Case-insensitive comparison, but original casing is preserved.

    Parameters
    ----------
    input_str : str
        Delimited string, e.g. ``"C A B B A G E"``.
    dlm : str
        Token delimiter (default space).

    Returns
    -------
    str
    """
    tokens = input_str.split(dlm) if dlm == " " else input_str.split(dlm)
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        token_stripped = token.strip()
        if not token_stripped:
            continue
        key = token_stripped.upper()
        if key not in seen:
            seen.add(key)
            result.append(token_stripped)
    return dlm.join(result) if dlm != " " else " ".join(result)


# ---------------------------------------------------------------------------
# dedup_mstring  –  SAS %dedup_mstring macro  (dedup_mstring.sas)
# ---------------------------------------------------------------------------

def dedup_mstring(
    input_str: str,
    indlm: str = " ",
    dlm: str | None = None,
) -> str:
    """Remove duplicate tokens from a macro-variable-style string.

    The SAS ``%dedup_mstring`` macro operates at compile time on macro
    variables.  In Python the effect is the same as :func:`dedup_string`
    but with separate input / output delimiter control.

    Parameters
    ----------
    input_str : str
    indlm : str
        Input delimiter (may be multi-character; each character is a
        distinct delimiter).
    dlm : str, optional
        Output delimiter.  Defaults to *indlm* when ``len(indlm) == 1``
        or a space when ``len(indlm) > 1``.

    Returns
    -------
    str
    """
    if dlm is None:
        dlm = indlm if len(indlm) == 1 else " "

    if len(indlm) == 1:
        tokens = input_str.split(indlm)
    else:
        pattern = "[" + re.escape(indlm) + "]"
        tokens = re.split(pattern, input_str)

    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        key = token.upper()
        if key not in seen:
            seen.add(key)
            result.append(token)
    return dlm.join(result)


# ---------------------------------------------------------------------------
# export_csv  –  SAS %export_csv macro  (export_csv.sas)
# ---------------------------------------------------------------------------

def export_csv(
    data: pd.DataFrame,
    path: str,
    replace: bool = False,
    label: bool = False,
    header: bool = True,
    labels: dict[str, str] | None = None,
) -> str:
    """Export *data* to a CSV file.

    Parameters
    ----------
    data : DataFrame
    path : str
        Output file or directory.  If a directory, the file is named
        ``data.csv``.  (Caller should provide a full path in practice.)
    replace : bool
        Overwrite existing file.
    label : bool
        Use column labels (from *labels* dict) instead of column names
        for the header row.
    header : bool
        Write a header row.
    labels : dict, optional
        ``{column_name: label}`` mapping used when *label* is ``True``.

    Returns
    -------
    str  – resolved output file path.
    """
    path = _resolve_export_path(path, "csv")
    _check_replace(path, replace)

    cols = data.columns.tolist()
    if label and labels:
        header_row = [labels.get(c, c) for c in cols]
    elif header:
        header_row = cols  # type: ignore[assignment]
    else:
        header_row = False  # type: ignore[assignment]

    data.to_csv(path, index=False, header=header_row)
    return path


# ---------------------------------------------------------------------------
# export_xlsx  –  SAS %export_xlsx macro  (export_xlsx.sas)
# ---------------------------------------------------------------------------

def export_xlsx(
    data: pd.DataFrame,
    path: str,
    replace: bool = False,
    label: bool = False,
    labels: dict[str, str] | None = None,
) -> str:
    """Export *data* to an XLSX (Excel) file.

    Parameters
    ----------
    data : DataFrame
    path : str
    replace : bool
    label : bool
    labels : dict, optional

    Returns
    -------
    str  – resolved output file path.
    """
    path = _resolve_export_path(path, "xlsx")
    _check_replace(path, replace)

    header: list[str] | bool = True
    if label and labels:
        header = [labels.get(c, c) for c in data.columns]

    data.to_excel(path, index=False, header=header, engine="openpyxl")
    return path


# ---------------------------------------------------------------------------
# export_dbms  –  SAS %export_dbms macro  (export_dbms.sas)
# ---------------------------------------------------------------------------

_DBMS_EXT: dict[str, str] = {
    "xlsx": "xlsx",
    "xls": "xls",
    "csv": "csv",
    "spss": "sav",
    "stata": "dta",
}


def export_dbms(
    data: pd.DataFrame,
    path: str,
    dbms: str = "xlsx",
    replace: bool = False,
    label: bool = False,
    labels: dict[str, str] | None = None,
) -> str:
    """Generic export matching SAS ``PROC EXPORT``.

    Dispatches to the appropriate pandas writer based on *dbms*.

    Parameters
    ----------
    data : DataFrame
    path : str
    dbms : str
        One of ``xlsx``, ``xls``, ``csv``, ``spss``, ``stata``.
    replace : bool
    label : bool
    labels : dict, optional

    Returns
    -------
    str  – resolved output file path.
    """
    dbms = dbms.lower()
    if dbms not in _DBMS_EXT:
        raise ValueError(f"Unsupported dbms: {dbms!r}")

    ext = _DBMS_EXT[dbms]
    path = _resolve_export_path(path, ext)
    _check_replace(path, replace)

    header: list[str] | bool = True
    if label and labels:
        header = [labels.get(c, c) for c in data.columns]

    if dbms in ("xlsx", "xls"):
        engine = "openpyxl" if dbms == "xlsx" else "xlwt"
        data.to_excel(path, index=False, header=header, engine=engine)
    elif dbms == "csv":
        data.to_csv(path, index=False, header=header)
    elif dbms == "stata":
        data.to_stata(path, write_index=False)
    elif dbms == "spss":
        raise NotImplementedError("SPSS export requires pyreadstat.")

    # Clean up .bak file (mirrors SAS PROC EXPORT behaviour)
    bak = path + ".bak"
    if os.path.exists(bak):
        os.remove(bak)

    return path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _resolve_export_path(path: str, ext: str) -> str:
    """If *path* is a directory, append a default filename."""
    path = path.strip("'\"")
    if os.path.isdir(path):
        path = os.path.join(path, f"output.{ext}")
    elif not os.path.splitext(path)[1]:
        path = f"{path}.{ext}"
    return path


def _check_replace(path: str, replace: bool) -> None:
    if os.path.exists(path) and not replace:
        raise FileExistsError(
            f"{path!r} already exists. Specify replace=True to overwrite."
        )
