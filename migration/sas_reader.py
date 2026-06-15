"""Read SAS7BDAT files and apply data type conversions for Snowflake loading.

Converts SAS numeric dates, byte-encoded strings, and SAS numeric IDs into
clean pandas DataFrames ready for CSV export or direct Snowflake ingestion.
"""

import logging
from pathlib import Path

import pandas as pd
import pyreadstat

logger = logging.getLogger(__name__)

# SAS format strings that indicate a date/datetime column
_DATE_FORMATS = frozenset(
    {
        "YYMMDD10",
        "DATE7",
        "DATE9",
        "DATE11",
        "MMDDYY10",
        "DDMMYY10",
        "DATETIME20",
    }
)


def _is_date_format(fmt: str) -> bool:
    return fmt.upper() in _DATE_FORMATS


def _coerce_dates(df: pd.DataFrame, variable_types: dict[str, str]) -> pd.DataFrame:
    """Convert columns whose SAS format indicates a date into proper datetime."""
    for col, fmt in variable_types.items():
        if col not in df.columns:
            continue
        if not _is_date_format(fmt):
            continue
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _coerce_integers(df: pd.DataFrame, variable_types: dict[str, str]) -> pd.DataFrame:
    """Downcast SAS BEST12 numeric IDs (stored as float64) to nullable Int64."""
    for col, fmt in variable_types.items():
        if col not in df.columns:
            continue
        if fmt.upper().startswith("BEST") and pd.api.types.is_float_dtype(df[col]):
            if (df[col].dropna() % 1 == 0).all():
                df[col] = df[col].astype("Int64")
    return df


def _decode_byte_strings(df: pd.DataFrame) -> pd.DataFrame:
    """Decode any byte/bytearray columns to UTF-8 strings and strip padding."""
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].apply(
                lambda x: (
                    x.decode("utf-8", errors="ignore").strip()
                    if isinstance(x, (bytes, bytearray))
                    else x
                )
            )
    return df


def read_sas_dataset(
    filepath: str | Path,
    encoding: str = "utf-8",
) -> tuple[pd.DataFrame, dict]:
    """Read a SAS7BDAT file and return a cleaned DataFrame plus metadata.

    Parameters
    ----------
    filepath : str or Path
        Path to a .sas7bdat file.
    encoding : str
        Character encoding for string columns (default UTF-8).

    Returns
    -------
    tuple[pd.DataFrame, dict]
        (df, metadata) where metadata contains column_names, variable_types,
        row_count, and file_encoding.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"SAS dataset not found: {filepath}")

    logger.info("Reading SAS dataset: %s", filepath)
    df, meta = pyreadstat.read_sas7bdat(str(filepath), encoding=encoding)

    variable_types: dict[str, str] = dict(meta.original_variable_types)
    metadata = {
        "column_names": list(meta.column_names),
        "variable_types": variable_types,
        "row_count": meta.number_rows,
        "file_encoding": meta.file_encoding,
        "source_path": str(filepath),
    }

    df = _decode_byte_strings(df)
    df = _coerce_dates(df, variable_types)
    df = _coerce_integers(df, variable_types)

    logger.info(
        "Loaded %d rows × %d cols from %s", len(df), len(df.columns), filepath.name
    )
    return df, metadata


def export_to_csv(
    df: pd.DataFrame,
    output_path: str | Path,
    date_format: str = "%Y-%m-%d",
) -> Path:
    """Export a DataFrame to a Snowflake-friendly CSV.

    - Dates formatted as YYYY-MM-DD
    - UTF-8 encoding, no BOM
    - NULL represented as empty string
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    df.to_csv(output_path, index=False, date_format=date_format, encoding="utf-8")
    logger.info("Exported %d rows to %s", len(df), output_path)
    return output_path


def read_all_datasets(
    data_dir: str | Path,
) -> dict[str, tuple[pd.DataFrame, dict]]:
    """Read all three SAS datasets from a directory.

    Expects CUST_ACCOUNTS.sas7bdat, DAILY_BALANCE.sas7bdat, MONTHLY_AMB.sas7bdat.

    Returns
    -------
    dict mapping dataset name to (DataFrame, metadata) tuples.
    """
    data_dir = Path(data_dir)
    dataset_names = ["CUST_ACCOUNTS", "DAILY_BALANCE", "MONTHLY_AMB"]
    results: dict[str, tuple[pd.DataFrame, dict]] = {}

    for name in dataset_names:
        sas_path = data_dir / f"{name}.sas7bdat"
        if sas_path.exists():
            results[name] = read_sas_dataset(sas_path)
        else:
            logger.warning("SAS file not found, skipping: %s", sas_path)

    return results


def apply_column_mapping(df: pd.DataFrame, table_name: str) -> pd.DataFrame:
    """Rename columns from SAS conventions to Snowflake target schema names.

    DAILY_BALANCE: ``date`` → ``BALANCE_DATE``, ``month`` → ``BALANCE_MONTH``
    All tables: column names uppercased.
    """
    rename_map: dict[str, str] = {}
    if table_name == "DAILY_BALANCE":
        rename_map = {"date": "BALANCE_DATE", "month": "BALANCE_MONTH"}
    df = df.rename(columns=rename_map)
    df.columns = [c.upper() for c in df.columns]
    return df


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Read SAS7BDAT files and export to CSV"
    )
    parser.add_argument(
        "data_dir",
        help="Directory containing .sas7bdat files (e.g. sample_data/)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="migration/output",
        help="Directory for exported CSV files (default: migration/output)",
    )
    args = parser.parse_args()

    datasets = read_all_datasets(args.data_dir)
    if not datasets:
        logger.error("No SAS datasets found in %s", args.data_dir)
        sys.exit(1)

    for name, (df, meta) in datasets.items():
        df = apply_column_mapping(df, name)
        out_path = export_to_csv(df, Path(args.output_dir) / f"{name}.csv")
        print(f"  {name}: {meta['row_count']} rows → {out_path}")

    print(f"\nExported {len(datasets)} dataset(s) to {args.output_dir}/")
