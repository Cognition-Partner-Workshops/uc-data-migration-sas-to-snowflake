"""
Report generation for validation results.

Produces:
- Summary CSV with one row per validation check
- Styled HTML report with color-coded pass/fail status,
  summary statistics, and per-table breakdown
"""

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pandas as pd

from validation_framework.validators import ValidationResult


def results_to_dataframe(results: List[ValidationResult]) -> pd.DataFrame:
    """
    Convert a list of ValidationResult objects to a pandas DataFrame.

    Args:
        results: List of validation results.

    Returns:
        DataFrame with columns matching ValidationResult fields.
    """
    if not results:
        return pd.DataFrame(
            columns=[
                "rule_id",
                "table",
                "rule_type",
                "rule_display_name",
                "column",
                "source_value",
                "target_value",
                "delta",
                "status",
                "detail",
            ]
        )

    records = []
    for r in results:
        records.append(
            {
                "rule_id": r.rule_id,
                "table": r.table,
                "rule_type": r.rule_type,
                "rule_display_name": r.rule_display_name,
                "column": r.column,
                "source_value": r.source_value,
                "target_value": r.target_value,
                "delta": r.delta,
                "status": r.status,
                "detail": r.detail,
            }
        )
    return pd.DataFrame(records)


def generate_csv_report(
    results: List[ValidationResult],
    output_path: Path,
) -> Path:
    """
    Write validation results to a CSV file.

    Args:
        results: List of validation results.
        output_path: Path for the output CSV file.

    Returns:
        Path to the generated CSV file.
    """
    df = results_to_dataframe(results)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    return output_path


def _escape(text: str) -> str:
    """HTML-escape a string for safe rendering."""
    return html.escape(str(text))


def _status_badge(status: str) -> str:
    """Render a status as a colored badge."""
    colors = {
        "PASS": "#28a745",
        "FAIL": "#dc3545",
        "SKIPPED": "#6c757d",
    }
    bg = colors.get(status, "#6c757d")
    return (
        f'<span style="background:{bg};color:#fff;padding:2px 8px;'
        f'border-radius:4px;font-weight:bold;font-size:0.85em;">'
        f"{_escape(status)}</span>"
    )


def generate_html_report(
    results: List[ValidationResult],
    output_path: Path,
    report_title: str = "Data Validation Report: SAS Source vs. Snowflake Target",
) -> Path:
    """
    Generate a styled HTML validation report.

    Includes:
    - Header with title and timestamp
    - Summary statistics (total, passed, failed, skipped)
    - Per-table result tables with color-coded status
    - Footer

    Args:
        results: List of validation results.
        output_path: Path for the output HTML file.
        report_title: Title for the report header.

    Returns:
        Path to the generated HTML file.
    """
    df = results_to_dataframe(results)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    total = len(df)
    passed = int((df["status"] == "PASS").sum()) if total > 0 else 0
    failed = int((df["status"] == "FAIL").sum()) if total > 0 else 0
    skipped = int((df["status"] == "SKIPPED").sum()) if total > 0 else 0
    tables = sorted(df["table"].unique().tolist()) if total > 0 else []

    html_parts: List[str] = []

    # --- HTML head and styles ---
    html_parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_escape(report_title)}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    margin: 0; padding: 20px 40px;
    background: #f8f9fa; color: #212529;
  }}
  h1 {{ color: #0d6efd; border-bottom: 2px solid #0d6efd; padding-bottom: 10px; }}
  h2 {{ color: #495057; margin-top: 30px; }}
  .summary-grid {{
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 16px; margin: 20px 0;
  }}
  .summary-card {{
    background: #fff; border-radius: 8px; padding: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.12); text-align: center;
  }}
  .summary-card .number {{ font-size: 2em; font-weight: bold; }}
  .summary-card .label {{ font-size: 0.9em; color: #6c757d; margin-top: 4px; }}
  .card-total .number {{ color: #0d6efd; }}
  .card-pass .number {{ color: #28a745; }}
  .card-fail .number {{ color: #dc3545; }}
  .card-skip .number {{ color: #6c757d; }}
  table {{
    width: 100%; border-collapse: collapse; margin: 16px 0;
    background: #fff; border-radius: 8px; overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.12);
  }}
  th {{
    background: #343a40; color: #fff; padding: 10px 14px;
    text-align: left; font-size: 0.9em;
  }}
  td {{ padding: 8px 14px; border-bottom: 1px solid #dee2e6; font-size: 0.9em; }}
  tr:nth-child(even) {{ background: #f8f9fa; }}
  tr:hover {{ background: #e9ecef; }}
  .footer {{
    margin-top: 40px; padding-top: 16px; border-top: 1px solid #dee2e6;
    color: #6c757d; font-size: 0.85em;
  }}
</style>
</head>
<body>
""")

    # --- Header ---
    html_parts.append(f"<h1>{_escape(report_title)}</h1>")
    html_parts.append(f"<p>Generated: <strong>{timestamp}</strong></p>")

    # --- Summary cards ---
    html_parts.append('<div class="summary-grid">')
    html_parts.append(
        f'<div class="summary-card card-total">'
        f'<div class="number">{total}</div><div class="label">Total Checks</div></div>'
    )
    html_parts.append(
        f'<div class="summary-card card-pass">'
        f'<div class="number">{passed}</div><div class="label">Passed</div></div>'
    )
    html_parts.append(
        f'<div class="summary-card card-fail">'
        f'<div class="number">{failed}</div><div class="label">Failed</div></div>'
    )
    html_parts.append(
        f'<div class="summary-card card-skip">'
        f'<div class="number">{skipped}</div><div class="label">Skipped</div></div>'
    )
    html_parts.append("</div>")

    # --- Per-table results ---
    for table_name in tables:
        table_df = df[df["table"] == table_name]
        table_passed = int((table_df["status"] == "PASS").sum())
        table_total = len(table_df)

        html_parts.append(
            f"<h2>{_escape(table_name)} "
            f"({table_passed}/{table_total} passed)</h2>"
        )
        html_parts.append("<table>")
        html_parts.append(
            "<thead><tr>"
            "<th>#</th>"
            "<th>Rule</th>"
            "<th>Column</th>"
            "<th>Source Value</th>"
            "<th>Target Value</th>"
            "<th>Delta</th>"
            "<th>Status</th>"
            "<th>Detail</th>"
            "</tr></thead>"
        )
        html_parts.append("<tbody>")

        for _, row in table_df.iterrows():
            html_parts.append(
                f"<tr>"
                f"<td>{_escape(str(row['rule_id']))}</td>"
                f"<td>{_escape(str(row['rule_display_name']))}</td>"
                f"<td>{_escape(str(row['column']))}</td>"
                f"<td>{_escape(str(row['source_value']))}</td>"
                f"<td>{_escape(str(row['target_value']))}</td>"
                f"<td>{_escape(str(row['delta']))}</td>"
                f"<td>{_status_badge(str(row['status']))}</td>"
                f"<td>{_escape(str(row['detail']))}</td>"
                f"</tr>"
            )

        html_parts.append("</tbody></table>")

    # --- Footer ---
    html_parts.append(
        f'<div class="footer">'
        f"Validation Framework v1.0.0 | "
        f"Tables validated: {len(tables)} | "
        f"Report generated: {timestamp}"
        f"</div>"
    )

    html_parts.append("</body></html>")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(html_parts), encoding="utf-8")
    return output_path
