#!/usr/bin/env python3
"""CLI reconciliation harness for SAS-to-Snowflake migration validation.

Loads SAS .sas7bdat source datasets and Snowflake CSV exports, runs the
configured validation rules, and exits non-zero on any FAIL. Output is a
human-readable report suitable for PR inclusion.

This module is self-contained — it does not import the Streamlit-dependent
helper_functions or the LLM agents, so it runs without optional dependencies.

Usage:
    python verify/reconcile.py --table MONTHLY_AMB --scenario Scenario1
    python verify/reconcile.py --scenario Scenario1          # all tables
    python verify/reconcile.py --scenario Scenario1 --quick  # row counts only
"""
import argparse
import os
import sys
from typing import Tuple

import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_DIR = os.path.join(REPO_ROOT, "sample_data")
CONFIG_DIR = os.path.join(REPO_ROOT, "config")
VALIDATIONS_CSV = os.path.join(CONFIG_DIR, "validations_list.csv")

KNOWN_TABLES = ["CUST_ACCOUNTS", "DAILY_BALANCE", "MONTHLY_AMB"]

# Golden-baseline mode (--sas-golden DIR): tables are addressed as LIB.TABLE
# (e.g. CURATED.TXN_ANOMALIES). The SAS side is DIR/<lib>__<table>.csv as
# written by the estate's docker/sas/export_golden.sas; the Snowflake side is
# sample_data/<scenario>/<LIB>__<TABLE>.csv. Column names are compared
# case-insensitively because PROC EXPORT and Snowflake both upper-case them.
SCENARIO_VALIDATIONS_FILE = "validations.csv"


# ── helpers (inlined to avoid importing the Streamlit/LLM chain) ─────────

def decode_value(x):
    if isinstance(x, (bytes, bytearray)):
        return x.decode("utf-8", errors="ignore").strip()
    if isinstance(x, (list, tuple)) or (
        hasattr(x, "__iter__") and not isinstance(x, str)
    ):
        try:
            return bytes(x).decode("utf-8", errors="ignore").strip()
        except Exception:
            return x
    return x


