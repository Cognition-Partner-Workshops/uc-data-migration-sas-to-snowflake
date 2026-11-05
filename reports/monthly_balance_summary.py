"""Render the monthly customer balance summary HTML report.

The same summary and rendering code runs for both sources; only the loaders
differ. ``legacy`` reads the frozen SAS run in ``sample_data/``; ``snowflake``
reads the converted JOB03 output and the account table it was computed from.
"""

import argparse
import calendar
import hashlib
import html
import json
import re
import sys
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from snowflake_sql.dual_run import monthly_amb as harness

INPUT_DIGEST = "01668cf1d045d69aca40a898e3b542ed2a70e20a23e0c28ec5d1f1e9d627b730"
AMB_COLUMNS = ["customer_id", "account_id", "reporting_month_yyyymm",
               "average_monthly_balance", "date_computed"]
ACCOUNT_COLUMNS = ["customer_id", "account_id", "account_type", "is_active"]
KEYS = ["customer_id", "account_id"]
TOP_N = 10
UNKNOWN = "Unknown"
FQN = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){2}")


def _text(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _integer(value) -> int:
    try:
        number = Decimal(value.decode("utf-8") if isinstance(value, bytes) else str(value))
    except InvalidOperation as error:
        raise ValueError(f"Expected an integer identifier, got {value!r}") from error
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"Expected an integer identifier, got {value!r}")
    return int(number)


def _date(value) -> date:
    stamp = pd.Timestamp(value)
    if stamp != stamp.normalize():
        raise ValueError(f"Expected a date, got {value!r}")
    return stamp.date()


def prepare(amb: pd.DataFrame, accounts: pd.DataFrame) -> pd.DataFrame:
    """Type the raw tables identically for every source and attach account attributes."""
    amb = amb[AMB_COLUMNS].copy()
    accounts = accounts[ACCOUNT_COLUMNS].copy()
    for frame in (amb, accounts):
        frame["customer_id"] = frame["customer_id"].map(_integer)
        frame["account_id"] = frame["account_id"].map(_text)
    for column in ("account_type", "is_active"):
        accounts[column] = accounts[column].map(_text)
    amb["reporting_month_yyyymm"] = amb["reporting_month_yyyymm"].map(_integer)
    amb["average_monthly_balance"] = amb["average_monthly_balance"].astype(float)
    amb["date_computed"] = amb["date_computed"].map(_date)
    frame = amb.merge(accounts, on=KEYS, how="left", validate="many_to_one")
    frame[["account_type", "is_active"]] = frame[["account_type", "is_active"]].fillna(UNKNOWN)
    return frame.sort_values(KEYS, kind="stable").reset_index(drop=True)


def load_legacy(data_dir: Path) -> pd.DataFrame:
    def read(table):
        return pd.read_sas(data_dir / f"{table}.sas7bdat", format="sas7bdat", encoding=None)

    return prepare(read("MONTHLY_AMB"), read("CUST_ACCOUNTS"))


def load_snowflake(cursor, amb_table: str, accounts_table: str) -> pd.DataFrame:
    def read(table, columns):
        if not FQN.fullmatch(table):
            raise ValueError(f"Expected DATABASE.SCHEMA.TABLE, got {table!r}")
        select = ", ".join(column.upper() for column in columns)
        rows = cursor.execute(f"SELECT {select} FROM IDENTIFIER(?)", (table,)).fetchall()
        return pd.DataFrame(rows, columns=columns)

    return prepare(read(amb_table, AMB_COLUMNS), read(accounts_table, ACCOUNT_COLUMNS))


def _month_label(yyyymm: int) -> str:
    return f"{calendar.month_name[yyyymm % 100]} {yyyymm // 100}"


def _breakdown(frame: pd.DataFrame, column: str):
    grouped = frame.groupby(column, sort=True)["average_monthly_balance"]
    return [{"label": label, "accounts": int(count), "total": float(total), "average": float(mean)}
            for label, count, total, mean in zip(grouped.size().index, grouped.size(),
                                                 grouped.sum(), grouped.mean())]


