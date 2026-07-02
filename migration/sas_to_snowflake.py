"""Prepare SAS samples for Snowflake loading.

This CLI reads the immutable SAS baselines in ``sample_data/``, normalizes
them into Snowflake-ready CSVs, emits explicit COPY INTO commands, and writes
source-side reconciliation metrics for later validation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

try:  # pragma: no cover - optional import path is exercised at runtime.
    import pyreadstat
except ImportError:  # pragma: no cover
    pyreadstat = None

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from migration.table_specs import TABLE_BY_NAME, TABLE_SPECS, TableSpec  # noqa: E402

SAMPLE_DIR = REPO_ROOT / "sample_data"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "build" / "snowflake_load"
TARGET_DATABASE = "SAS_MIGRATION"
STAGE_NAME = f"{TARGET_DATABASE}.RAW.SAS_MIGRATION_STAGE"
FILE_FORMAT_NAME = f"{TARGET_DATABASE}.RAW.SAS_MIGRATION_CSV_FILE_FORMAT"


# ── loaders ──────────────────────────────────────────────────────────────

def read_sas_table(path: Path) -> pd.DataFrame:
    """Load a SAS7BDAT file with pyreadstat first and pandas as fallback."""
    if pyreadstat is not None:
        df, _ = pyreadstat.read_sas7bdat(str(path))
        return df
    return pd.read_sas(path, format="sas7bdat")


# ── normalization helpers ────────────────────────────────────────────────

def load_and_clean_table(spec: TableSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return the raw decoded SAS frame and the Snowflake-ready frame."""
    sas_path = SAMPLE_DIR / spec.sas_filename
    raw_df = read_sas_table(sas_path).map(
        lambda value: value.decode("utf-8", errors="ignore").strip()
        if isinstance(value, (bytes, bytearray))
        else value
    )

    cleaned_columns: dict[str, pd.Series] = {}
    for column_spec in spec.columns:
        series = raw_df[column_spec.source_col]
        cleaned_columns[column_spec.target_col] = column_spec.converter(series)

    cleaned_df = pd.DataFrame(cleaned_columns, columns=spec.target_columns)
    return raw_df, cleaned_df


def source_metrics(cleaned_df: pd.DataFrame, spec: TableSpec) -> dict[str, Any]:
    """Compute row count, checksum sums, and distinct-count metrics.

    Metrics are computed from the cleaned (Snowflake-ready) frame using the
    target column names so they compare apples-to-apples with the SQL emitted
    by generate_validation_sql.py, which aggregates the loaded Snowflake table.
    """
    to_target = {c.source_col: c.target_col for c in spec.columns}
    key_targets = [to_target[c] for c in spec.natural_key_columns]
    metrics: dict[str, Any] = {
        "row_count": int(len(cleaned_df)),
        "numeric_sums": {},
        "distinct_counts": {},
        "natural_key_distinct_count": 0,
        "natural_key_columns": key_targets,
    }

    for column_name in spec.numeric_columns:
        target = to_target[column_name]
        metrics["numeric_sums"][target] = round(
            float(pd.to_numeric(cleaned_df[target], errors="coerce").sum()),
            2,
        )

    for column_name in spec.distinct_count_columns:
        target = to_target[column_name]
        metrics["distinct_counts"][target] = int(cleaned_df[target].nunique())

    if key_targets:
        metrics["natural_key_distinct_count"] = int(
            cleaned_df.loc[:, key_targets].drop_duplicates().shape[0]
        )

    return metrics


def write_clean_csv(cleaned_df: pd.DataFrame, output_path: Path) -> None:
    """Persist the Snowflake-ready CSV with empty strings for nulls."""
    cleaned_df.to_csv(output_path, index=False, na_rep="", float_format="%.2f")


def copy_statement(spec: TableSpec, csv_path: Path) -> str:
    """Build the PUT and COPY statements for a table."""
    column_list = ", ".join(spec.target_columns)
    return (
        f"PUT file://{csv_path.as_posix()} @{STAGE_NAME} AUTO_COMPRESS=FALSE OVERWRITE=TRUE;\n"
        f"COPY INTO {TARGET_DATABASE}.{spec.target_schema}.{spec.table_name} ({column_list})\n"
        f"FROM @{STAGE_NAME}/{csv_path.name}\n"
        f"FILE_FORMAT=(FORMAT_NAME={FILE_FORMAT_NAME})\n"
        "ON_ERROR='ABORT_STATEMENT' PURGE=FALSE;"
    )


def write_copy_file(stmts: list[str], output_path: Path) -> None:
    """Write the COPY/PUT commands for all tables."""
    header = """/*=====================================================================
  COPY_INTO.sql — Snowflake load commands for the SAS migration samples

  Generated from migration/table_specs.py. Upload the CSV files in
  build/snowflake_load/ to the internal stage before executing COPY INTO.
=====================================================================*/
"""
    output_path.write_text(header + "\n\n".join(stmts) + "\n", encoding="utf-8")


def write_metrics_file(metrics: dict[str, dict[str, Any]], output_path: Path) -> None:
    """Write the source-side reconciliation metrics manifest."""
    output_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def migrate_table(spec: TableSpec, output_dir: Path) -> dict[str, Any]:
    """Convert one SAS table to CSV and return its source metrics."""
    _raw_df, cleaned_df = load_and_clean_table(spec)
    csv_path = output_dir / f"{spec.table_name}.csv"
    write_clean_csv(cleaned_df, csv_path)
    metrics = source_metrics(cleaned_df, spec)
    print(
        f"{spec.table_name}: rows={metrics['row_count']}, "
        f"csv={csv_path.relative_to(REPO_ROOT)}, "
        f"distinct_keys={metrics['natural_key_distinct_count']}"
    )
    return metrics


# ── main ─────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Convert SAS sample data into Snowflake load artifacts",
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--table", choices=sorted(TABLE_BY_NAME), help="Single table to process")
    scope.add_argument("--all", action="store_true", help="Process all tables")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for generated CSV, SQL, and JSON artifacts",
    )
    return parser.parse_args()


def main() -> None:
    """Run the migration prep workflow."""
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tables = [TABLE_BY_NAME[args.table]] if args.table else list(TABLE_SPECS)

    # Single-table runs update only their entry so a prior --all manifest is
    # not silently reduced to one table.
    metrics_path = output_dir / "source_metrics.json"
    metrics_manifest: dict[str, dict[str, Any]] = {}
    if args.table and metrics_path.exists():
        metrics_manifest = json.loads(metrics_path.read_text(encoding="utf-8"))

    for spec in tables:
        metrics_manifest[spec.table_name] = migrate_table(spec, output_dir)

    # COPY commands are data-independent, so always emit the full set.
    copy_statements = [
        copy_statement(spec, output_dir / f"{spec.table_name}.csv")
        for spec in TABLE_SPECS
    ]

    write_metrics_file(metrics_manifest, metrics_path)
    write_copy_file(copy_statements, output_dir / "COPY_INTO.sql")

    print(f"Wrote metrics to {output_dir / 'source_metrics.json'}")
    print(f"Wrote COPY commands to {output_dir / 'COPY_INTO.sql'}")


if __name__ == "__main__":
    main()
