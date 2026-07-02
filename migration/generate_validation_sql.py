"""Generate Snowflake validation SQL from the prepared source metrics."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from migration.table_specs import TABLE_SPECS, TableSpec  # noqa: E402

OUTPUT_DIR = REPO_ROOT / "build" / "snowflake_load"
METRICS_PATH = OUTPUT_DIR / "source_metrics.json"
VALIDATION_SQL_PATH = OUTPUT_DIR / "VALIDATION.sql"


# ── SQL builders ─────────────────────────────────────────────────────────

def target_column_name(spec: TableSpec, source_column: str) -> str:
    """Translate a source column name into its Snowflake target name."""
    for column_spec in spec.columns:
        if column_spec.source_col == source_column:
            return column_spec.target_col
    raise KeyError(f"Unknown source column {source_column!r} for {spec.table_name}")


def distinct_tuple_expression(spec: TableSpec) -> str:
    """Build a Snowflake expression that counts distinct key tuples."""
    parts = [f"COALESCE(TO_VARCHAR({target_column_name(spec, column)}), '')" for column in spec.natural_key_columns]
    return f"COUNT(DISTINCT CONCAT_WS('||', {', '.join(parts)})) AS DISTINCT_NATURAL_KEY"


def table_select(spec: TableSpec, metrics: dict[str, Any] | None) -> str:
    """Build the validation SELECT for one table."""
    select_lines = [
        f"SELECT '{spec.table_name}' AS TABLE_NAME",
        "     , COUNT(*) AS ROW_COUNT",
    ]

    for source_column in spec.numeric_columns:
        target_column = target_column_name(spec, source_column)
        select_lines.append(
            f"     , SUM({target_column}) AS SUM_{target_column}"
        )

    for source_column in spec.distinct_count_columns:
        target_column = target_column_name(spec, source_column)
        select_lines.append(
            f"     , COUNT(DISTINCT {target_column}) AS DISTINCT_{target_column}"
        )

    select_lines.append(f"     , {distinct_tuple_expression(spec)}")
    select_lines.append(
        "     , HASH_AGG(TO_JSON(OBJECT_CONSTRUCT_KEEP_NULL(*))) AS HASH_AGG_ALL_COLUMNS"
    )
    select_lines.append(f"FROM SAS_MIGRATION.{spec.target_schema}.{spec.table_name};")

    if metrics is None:
        return "\n".join(select_lines)

    expected_block = json.dumps(metrics, indent=2, sort_keys=True).splitlines()
    select_lines.append("")
    select_lines.append(f"-- Expected SAS-side metrics for {spec.table_name}:")
    select_lines.extend(f"-- {line}" for line in expected_block)
    return "\n".join(select_lines)


def write_validation_sql(output_path: Path, metrics: dict[str, Any] | None) -> None:
    """Write the generated SQL file."""
    header = """/*=====================================================================
  VALIDATION.sql — Snowflake-side checks for SAS migration parity

  HASH_AGG is used for internal drift detection across Snowflake loads and
  is not cross-engine comparable with SAS source values.
=====================================================================*/
"""
    parts = [header.rstrip()]
    for spec in TABLE_SPECS:
        table_metrics = metrics.get(spec.table_name) if metrics else None
        parts.append(table_select(spec, table_metrics))
        parts.append("")

    output_path.write_text("\n\n".join(parts).rstrip() + "\n", encoding="utf-8")


# ── main ─────────────────────────────────────────────────────────────────

def main() -> None:
    """Emit VALIDATION.sql using the source metrics manifest if present."""
    metrics: dict[str, Any] | None = None
    if METRICS_PATH.exists():
        metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_validation_sql(VALIDATION_SQL_PATH, metrics)
    print(f"Wrote validation SQL to {VALIDATION_SQL_PATH}")


if __name__ == "__main__":
    main()