def summarise(frame: pd.DataFrame) -> dict:
    balances = frame["average_monthly_balance"]
    customers = (frame.groupby("customer_id", sort=True)
                 .agg(accounts=("account_id", "size"), total=("average_monthly_balance", "sum"))
                 .reset_index()
                 .sort_values(["total", "customer_id"], ascending=[False, True], kind="stable"))
    customers["rank"] = range(1, len(customers) + 1)
    details = {
        str(customer_id): [
            {"account_id": row.account_id, "account_type": row.account_type,
             "is_active": row.is_active, "balance": float(row.average_monthly_balance)}
            for row in group.itertuples(index=False)
        ]
        for customer_id, group in frame.groupby("customer_id", sort=True)
    }
    ranks = {str(row.customer_id): {"rank": int(row.rank), "total": float(row.total)}
             for row in customers.itertuples(index=False)}
    return {
        "months": [int(month) for month in sorted(frame["reporting_month_yyyymm"].unique())],
        "run_dates": [str(value) for value in sorted(frame["date_computed"].unique())],
        "totals": {
            "accounts": len(frame),
            "customers": int(frame["customer_id"].nunique()),
            "total": float(balances.sum()),
            "average_account": float(balances.mean()) if len(frame) else 0.0,
            "average_customer": float(customers["total"].mean()) if len(customers) else 0.0,
        },
        "by_type": _breakdown(frame, "account_type"),
        "by_status": _breakdown(frame, "is_active"),
        "top": [{"rank": int(row.rank), "customer_id": int(row.customer_id),
                 "accounts": int(row.accounts), "total": float(row.total)}
                for row in customers.head(TOP_N).itertuples(index=False)],
        "details": details,
        "ranks": ranks,
    }


def money(value: float) -> str:
    return f"{value:,.2f}"


def _table(headers, rows, numeric_from: int) -> str:
    head = "".join(f'<th class="{"num" if i >= numeric_from else ""}">{html.escape(h)}</th>'
                   for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f'<td class="{"num" if i >= numeric_from else ""}">{cell}</td>'
                         for i, cell in enumerate(row)) + "</tr>"
        for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _breakdown_table(title: str, rows) -> str:
    return _table([title, "Accounts", "Total balance", "Average balance"],
                  [[html.escape(r["label"]), f'{r["accounts"]:,}', money(r["total"]),
                    money(r["average"])] for r in rows], 1)


STYLE = """
body{font-family:Segoe UI,Arial,sans-serif;margin:0;color:#1f2933;background:#f5f7fa}
header{padding:18px 28px;color:#fff}
header.legacy{background:#5b3f8c} header.snowflake{background:#1170b5}
header h1{margin:0;font-size:22px} header p{margin:4px 0 0;font-size:13px;opacity:.9}
main{padding:12px 28px 28px} section{background:#fff;border:1px solid #d9e2ec;
border-radius:6px;padding:14px 18px;margin-top:14px} h2{font-size:16px;margin:0 0 10px}
.kpis{display:flex;gap:12px;flex-wrap:wrap}.kpi{flex:1;min-width:130px;background:#f0f4f8;
border-radius:6px;padding:10px 12px}.kpi span{display:block;font-size:12px;color:#52606d}
.kpi b{font-size:18px} table{border-collapse:collapse;width:100%;font-size:13px;margin-top:6px}
th,td{padding:5px 8px;border-bottom:1px solid #e4e7eb;text-align:left}th{background:#f0f4f8}
.num{text-align:right;font-variant-numeric:tabular-nums}.grid{display:flex;gap:18px;flex-wrap:wrap}
.grid>div{flex:1;min-width:300px} a{color:#1170b5} form{margin-bottom:8px;font-size:13px}
input{width:90px;padding:3px} tfoot td{font-weight:600}
"""