def _filtered(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """Apply a business-rule predicate (pandas ``query`` syntax) to *df*."""
    if not code or code == "NA":
        return df
    return df.query(code, engine="python")


def _run_validations(
    validations_list: list,
    sas_df: pd.DataFrame,
    sas_table_name: str,
    sf_df: pd.DataFrame,
    sf_table_name: str,
) -> Tuple[bool, pd.DataFrame]:
    """Execute validation rules and return (any_failures, results_df)."""
    any_failures = False
    results: list[dict] = []

    for val in validations_list:
        rule = val["rule"]
        code = val.get("code", "NA")

        if rule == "Filtered Count":
            # Business-rule check: number of rows satisfying ``code`` on both sides.
            fc_sas, fc_sf = len(_filtered(sas_df, code)), len(_filtered(sf_df, code))
            status = "PASS" if fc_sas == fc_sf else "FAIL"
            results.append({
                "Test": f"Filtered Count ({code})",
                "SAS Dataset": sas_table_name,
                "SF Table": sf_table_name,
                "SAS Column": "NA",
                "SF Column": "NA",
                "SAS Value": fc_sas,
                "SF Value": fc_sf,
                "Status": status,
            })
            continue

        if rule == "Filtered Sum":
            # Business-rule check: sum of ``column`` over rows satisfying ``code``.
            col = val["column"]
            if col in sas_df.columns and col in sf_df.columns:
                fs_sas = _filtered(sas_df, code)[col].astype(float).sum()
                fs_sf = _filtered(sf_df, code)[col].astype(float).sum()
                status = "PASS" if abs(fs_sas - fs_sf) < 0.01 else "FAIL"
                sas_val, sf_val = round(fs_sas, 2), round(fs_sf, 2)
            else:
                status, sas_val, sf_val = "SKIP (column missing)", 0, 0
            results.append({
                "Test": f"Filtered Sum ({code})",
                "SAS Dataset": sas_table_name,
                "SF Table": sf_table_name,
                "SAS Column": col,
                "SF Column": col,
                "SAS Value": sas_val,
                "SF Value": sf_val,
                "Status": status,
            })
            continue

        if rule == "Row Count":
            rc_sas, rc_sf = len(sas_df), len(sf_df)
            status = "PASS" if rc_sas == rc_sf else "FAIL"
            results.append({
                "Test": "Row Count",
                "SAS Dataset": sas_table_name,
                "SF Table": sf_table_name,
                "SAS Column": "NA",
                "SF Column": "NA",
                "SAS Value": rc_sas,
                "SF Value": rc_sf,
                "Status": status,
            })

        elif rule == "Sum Amount":
            col = val["column"]
            if col in sas_df.columns and col in sf_df.columns:
                sa_sas = sas_df[col].astype(float).sum()
                sa_sf = sf_df[col].astype(float).sum()
                status = "PASS" if abs(sa_sas - sa_sf) < 0.01 else "FAIL"
                results.append({
                    "Test": "Sum Amount",
                    "SAS Dataset": sas_table_name,
                    "SF Table": sf_table_name,
                    "SAS Column": col,
                    "SF Column": col,
                    "SAS Value": round(sa_sas, 2),
                    "SF Value": round(sa_sf, 2),
                    "Status": status,
                })
            else:
                results.append({
                    "Test": "Sum Amount",
                    "SAS Dataset": sas_table_name,
                    "SF Table": sf_table_name,
                    "SAS Column": col,
                    "SF Column": col,
                    "SAS Value": 0,
                    "SF Value": 0,
                    "Status": "SKIP (column missing)",
                })

        elif rule == "Distinct Count":
            col = val["column"]
            if col in sas_df.columns and col in sf_df.columns:
                dc_sas = sas_df[col].nunique()
                dc_sf = sf_df[col].nunique()
                status = "PASS" if dc_sas == dc_sf else "FAIL"
                results.append({
                    "Test": "Distinct Count",
                    "SAS Dataset": sas_table_name,
                    "SF Table": sf_table_name,
                    "SAS Column": col,
                    "SF Column": col,
                    "SAS Value": dc_sas,
                    "SF Value": dc_sf,
                    "Status": status,
                })
            else:
                results.append({
                    "Test": "Distinct Count",
                    "SAS Dataset": sas_table_name,
                    "SF Table": sf_table_name,
                    "SAS Column": col,
                    "SF Column": col,
                    "SAS Value": 0,
                    "SF Value": 0,
                    "Status": "SKIP (column missing)",
                })

        elif rule == "Not Null":
            col = val["column"]
            if col in sas_df.columns and col in sf_df.columns:
                nn_sas = int(sas_df[col].isna().sum())
                nn_sf = int(sf_df[col].isna().sum())
                status = "PASS" if nn_sas == nn_sf == 0 else "FAIL"
                results.append({
                    "Test": "Not Null",
                    "SAS Dataset": sas_table_name,
                    "SF Table": sf_table_name,
                    "SAS Column": col,
                    "SF Column": col,
                    "SAS Value": nn_sas,
                    "SF Value": nn_sf,
                    "Status": status,
                })
            else:
                results.append({
                    "Test": "Not Null",
                    "SAS Dataset": sas_table_name,
                    "SF Table": sf_table_name,
                    "SAS Column": col,
                    "SF Column": col,
                    "SAS Value": 0,
                    "SF Value": 0,
                    "Status": "SKIP (column missing)",
                })

        elif rule == "Uniqueness":
            col = val["column"]
            if col in sas_df.columns and col in sf_df.columns:
                uq_sas = sas_df[col].is_unique
                uq_sf = sf_df[col].is_unique
                status = "PASS" if uq_sas and uq_sf else "FAIL"
                results.append({
                    "Test": "Uniqueness",
                    "SAS Dataset": sas_table_name,
                    "SF Table": sf_table_name,
                    "SAS Column": col,
                    "SF Column": col,
                    "SAS Value": uq_sas,
                    "SF Value": uq_sf,
                    "Status": status,
                })
            else:
                results.append({
                    "Test": "Uniqueness",
                    "SAS Dataset": sas_table_name,
                    "SF Table": sf_table_name,
                    "SAS Column": col,
                    "SF Column": col,
                    "SAS Value": 0,
                    "SF Value": 0,
                    "Status": "SKIP (column missing)",
                })

    results_df = pd.DataFrame(results)
    if "Status" in results_df.columns:
        any_failures = (results_df["Status"] == "FAIL").any()

    return any_failures, results_df


# ── loaders ──────────────────────────────────────────────────────────────

def _golden_name(table: str) -> str:
    return table.replace(".", "__")


def load_sas_source(table: str, golden_dir: str | None = None) -> pd.DataFrame:
    """Load the SAS baseline for *table* (.sas7bdat, CSV, or golden export)."""
    if golden_dir:
        golden_path = os.path.join(golden_dir, f"{_golden_name(table).lower()}.csv")
        if not os.path.exists(golden_path):
            raise FileNotFoundError(f"SAS golden export not found: {golden_path}")
        df = pd.read_csv(golden_path)
        df.columns = [c.upper() for c in df.columns]
        return df
    sas_path = os.path.join(SAMPLE_DIR, f"{table}.sas7bdat")
    if os.path.exists(sas_path):
        df = pd.read_sas(sas_path, format="sas7bdat")
        df = df.map(decode_value)
        return df
    csv_fallback = os.path.join(SAMPLE_DIR, f"{table}.csv")
    if os.path.exists(csv_fallback):
        return pd.read_csv(csv_fallback)
    raise FileNotFoundError(f"SAS source not found: {sas_path}")


def target_path(table: str, scenario: str) -> str:
    return os.path.join(SAMPLE_DIR, scenario, f"{_golden_name(table)}.csv")


def load_snowflake_target(
    table: str, scenario: str, golden_mode: bool = False
) -> pd.DataFrame:
    """Load the Snowflake CSV export for *table* in *scenario*."""
    path = target_path(table, scenario)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Snowflake target not found: {path}")
    df = pd.read_csv(path)
    if golden_mode:
        df.columns = [c.upper() for c in df.columns]
    return df


def validations_path(scenario: str | None = None) -> str:
    """Scenario-local rules (sample_data/<scenario>/validations.csv) win over
    the shared config so parallel conversions never edit the same file."""
    if scenario:
        local = os.path.join(SAMPLE_DIR, scenario, SCENARIO_VALIDATIONS_FILE)
        if os.path.exists(local):
            return local
    return VALIDATIONS_CSV


def scenario_tables(scenario: str) -> list:
    """Tables named in the scenario-local validations file (in file order)."""
    path = validations_path(scenario)
    if path == VALIDATIONS_CSV:
        return []
    df = pd.read_csv(path, keep_default_na=False)
    return list(dict.fromkeys(df["table"].tolist()))


def load_validations(
    table: str, quick: bool = False, scenario: str | None = None
) -> list:
    """Return the validation rules for *table* from the CSV config."""
    path = validations_path(scenario)
    if not os.path.exists(path):
        return [{"rule": "Row Count", "column": "NA"}]

    df = pd.read_csv(path, keep_default_na=False)
    df = df[df["table"] == table]

    if quick:
        df = df[df["rule"] == "Row Count"]

    rules: list[dict] = []
    for _, row in df.iterrows():
        entry: dict = {
            "rule": row["rule"],
            "column": row.get("column", "NA"),
            "code": row.get("code", "NA") or "NA",
        }
        rules.append(entry)

    if not rules:
        rules.append({"rule": "Row Count", "column": "NA"})
    return rules


# ── reporting ────────────────────────────────────────────────────────────

def print_report(
    table: str, scenario: str, results_df: pd.DataFrame, any_failures: bool
) -> None:
    status_label = "FAIL" if any_failures else "PASS"
    print(f"\n{'=' * 72}")
    print(f"  Reconciliation Report: {table}  ({scenario})")
    print(f"  Overall: {status_label}")
    print(f"{'=' * 72}")

    if results_df.empty:
        print("  (no validation rules configured)")
        return

    for _, row in results_df.iterrows():
        test = row.get("Test", "?")
        status = row.get("Status", "?")
        sas_val = row.get("SAS Value", "")
        sf_val = row.get("SF Value", "")
        col = row.get("SAS Column", "")
        marker = "PASS" if status == "PASS" else f"FAIL <<<" if status == "FAIL" else status
        col_label = f" [{col}]" if col and col != "NA" else ""
        print(f"  {test}{col_label}: SAS={sas_val}  SF={sf_val}  -> {marker}")

    print(f"{'=' * 72}\n")


# ── main ─────────────────────────────────────────────────────────────────

def reconcile_table(
    table: str,
    scenario: str,
    quick: bool = False,
    golden_dir: str | None = None,
    report_rows: list | None = None,
) -> bool:
    """Run reconciliation for one table. Returns True if all checks pass."""
    sas_df = load_sas_source(table, golden_dir=golden_dir)
    sf_df = load_snowflake_target(table, scenario, golden_mode=bool(golden_dir))
    rules = load_validations(table, quick=quick, scenario=scenario)

    any_failures, results_df = _run_validations(
        rules, sas_df, table, sf_df, table
    )

    print_report(table, scenario, results_df, any_failures)
    if report_rows is not None:
        for _, row in results_df.iterrows():
            report_rows.append(row.to_dict())
    return not any_failures


def write_markdown_report(path: str, scenario: str, rows: list, all_pass: bool) -> None:
    """Write the reconciliation results as a Markdown table (for PR bodies)."""
    lines = [
        f"# Reconciliation report: {scenario}",
        "",
        f"Overall: **{'PASS' if all_pass else 'FAIL'}**",
        "",
        "| Table | Test | Column | SAS | Snowflake | Status |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('SF Table', '')} | {r.get('Test', '')} | {r.get('SAS Column', '')} "
            f"| {r.get('SAS Value', '')} | {r.get('SF Value', '')} | {r.get('Status', '')} |"
        )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SAS-to-Snowflake migration reconciliation harness",
    )
    parser.add_argument(
        "--table", default=None,
        help="Table to validate (e.g. MONTHLY_AMB). Omit for all tables.",
    )
    parser.add_argument(
        "--scenario", required=True,
        help="Scenario directory (e.g. Scenario1, Scenario2)",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Quick mode: row counts only",
    )
    parser.add_argument(
        "--sas-golden", default=None, metavar="DIR",
        help="Directory of SAS golden CSV exports (<lib>__<table>.csv). "
             "Tables are then addressed as LIB.TABLE.",
    )
    parser.add_argument(
        "--report", default=None, metavar="PATH",
        help="Also write the results as a Markdown table to PATH",
    )
    args = parser.parse_args()

    if args.table:
        tables = [args.table]
    else:
        tables = scenario_tables(args.scenario) or KNOWN_TABLES
    all_pass = True
    report_rows: list = []

    for tbl in tables:
        if not os.path.exists(target_path(tbl, args.scenario)):
            print(f"  SKIP {tbl}: no CSV in {args.scenario}/")
            continue
        passed = reconcile_table(
            tbl, args.scenario, quick=args.quick,
            golden_dir=args.sas_golden, report_rows=report_rows,
        )
        if not passed:
            all_pass = False

    if args.report:
        write_markdown_report(args.report, args.scenario, report_rows, all_pass)
        print(f"Report written to {args.report}")

    print()
    if all_pass:
        print("All reconciliation checks PASSED.")
        sys.exit(0)
    else:
        print("One or more reconciliation checks FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    main()
