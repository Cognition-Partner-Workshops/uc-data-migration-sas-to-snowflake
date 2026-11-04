"""Render the monthly customer balance summary HTML report.

The same summary and rendering code runs for both sources; only the loaders
differ. ``legacy`` reads the frozen SAS run in ``sample_data/``; ``snowflake``
reads the converted JOB03 output and the account table it was computed from.
"""

import argparse
import calendar
import html
import json
import os
import re
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
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
    number = float(value)
    if not number.is_integer():
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
    payload = json.dumps(value, sort_keys=True).replace("</", r"<\/")
    return f'<script type="application/json" id="{element_id}">{payload}</script>'


def render(summary: dict, source: str, source_detail: str) -> str:
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
    label = "Legacy SAS baseline" if source == "legacy" else "Snowflake"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Monthly Customer Balance Summary - {html.escape(month)} - {label}</title>
<style>{STYLE}</style></head>
<body data-default-customer="{default_customer}">
<header class="{source}"><h1>Monthly Customer Balance Summary &mdash; {html.escape(month)}</h1>
<p>Source: <b>{label}</b> ({html.escape(source_detail)}) &middot; Run date:
{html.escape(", ".join(summary["run_dates"]))} &middot; Balances are average daily balances (AMB)</p>
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


def connect():
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(os.environ["SFNFR_PRIVATE_KEY"].encode(), password=None)
    return snowflake.connector.connect(
        account=os.environ["SFNFR_ACCOUNT"], user=os.environ["SFNFR_USER"],
        role=os.environ["SFNFR_ROLE"], authenticator="SNOWFLAKE_JWT",
        private_key=key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()),
        warehouse=os.environ.get("SFNFR_WAREHOUSE", "WH_SAS_AMB"), paramstyle="qmark",
        session_parameters={"QUERY_TAG": "sas_monthly_balance_summary_report"},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=["legacy", "snowflake"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "sample_data")
    parser.add_argument("--amb-table",
                        default="SFNFR_20261104.SAS_AMB_SF_SFNFR_20261104.MONTHLY_AMB")
    parser.add_argument("--accounts-table",
                        default="SFNFR_20261104.SAS_AMB_SRC_SFNFR_20261104.CUST_ACCOUNTS")
    args = parser.parse_args()
    if args.source == "legacy":
        frame = load_legacy(args.data_dir)
        detail = f"{args.data_dir.name}/MONTHLY_AMB.sas7bdat"
    else:
        with connect() as connection, connection.cursor() as cursor:
            frame = load_snowflake(cursor, args.amb_table, args.accounts_table)
        detail = args.amb_table
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(summarise(frame), args.source, detail), encoding="utf-8")
    print(f"Wrote {args.out} ({len(frame)} accounts)")


if __name__ == "__main__":
    main()