SCRIPT = r"""
const data = JSON.parse(document.getElementById('details').textContent);
const ranks = JSON.parse(document.getElementById('ranks').textContent);
const fmt = v => v.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function show(id) {
  const out = document.getElementById('detail');
  document.getElementById('customer').value = id;
  const rows = data[id];
  if (!rows) { out.innerHTML = '<p>Customer ' + esc(id) + ' is not in this report.</p>'; return; }
  const body = rows.map(r => '<tr><td>' + esc(r.account_id) + '</td><td>' + esc(r.account_type) +
    '</td><td>' + esc(r.is_active) + '</td><td class="num">' + fmt(r.balance) + '</td></tr>').join('');
  out.innerHTML = '<p>Customer <b>' + esc(id) + '</b> &middot; rank ' + ranks[id].rank + ' of ' +
    Object.keys(ranks).length + ' &middot; ' + rows.length + ' account(s)</p>' +
    '<table><thead><tr><th>Account</th><th>Type</th><th>Status</th>' +
    '<th class="num">Average monthly balance</th></tr></thead><tbody>' + body +
    '</tbody><tfoot><tr><td colspan="3">Customer total</td><td class="num">' + fmt(ranks[id].total) +
    '</td></tr></tfoot></table>';
}
function fromHash() {
  const m = location.hash.match(/customer=(\d+)/);
  show(m ? m[1] : document.body.dataset.defaultCustomer);
}
document.getElementById('lookup').addEventListener('submit', e => {
  e.preventDefault(); location.hash = 'customer=' + document.getElementById('customer').value.trim();
});
window.addEventListener('hashchange', fromHash);
fromHash();
"""


def _json_script(element_id: str, value) -> str:
    payload = (json.dumps(value, sort_keys=True).replace("<", r"\u003c")
               .replace(">", r"\u003e").replace("&", r"\u0026"))
    return f'<script type="application/json" id="{element_id}">{payload}</script>'


def render(summary: dict, source: str, source_detail: str, *, commit="unknown",
           digest=INPUT_DIGEST, reporting_month=202507, run_date=date(2025, 8, 1)) -> str:
    month = ", ".join(_month_label(m) for m in summary["months"]) or "No data"
    totals = summary["totals"]
    kpis = [("Customers", f'{totals["customers"]:,}'), ("Accounts", f'{totals["accounts"]:,}'),
            ("Total balance", money(totals["total"])),
            ("Average per account", money(totals["average_account"])),
            ("Average per customer", money(totals["average_customer"]))]
    kpi_html = "".join(f"<div class=\"kpi\"><span>{label}</span><b>{value}</b></div>"
                       for label, value in kpis)
    top_rows = [[str(r["rank"]),
                 f'<a href="#customer={r["customer_id"]}">{r["customer_id"]}</a>',
                 str(r["accounts"]), money(r["total"])] for r in summary["top"]]
    default_customer = summary["top"][0]["customer_id"] if summary["top"] else ""
    label = "EMULATED Legacy SAS baseline" if source == "legacy" else "Snowflake"
    disclosure = ("EMULATED report rendering from retained SAS output; no native SAS engine rerun. "
                  "SAS program equivalence is unverified: original SAS source missing; "
                  "embedded snippet differs from the conversion." if source == "legacy" else "")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Monthly Customer Balance Summary - {html.escape(month)} - {label}</title>
