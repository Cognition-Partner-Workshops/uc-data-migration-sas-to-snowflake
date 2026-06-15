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

KNOWN_TABLES = [
    "CUST_ACCOUNTS", "DAILY_BALANCE", "MONTHLY_AMB",
    "CLAIMS_REGISTER", "CLAIMS_REVIEW_QUEUE", "FRAUD_ALERTS",
]


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

def load_sas_source(table: str) -> pd.DataFrame:
    """Load the SAS .sas7bdat baseline for *table*."""
    sas_path = os.path.join(SAMPLE_DIR, f"{table}.sas7bdat")
    if os.path.exists(sas_path):
        df = pd.read_sas(sas_path, format="sas7bdat")
        df = df.map(decode_value)
        return df
    csv_fallback = os.path.join(SAMPLE_DIR, f"{table}.csv")
    if os.path.exists(csv_fallback):
        return pd.read_csv(csv_fallback)
    raise FileNotFoundError(f"SAS source not found: {sas_path}")


def load_snowflake_target(table: str, scenario: str) -> pd.DataFrame:
    """Load the Snowflake CSV export for *table* in *scenario*."""
    path = os.path.join(SAMPLE_DIR, scenario, f"{table}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Snowflake target not found: {path}")
    return pd.read_csv(path)


def load_validations(table: str, quick: bool = False) -> list:
    """Return the validation rules for *table* from the CSV config."""
    if not os.path.exists(VALIDATIONS_CSV):
        return [{"rule": "Row Count", "column": "NA"}]

    df = pd.read_csv(VALIDATIONS_CSV, keep_default_na=False)
    df = df[df["table"] == table]

    if quick:
        df = df[df["rule"] == "Row Count"]

    rules: list[dict] = []
    for _, row in df.iterrows():
        entry: dict = {"rule": row["rule"], "column": row.get("column", "NA")}
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

def reconcile_table(table: str, scenario: str, quick: bool = False) -> bool:
    """Run reconciliation for one table. Returns True if all checks pass."""
    sas_df = load_sas_source(table)
    sf_df = load_snowflake_target(table, scenario)
    rules = load_validations(table, quick=quick)

    any_failures, results_df = _run_validations(
        rules, sas_df, table, sf_df, table
    )

    print_report(table, scenario, results_df, any_failures)
    return not any_failures


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
    args = parser.parse_args()

    tables = [args.table] if args.table else KNOWN_TABLES
    all_pass = True

    for tbl in tables:
        scenario_path = os.path.join(SAMPLE_DIR, args.scenario, f"{tbl}.csv")
        if not os.path.exists(scenario_path):
            print(f"  SKIP {tbl}: no CSV in {args.scenario}/")
            continue
        passed = reconcile_table(tbl, args.scenario, quick=args.quick)
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("All reconciliation checks PASSED.")
        sys.exit(0)
    else:
        print("One or more reconciliation checks FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    main()
