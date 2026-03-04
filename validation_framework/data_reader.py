"""
Data reader module for loading SAS source and Snowflake target datasets.

Supports:
- CSV files (primary format for both Scenario1/Scenario2 proxies)
- SAS .sas7bdat files (native SAS format, read via pandas)

Column names are normalized to lowercase for consistent comparison.
"""

from pathlib import Path
from typing import Dict, List

import pandas as pd


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to lowercase for consistent comparisons."""
    df.columns = [col.strip().lower() for col in df.columns]
    return df


def _decode_bytes_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Decode byte-string columns from SAS files to regular strings."""
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].apply(
                lambda x: x.decode("utf-8", errors="ignore").strip()
                if isinstance(x, (bytes, bytearray))
                else x
            )
    return df


def read_csv_file(file_path: Path) -> pd.DataFrame:
    """
    Read a CSV file into a DataFrame with normalized columns.

    Args:
        file_path: Path to the CSV file.

    Returns:
        DataFrame with lowercase column names.

    Raises:
        FileNotFoundError: If the CSV file does not exist.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"CSV file not found: {file_path}")
    df = pd.read_csv(file_path)
    return _normalize_columns(df)


def read_sas_file(file_path: Path) -> pd.DataFrame:
    """
    Read a SAS .sas7bdat file into a DataFrame with normalized columns.

    Handles byte-string decoding common in SAS character fields.

    Args:
        file_path: Path to the .sas7bdat file.

    Returns:
        DataFrame with lowercase column names and decoded string values.

    Raises:
        FileNotFoundError: If the SAS file does not exist.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"SAS file not found: {file_path}")
    df = pd.read_sas(file_path, format="sas7bdat")
    df = _decode_bytes_columns(df)
    return _normalize_columns(df)


def load_table(
    directory: Path,
    table_name: str,
    prefer_sas: bool = False,
) -> pd.DataFrame:
    """
    Load a table from a directory, trying CSV first (or SAS first if preferred).

    Args:
        directory: Directory containing data files.
        table_name: Table name (e.g., "MONTHLY_AMB"). Used to find
                    files named {table_name}.csv or {table_name}.sas7bdat.
        prefer_sas: If True, try .sas7bdat before .csv.

    Returns:
        Loaded DataFrame.

    Raises:
        FileNotFoundError: If neither CSV nor SAS file is found.
    """
    csv_path = directory / f"{table_name}.csv"
    sas_path = directory / f"{table_name}.sas7bdat"

    if prefer_sas:
        if sas_path.exists():
            return read_sas_file(sas_path)
        if csv_path.exists():
            return read_csv_file(csv_path)
    else:
        if csv_path.exists():
            return read_csv_file(csv_path)
        if sas_path.exists():
            return read_sas_file(sas_path)

    raise FileNotFoundError(
        f"No data file found for table '{table_name}' in {directory}. "
        f"Looked for: {csv_path}, {sas_path}"
    )


def discover_tables(directory: Path) -> List[str]:
    """
    Discover available table names in a directory based on CSV/SAS files.

    Args:
        directory: Directory to scan.

    Returns:
        Sorted list of unique table names (uppercase, no extension).
    """
    tables: set = set()
    if not directory.is_dir():
        return []

    for path in directory.iterdir():
        if path.suffix.lower() in (".csv", ".sas7bdat"):
            tables.add(path.stem.upper())

    return sorted(tables)


def load_source_and_target(
    source_dir: Path,
    target_dir: Path,
    table_name: str,
    source_prefer_sas: bool = False,
) -> Dict[str, pd.DataFrame]:
    """
    Load both source and target DataFrames for a given table.

    Args:
        source_dir: Path to SAS source data directory (e.g., Scenario1/).
        target_dir: Path to Snowflake target data directory (e.g., Scenario2/).
        table_name: Table name to load.
        source_prefer_sas: Whether to prefer .sas7bdat for source data.

    Returns:
        Dict with keys "source" and "target" mapping to DataFrames.
    """
    return {
        "source": load_table(source_dir, table_name, prefer_sas=source_prefer_sas),
        "target": load_table(target_dir, table_name, prefer_sas=False),
    }