<style>{STYLE}</style></head>
<body data-default-customer="{default_customer}">
<header class="{source}"><h1>Monthly Customer Balance Summary &mdash; {html.escape(month)}</h1>
<p>Source: <b>{label}</b> ({html.escape(source_detail)}) &middot; Run date:
{html.escape(", ".join(summary["run_dates"]))} &middot; Balances are average daily balances (AMB)</p>
<p>{disclosure}</p><p>Input digest: {html.escape(digest)} &middot; Reporting month:
{reporting_month} &middot; Run date: {run_date} &middot; Source commit: {html.escape(commit)}</p>
</header><main>
<section><h2>Totals</h2><div class="kpis">{kpi_html}</div>
<div class="grid"><div>{_breakdown_table("Account type", summary["by_type"])}</div>
<div>{_breakdown_table("Account status", summary["by_status"])}</div></div></section>
<section><h2>Top {TOP_N} customer balances</h2>
{_table(["Rank", "Customer", "Accounts", "Total balance"], top_rows, 2)}</section>
<section><h2>Customer detail</h2>
<form id="lookup">Customer ID <input id="customer" inputmode="numeric">
<button type="submit">Show</button></form><div id="detail"></div></section>
</main>
{_json_script("details", summary["details"])}
{_json_script("ranks", summary["ranks"])}
<script>{SCRIPT}</script>
</body></html>
"""


def frozen_input_digest(reporting_month, run_date):
    lines = []
    for table in sorted(harness.TABLES):
        rows, file_sha = harness.read_sas(table)
        size = (ROOT / "sample_data" / f"{table}.sas7bdat").stat().st_size
        lines.append(f"{table}|{file_sha}|{size}|{len(rows)}|{harness.canonical_sha256(rows)}")
    lines.append(f"business|{reporting_month}|{run_date}")
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    if digest != INPUT_DIGEST:
        raise RuntimeError(f"Frozen input digest mismatch: {digest}")
    return digest


def validate_business(frame, reporting_month, run_date):
    if (set(frame.reporting_month_yyyymm) != {reporting_month}
            or set(frame.date_computed) != {run_date}):
        raise RuntimeError("Report data month/run date mismatch (or empty data)")


def outside_repo(path):
    path = path.resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError("Output must be outside the repository")
    return path


def warehouse_snapshot(cursor, suspended=False, *, evidence=None, key="warehouse",
                       timeout=60, interval=2):
    deadline = time.monotonic() + timeout
    while True:
        rows = harness.show_rows(cursor, f"SHOW WAREHOUSES LIKE '{harness.WAREHOUSE}'")
        if evidence is not None:
            evidence[key] = rows
            evidence.setdefault("warehouse_readbacks", []).append({"phase": key, "rows": rows})
        matching = [row for row in rows if str(row.get("name")).upper() == harness.WAREHOUSE]
        if len(matching) != 1:
            raise RuntimeError(f"Warehouse missing: expected one {harness.WAREHOUSE}; observed={rows!r}")
        row = matching[0]
        for condition, observed, expected in (
            ("monitor", row.get("resource_monitor"), harness.MONITOR),
            ("size", row.get("size"), "X-Small"),
            ("auto_suspend", row.get("auto_suspend"), 60),
        ):
            if str(observed).upper() != str(expected).upper():
                raise RuntimeError(f"Warehouse {condition}: expected={expected!r}; observed={observed!r}")
        if not suspended or str(row.get("state")).upper() == "SUSPENDED":
            return row
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"Warehouse state: expected='SUSPENDED'; observed={row.get('state')!r}; "
                               f"timeout={timeout}s")
        time.sleep(min(interval, remaining))


def tasks_snapshot(cursor, database, *, evidence=None, key="tasks"):
    rows = harness.show_rows(cursor, f"SHOW TASKS IN DATABASE {database}")
    if evidence is not None:
        evidence[key] = rows
    if any(str(row["state"]).upper() != "SUSPENDED" for row in rows):
        raise RuntimeError(f"Existing tasks must remain suspended; observed={rows!r}")
    return rows


def run_snowflake(args, commit, digest):
    run_id = harness.sf.make_run_id(harness.WORKLOAD, "dev")
    evidence = {"run_id": run_id, "commit": commit, "warehouse_before": None,
                "warehouse_after": None, "tasks_before": None, "tasks_after": None,
                "queries": [], "errors": []}
    print(f"run_id={run_id}")
    cursor = None
    try:
        params = harness.connection_arguments(args, commit, run_id)
        params["enable_server_session_keep_alive_auto_detection"] = False
        with (harness.snowflake.connector.connect(**params) as connection,
              connection.cursor() as raw):
            cursor = harness.RecordedCursor(raw, args.out.parent)
            verified = False
            try:
                warehouse_snapshot(cursor, evidence=evidence, key="warehouse_before")
                verified = True
                tasks_snapshot(cursor, args.database, evidence=evidence, key="tasks_before")
                cursor.execute(f"USE WAREHOUSE {harness.WAREHOUSE}")
                frame = load_snowflake(cursor, args.amb_table, args.accounts_table)
                write_report(frame, args, commit, digest)
            finally:
                active_error = sys.exc_info()[0] is not None
                cleanup_errors = []
                if verified:
                    try:
                        cursor.execute(f"ALTER WAREHOUSE {harness.WAREHOUSE} SUSPEND")
                    except harness.snowflake.connector.errors.ProgrammingError as error:
                        if error.errno == 90064 or "already suspended" in str(error).lower():
                            evidence["suspend_response"] = str(error)
                        else:
                            cleanup_errors.append(str(error))
                    except harness.snowflake.connector.errors.Error as error:
                        cleanup_errors.append(str(error))
                for operation in (
                    lambda: warehouse_snapshot(cursor, suspended=verified, evidence=evidence,
                                               key="warehouse_after"),
                    lambda: tasks_snapshot(cursor, args.database, evidence=evidence, key="tasks_after"),
                ):
                    try:
                        operation()
                    except (RuntimeError, ValueError, KeyError,
                            harness.snowflake.connector.errors.Error) as error:
                        cleanup_errors.append(str(error))
                evidence["errors"].extend(cleanup_errors)
                if cleanup_errors and not active_error:
                    raise RuntimeError("; ".join(cleanup_errors))
    except Exception as error:
        evidence["errors"].append(str(error))
        raise
    finally:
        if cursor is not None:
            evidence["queries"] = [{**query, "run_id": run_id} for query in cursor.queries]
        harness.write_json(args.out.with_suffix(".evidence.json"), evidence)
        print(json.dumps(evidence, default=str))


def write_report(frame, args, commit, digest):
    validate_business(frame, args.reporting_month, args.run_date)
    summary = summarise(frame)
    detail = ("sample_data/MONTHLY_AMB.sas7bdat" if args.source == "legacy"
              else args.amb_table)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(summary, args.source, detail, commit=commit, digest=digest,
                               reporting_month=args.reporting_month, run_date=args.run_date),
                        encoding="utf-8")
    harness.write_json(args.out.with_suffix(".json"), {
        "summary": summary, "rows": frame.to_dict(orient="records"),
        "commit": commit, "input_digest": digest})
    print(f"Wrote {args.out} ({len(frame)} accounts)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=["legacy", "snowflake"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reporting-month", type=int, default=202507)
    parser.add_argument("--run-date", type=date.fromisoformat, default=date(2025, 8, 1))
    parser.add_argument("--amb-table",
                        default="DB_MIG_SAS_SFEXT_20261105A.MONTHLY_AMB_SF_SFEXT_20261105A.MONTHLY_AMB")
    parser.add_argument("--accounts-table",
                        default="DB_MIG_SAS_SFEXT_20261105A.MONTHLY_AMB_SRC_SFEXT_20261105A.CUST_ACCOUNTS")
    args = parser.parse_args()
    args.out = outside_repo(args.out)
    digest = frozen_input_digest(args.reporting_month, args.run_date)
    commit = harness.pushed_head()
    if args.source == "legacy":
        write_report(load_legacy(ROOT / "sample_data"), args, commit, digest)
    else:
        if not FQN.fullmatch(args.amb_table) or not FQN.fullmatch(args.accounts_table):
            raise ValueError("Expected DATABASE.SCHEMA.TABLE")
        args.database, args.target_schema, _ = args.amb_table.split(".")
        run_snowflake(args, commit, digest)


if __name__ == "__main__":
    main()
